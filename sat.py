"""Images satellite MESURÉES (Meteosat MTG-I, imageur FCI, infrarouge 10,5 µm) → fc1/sat/ pour l'app.

Source : EUMETView (WMS d'EUMETSAT). Politique de données EUMETSAT (janvier 2026) : les images Level 1 « horaires »
(cycle référencé à chaque heure UTC) sont des données « Core », gratuites et sans restriction, licence CC BY 4.0.
On ne prend donc QUE les images des heures pleines, une fois chacune (passage toutes les heures, 12 dernières heures).
Crédit à afficher : « Contient des données EUMETSAT Meteosat modifiées <année> ».

Sortie : une image PNG par heure en projection Web Mercator (EPSG:3857, la même que la carte de l'app), blanche,
dont la transparence suit le froid du sommet des nuages (sol et ciel clair transparents) ; fc1/sat/index.json liste
les heures disponibles. Les images de plus de 26 h sont effacées.
"""
import datetime as dt, io, json, math, os, time, urllib.request
import numpy as np
from PIL import Image

WMS = "https://view.eumetsat.int/geoserver/wms"
LAYER = "mtg_fd:ir105_hrfi"
MASK = "msg_fes:clm"          # masque nuageux (produit dérivé « Core ») : voit aussi stratus et nuages bas
PREFIX = "fc1/sat/"
LON0, LON1, LAT0, LAT1 = -14.0, 30.0, 34.0, 60.0
WIDTH = 2000
HOURS = 12
RENDER = 2                    # version du rendu (changer pour tout recalculer)
UA = {"User-Agent": "Lucarne-previsions (info@photily.fr)"}

def merc(lon, lat):
    r = 6378137.0
    return r * math.radians(lon), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

X0, Y0 = merc(LON0, LAT0)
X1, Y1 = merc(LON1, LAT1)
HEIGHT = int(round(WIDTH * (Y1 - Y0) / (X1 - X0)))

def fetch(t, layer=LAYER):
    u = (f"{WMS}?service=WMS&version=1.3.0&request=GetMap&layers={layer}&styles=&crs=EPSG:3857"
         f"&bbox={X0:.1f},{Y0:.1f},{X1:.1f},{Y1:.1f}&width={WIDTH}&height={HEIGHT}&format=image/png"
         f"&time={t:%Y-%m-%dT%H:%M:%S}.000Z")
    for k in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=90) as r:
                d = r.read()
                if r.headers.get("Content-Type", "").startswith("image/"):
                    return d
                print("Satellite :", t, "réponse inattendue", d[:160]); return None
        except Exception as e:
            print("Satellite :", t, "essai", k + 1, ":", str(e)[:100]); time.sleep(10)
    return None

def to_overlay(png, mask_png=None):
    """Infrarouge (blanc = froid) + masque nuageux → nuages blancs (sommets froids) ou gris clair (nuages bas,
    stratus), sol et ciel clair transparents. Sans le masque, un stratus de novembre (à peine plus froid que le
    sol) restait presque invisible alors que le ciel était bouché."""
    im = Image.open(io.BytesIO(png)).convert("LA")
    a = np.asarray(im, dtype=np.float32)
    lum, valid = a[..., 0], a[..., 1] > 0
    cold = np.clip((lum - 120) / 100, 0, 1)                      # 0 : sommet tiède (bas) ; 1 : sommet froid (haut)
    alpha = np.clip((lum - 95) / 125, 0, 1) ** 0.9
    if mask_png:
        m = np.asarray(Image.open(io.BytesIO(mask_png)).convert("RGB").resize(im.size, Image.BILINEAR), dtype=np.float32)
        cloud = ((m.min(axis=2) - 200) / 45).clip(0, 1)           # blanc dans le masque = nuage
        alpha = np.maximum(alpha, cloud * 0.78)
    alpha = (alpha * 240)
    alpha[~valid] = 0
    out = np.zeros(lum.shape + (4,), np.uint8)
    # Couleur : gris bleuté pour les nuages bas, blanc pour les sommets froids
    out[..., 0] = (200 + 55 * cold).astype(np.uint8)
    out[..., 1] = (206 + 49 * cold).astype(np.uint8)
    out[..., 2] = (218 + 37 * cold).astype(np.uint8)
    out[..., 3] = alpha.astype(np.uint8)
    b = io.BytesIO(); Image.fromarray(out, "RGBA").save(b, "PNG", optimize=True)
    return b.getvalue(), float(np.mean(lum[valid])) if valid.any() else 0

def run_sat(put_files=None):
    from lucarne_forecast import r2_client, upload
    s3, bucket = r2_client()
    try:
        idx = json.loads(s3.get_object(Bucket=bucket, Key=PREFIX + "index.json")["Body"].read())
    except Exception:
        idx = {"hours": []}
    have = set(idx.get("hours", [])) if idx.get("render") == RENDER else set()
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    files = {}
    for k in range(HOURS + 1):
        t = now - dt.timedelta(hours=k)
        key = t.strftime("%Y%m%d%H")
        if key in have:
            continue
        png = fetch(t)
        if not png:
            continue
        ov, mean = to_overlay(png, fetch(t, MASK))
        files[f"{PREFIX}{key}.png"] = ov
        have.add(key)
        print("Satellite :", t.isoformat(), "luminance moyenne", round(mean), "→", len(ov) // 1024, "ko")
        time.sleep(1)
    limit = (now - dt.timedelta(hours=26)).strftime("%Y%m%d%H")
    old = sorted(h for h in have if h < limit)
    keep = sorted(h for h in have if h >= limit)
    for h in old:
        try: s3.delete_object(Bucket=bucket, Key=f"{PREFIX}{h}.png")
        except Exception: pass
    meta = {"source": "EUMETSAT Meteosat MTG-I FCI IR 10,5 µm (EUMETView)", "licence": "CC BY 4.0",
            "credit": f"Contient des données EUMETSAT Meteosat modifiées {now.year}",
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "crs": "EPSG:3857", "render": RENDER, "bbox": [X0, Y0, X1, Y1], "size": [WIDTH, HEIGHT], "hours": keep}
    files[PREFIX + "index.json"] = json.dumps(meta).encode()
    # « latest.json » attendu en dernier par upload() : l'index joue ce rôle
    files[PREFIX + "latest.json"] = files.pop(PREFIX + "index.json")
    files_index = dict(files); files_index[PREFIX + "index.json"] = files[PREFIX + "latest.json"]
    upload(files_index)
    print("Satellite :", len(keep), "heures disponibles,", len(old), "effacées")

if __name__ == "__main__":
    run_sat()
