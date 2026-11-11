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
RENDER = 4                    # version du rendu (changer pour tout recalculer)
UA = {"User-Agent": "Lucarne-previsions (info@photily.fr)"}

def merc(lon, lat):
    r = 6378137.0
    return r * math.radians(lon), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

X0, Y0 = merc(LON0, LAT0)
X1, Y1 = merc(LON1, LAT1)
HEIGHT = int(round(WIDTH * (Y1 - Y0) / (X1 - X0)))

def fetch(t, layer=LAYER, width=WIDTH, tries=3, timeout=90):
    h = int(round(width * HEIGHT / WIDTH))
    u = (f"{WMS}?service=WMS&version=1.3.0&request=GetMap&layers={layer}&styles=&crs=EPSG:3857"
         f"&bbox={X0:.1f},{Y0:.1f},{X1:.1f},{Y1:.1f}&width={width}&height={h}&format=image/png"
         f"&time={t:%Y-%m-%dT%H:%M:%S}.000Z")
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=timeout) as r:
                d = r.read()
                if r.headers.get("Content-Type", "").startswith("image/"):
                    return d
                print("Satellite :", t, "réponse inattendue", d[:160]); return None
        except Exception as e:
            print("Satellite :", t, "essai", k + 1, ":", str(e)[:100]); time.sleep(10)
    return None

def to_overlay(png, mask_png=None):
    """Toutes les couches, avec la même logique que la prévision de l'app (BestSky drawIR) :
    - k : nuages d'altitude, d'après l'infrarouge (sommet froid → blanc, d'autant plus opaque qu'il est froid ;
      un voile de cirrus reste translucide, un cumulonimbus est opaque) ;
    - l : couche basse (stratus, brouillard), d'après le masque nuageux EUMETSAT, en gris bleuté à 75 % ;
    - opacité totale = k + l·(1 − k), couleur mélangée selon la part « haut ».
    Sans le masque, un stratus de novembre (presque à la température du sol) restait invisible."""
    im = Image.open(io.BytesIO(png)).convert("LA")
    a = np.asarray(im, dtype=np.float32)
    lum, valid = a[..., 0], a[..., 1] > 0
    k = np.clip((lum - 110) / 110, 0, 1) ** 1.1                   # nuages d'altitude
    if mask_png:
        m = np.asarray(Image.open(io.BytesIO(mask_png)).convert("RGB").resize(im.size, Image.BILINEAR), dtype=np.float32)
        l = ((m.min(axis=2) - 200) / 45).clip(0, 1) * 0.75        # blanc dans le masque = nuage
    else:
        l = np.zeros_like(k)
    alpha = k + l * (1 - k)
    w = k / np.maximum(alpha, 1e-3)                               # part « haut » dans la couleur
    alpha[~valid] = 0
    out = np.zeros(lum.shape + (4,), np.uint8)
    out[..., 0] = (150 + 105 * w).astype(np.uint8)                # bas (150,170,195) → haut blanc
    out[..., 1] = (170 + 85 * w).astype(np.uint8)
    out[..., 2] = (195 + 60 * w).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
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
    start = time.time()
    for k in range(HOURS + 1):
        t = now - dt.timedelta(hours=k)
        key = t.strftime("%Y%m%d%H")
        if key in have:
            continue
        png = fetch(t)
        if not png:
            continue
        if time.time() - start > 14 * 60:
            print("Satellite : temps limite atteint, la suite au prochain passage"); break
        mask = fetch(t, MASK, width=700, tries=2, timeout=40) if time.time() - start < 10 * 60 else None
        if not mask:        # sans masque, le stratus serait invisible : on retentera au prochain passage
            print("Satellite :", t, "masque nuageux indisponible, heure refaite au prochain passage"); continue
        ov, mean = to_overlay(png, mask)
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
