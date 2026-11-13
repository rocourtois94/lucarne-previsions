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
GEO = "mtg_fd:rgb_geocolour"  # « image satellite » comme MeteoSwiss : couleurs vraies le jour, nuages bas bleutés
                              # et lumières des villes la nuit (composition RGB des images Core, même licence)
CTH = "msg_fes:cth"           # hauteur du sommet des nuages (produit dérivé « Core ») : tout pixel coloré = nuage ;
                              # complète le masque, qui rate parfois le stratus au crépuscule (13/11/2026)
MASK = "msg_fes:clm"          # masque nuageux (produit dérivé « Core ») : voit aussi stratus et nuages bas
PREFIX = "fc1/sat/"
LON0, LON1, LAT0, LAT1 = -14.0, 30.0, 34.0, 60.0
WIDTH = 2000
HOURS = 12
RENDER = 5                    # version du rendu (5 : stratus de nuit d'après GeoColour) (changer pour tout recalculer)
UA = {"User-Agent": "Lucarne-previsions (info@photily.fr)"}

def merc(lon, lat):
    r = 6378137.0
    return r * math.radians(lon), r * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))

X0, Y0 = merc(LON0, LAT0)
X1, Y1 = merc(LON1, LAT1)
HEIGHT = int(round(WIDTH * (Y1 - Y0) / (X1 - X0)))

def fetch(t, layer=LAYER, width=WIDTH, tries=3, timeout=90, fmt="image/png"):
    h = int(round(width * HEIGHT / WIDTH))
    u = (f"{WMS}?service=WMS&version=1.3.0&request=GetMap&layers={layer}&styles=&crs=EPSG:3857"
         f"&bbox={X0:.1f},{Y0:.1f},{X1:.1f},{Y1:.1f}&width={width}&height={h}&format={fmt}"
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

def sun_elevation(lat, lon, t):
    """Hauteur du Soleil (degrés), formule simple (± 1°) ; lat/lon en degrés, tableaux numpy acceptés"""
    d = t.timetuple().tm_yday + (t.hour + t.minute / 60) / 24
    g = 2 * math.pi / 365 * (d - 1)
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    eq = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g) - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    ha = np.radians((t.hour * 60 + t.minute + eq + 4 * lon) / 4 - 180)
    la = np.radians(lat)
    return np.degrees(np.arcsin(np.sin(la) * np.sin(decl) + np.cos(la) * np.cos(decl) * np.cos(ha)))


def geo_low(jpg, t, size):
    """Nuages bas de nuit d'après l'image GeoColour d'EUMETSAT : stratus et brouillard y sont gris-bleu clair
    (méthode des services météo : écart infrarouge 10,8 − 3,9 µm). Le masque nuageux EUMETSAT, lui, rate souvent
    le stratus de nuit : le 13/11/2026 à 20 h, Genève « dégagé » dans le masque, sous une couche de stratus en vrai.
    Seulement là où il fait nuit (Soleil sous −6°) ; 0 ailleurs. Résultat 0…1, à la taille (W, H) demandée."""
    W, H = size
    g = np.asarray(Image.open(io.BytesIO(jpg)).convert("RGB").resize((W, H), Image.BILINEAR), dtype=np.float32)
    r, b = g[..., 0], g[..., 2]
    lum = g.mean(axis=2)
    low = np.clip((b - r - 15) / 25, 0, 1) * np.clip((lum - 70) / 60, 0, 1)
    R = 6378137.0
    xs = X0 + (np.arange(W) + 0.5) / W * (X1 - X0)
    ys = Y1 - (np.arange(H) + 0.5) / H * (Y1 - Y0)
    lon = np.degrees(xs / R)[None, :]
    lat = np.degrees(2 * np.arctan(np.exp(ys / R)) - math.pi / 2)[:, None]
    night = sun_elevation(lat, lon, t) < -6
    return np.where(night, low, 0).astype(np.float32)


def to_overlay(png, mask_png=None, low_extra=None):
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
    if low_extra is not None and low_extra.shape == l.shape:
        l = np.maximum(l, low_extra * 0.75)                     # stratus de nuit vu par GeoColour
    alpha = k + l * (1 - k)
    w = k / np.maximum(alpha, 1e-3)                               # part « haut » dans la couleur
    alpha[~valid] = 0
    out = np.zeros(lum.shape + (4,), np.uint8)
    out[..., 0] = (150 + 105 * w).astype(np.uint8)                # bas (150,170,195) → haut blanc
    out[..., 1] = (170 + 85 * w).astype(np.uint8)
    out[..., 2] = (195 + 60 * w).astype(np.uint8)
    out[..., 3] = (alpha * 255).astype(np.uint8)
    b = io.BytesIO(); Image.fromarray(out, "RGBA").save(b, "PNG", optimize=True)
    return b.getvalue(), float(np.mean(lum[valid])) if valid.any() else 0, (k, l / 0.75, valid)


SATM = "fc1/satm/"            # nuages MESURÉS de la dernière heure, en morceaux « LCFC » (recalage de la prévision)
SATM_RES, SATM_TILE = 0.05, 5

def with_cth(fields, cth_png):
    """Nuage mesuré = masque OU hauteur de sommet connue (pixel non noir de la couche CTH)"""
    if not cth_png:
        return fields
    k, m, valid = fields
    H, W = k.shape
    c = np.asarray(Image.open(io.BytesIO(cth_png)).convert("RGB").resize((W, H), Image.NEAREST), dtype=np.float32)
    seen = (c.max(axis=2) > 40).astype(np.float32)
    return k, np.maximum(m, seen), valid


G_LAT0, G_LAT1, G_LON0, G_LON1 = 30, 60, -15, 30   # grille des nuages mesurés (multiples de 5°, comme l'app)
NOWCAST_HOURS = 3                                   # heures extrapolées après l'observation
SATM_PREV = SATM + "_prev.npz"                      # grille de l'heure précédente (pour le mouvement)


def satm_grid(fields):
    """Grille régulière 0,05° (lignes du sud au nord) : nuage mesuré et part « haute », de 0 à 1 ; NaN hors image"""
    k, m, valid = fields
    H, W = k.shape
    def box(a):                                     # moyenne 3 × 3 (petit lissage avant l'échantillonnage)
        p = np.pad(a, 1, mode="edge"); c = p.cumsum(0).cumsum(1)
        c = np.pad(c, ((1, 0), (1, 0)))
        return (c[3:, 3:] - c[:-3, 3:] - c[3:, :-3] + c[:-3, :-3]) / 9
    mv, kv, vv = box(m * valid), box(k * valid), box(valid.astype(np.float32))
    lats = G_LAT0 + (np.arange(int((G_LAT1 - G_LAT0) / SATM_RES)) + 0.5) * SATM_RES
    lons = G_LON0 + (np.arange(int((G_LON1 - G_LON0) / SATM_RES)) + 0.5) * SATM_RES
    xs = np.array([(merc(x, 0)[0] - X0) / (X1 - X0) * W for x in lons])
    ys = np.array([(Y1 - merc(0, y)[1]) / (Y1 - Y0) * H for y in lats])
    xi = np.clip(xs.astype(int), 0, W - 1); yi = np.clip(ys.astype(int), 0, H - 1)
    inside = (xs >= 0) & (xs < W)
    rows = (ys >= 0) & (ys < H)
    V = vv[np.ix_(yi, xi)]
    ok = (V > 0.5) & rows[:, None] & inside[None, :]
    M = np.where(ok, mv[np.ix_(yi, xi)] / np.maximum(V, 1e-3), np.nan).astype(np.float32)
    K = np.where(ok, kv[np.ix_(yi, xi)] / np.maximum(V, 1e-3), np.nan).astype(np.float32)
    return M, K


def motion(prev, cur, block=20, search=8):
    """Déplacement des nuages en 1 h (cases de 0,05°), par blocs de 1° : comparaison de l'image précédente
    décalée avec l'image actuelle (somme des écarts au carré la plus faible). Blocs sans contraste : ignorés."""
    R, C = cur.shape
    nb_r, nb_c = R // block, C // block
    dy = np.full((nb_r, nb_c), np.nan, np.float32); dx = np.full((nb_r, nb_c), np.nan, np.float32)
    P = np.nan_to_num(prev, nan=0.0); Q = np.nan_to_num(cur, nan=0.0)
    for bi in range(nb_r):
        for bj in range(nb_c):
            r0, c0 = bi * block, bj * block
            q = Q[r0:r0 + block, c0:c0 + block]
            if not (0.1 < q.mean() < 0.9) or q.std() < 0.15 or np.isnan(cur[r0:r0 + block, c0:c0 + block]).any():
                continue
            best, arg = None, None
            for sy in range(-search, search + 1):
                for sx in range(-search, search + 1):
                    a0, b0 = r0 - sy, c0 - sx
                    if a0 < 0 or b0 < 0 or a0 + block > R or b0 + block > C:
                        continue
                    e = float(((P[a0:a0 + block, b0:b0 + block] - q) ** 2).mean())
                    if best is None or e < best:
                        best, arg = e, (sy, sx)
            if arg is not None and best < 0.5 * float(((P[r0:r0 + block, c0:c0 + block] - q) ** 2).mean() + 1e-6) + 0.02:
                dy[bi, bj], dx[bi, bj] = arg
    # Lissage : médiane des voisins, puis valeur de la zone (ou 0) là où rien n'a été mesuré
    def fill(a):
        out = a.copy()
        for i in range(a.shape[0]):
            for j in range(a.shape[1]):
                w = a[max(0, i - 1):i + 2, max(0, j - 1):j + 2]
                w = w[~np.isnan(w)]
                out[i, j] = np.median(w) if w.size >= 2 else (a[i, j] if not np.isnan(a[i, j]) else np.nan)
        g = out[~np.isnan(out)]
        return np.where(np.isnan(out), np.median(g) if g.size >= 10 else 0.0, out)
    return fill(dy), fill(dx), block


def advect(field, dy, dx, block, hours):
    """Le champ déplacé de « hours » fois le mouvement horaire (interpolation au plus proche)"""
    R, C = field.shape
    vy = np.kron(dy, np.ones((block, block)))[:R, :C]; vx = np.kron(dx, np.ones((block, block)))[:R, :C]
    if vy.shape != (R, C):
        vy = np.pad(vy, ((0, R - vy.shape[0]), (0, C - vy.shape[1])), mode="edge")
        vx = np.pad(vx, ((0, R - vx.shape[0]), (0, C - vx.shape[1])), mode="edge")
    ii, jj = np.indices((R, C))
    si = np.clip(np.rint(ii - hours * vy).astype(int), 0, R - 1)
    sj = np.clip(np.rint(jj - hours * vx).astype(int), 0, C - 1)
    return field[si, sj]


def satm_tiles(fields, t, prev=None):
    """Morceaux 5° × 5° à 0,05° : (0) nuage mesuré (%), (1) nuages d'altitude d'après l'infrarouge (%),
    255 = hors image. Heure 0 = observation ; heures 1 à 3 = nuages déplacés selon leur mouvement de la dernière
    heure (prévision immédiate par extrapolation, comme les services météo), si l'image précédente est connue."""
    import struct, zlib
    M, K = satm_grid(fields)
    stack_m, stack_k = [M], [K]
    note = "sans mouvement"
    if prev is not None and prev.get("t") == int(t.timestamp()) - 3600 and prev["M"].shape == M.shape:
        dy, dx, b = motion(prev["M"], M)
        for h in range(1, NOWCAST_HOURS + 1):
            stack_m.append(advect(M, dy, dx, b, h)); stack_k.append(advect(K, dy, dx, b, h))
        sp = np.hypot(dy, dx * 0.69) * SATM_RES * 111          # 0,69 = cos 46° (longitude)
        note = f"mouvement médian {np.median(sp):.0f} km/h"
    nh = len(stack_m)
    cube = np.stack([np.stack([a, c]) for a, c in zip(stack_m, stack_k)])          # [heure][var][ligne][col]
    enc = np.where(np.isnan(cube), 255, np.clip(np.rint(cube * 100), 0, 100)).astype(np.uint8)
    files = {}
    n = int(round(SATM_TILE / SATM_RES))
    ref = int(t.timestamp())
    for la in range(G_LAT0, G_LAT1, SATM_TILE):
        for lo in range(G_LON0, G_LON1, SATM_TILE):
            i0 = int(round((la - G_LAT0) / SATM_RES)); j0 = int(round((lo - G_LON0) / SATM_RES))
            block = enc[:, :, i0:i0 + n, j0:j0 + n]
            if block.shape[2] < n or block.shape[3] < n or (block == 255).all():
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref, 3600, nh, 2, float(la), float(lo), SATM_RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{SATM}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    b = io.BytesIO(); np.savez_compressed(b, M=M, t=np.int64(ref)); files[SATM_PREV] = b.getvalue()
    print("Satellite : nuages mesurés,", nh, "heure(s),", note)
    return files

def prev_grid(s3):
    from lucarne_forecast import r2_settings
    try:
        d = np.load(io.BytesIO(s3.get_object(Bucket=r2_settings()[3], Key=SATM_PREV)["Body"].read()))
        return {"M": d["M"], "t": int(d["t"])}
    except Exception:
        return None


def run_sat(put_files=None):
    from lucarne_forecast import r2_client, upload
    s3, bucket = r2_client()
    try:
        idx = json.loads(s3.get_object(Bucket=bucket, Key=PREFIX + "index.json")["Body"].read())
    except Exception:
        idx = {"hours": []}
    have = set(idx.get("hours", [])) if idx.get("render") == RENDER else set()
    have_geo = set(idx.get("geo", []))
    now = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    files = {}
    geo_cache = {}
    satm_new = None
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
        # Image GeoColour de la même heure (gardée pour la boucle GeoColour plus bas) : stratus de nuit
        geo_jpg = None if key in have_geo else fetch(t, GEO, tries=2, timeout=60, fmt="image/jpeg")
        if geo_jpg: geo_cache[key] = geo_jpg
        low = None
        if geo_jpg or key in have_geo:
            try:
                src = geo_jpg or s3.get_object(Bucket=bucket, Key=f"{PREFIX}geo/{key}.jpg")["Body"].read()
                low = geo_low(src, t, Image.open(io.BytesIO(png)).size)
            except Exception as e:
                print("Satellite : stratus GeoColour indisponible :", str(e)[:100])
        ov, mean, fields = to_overlay(png, mask, low)
        files[f"{PREFIX}{key}.png"] = ov
        # Heure la plus récente traitée : nuages mesurés pour recaler les premières heures de prévision dans l'app
        if not any(f.startswith(SATM) for f in files):
            try:
                cth = fetch(t, CTH, width=700, tries=2, timeout=40)
                files.update(satm_tiles(with_cth(fields, cth), t, prev_grid(s3))); satm_new = key
                print("Satellite : nuages mesurés", t.isoformat(), "→", sum(1 for f in files if f.startswith(SATM)), "morceaux")
            except Exception as e:
                print("Satellite : morceaux mesurés en échec :", str(e)[:120])
        have.add(key)
        print("Satellite :", t.isoformat(), "luminance moyenne", round(mean), "→", len(ov) // 1024, "ko")
        time.sleep(1)
    # Nuages mesurés : si cette passe n'a traité aucune heure nouvelle et que la dernière heure disponible n'a pas
    # encore ses morceaux (première fois, ou échec précédent), on les refait à partir de cette heure
    satm_at = idx.get("satm") if idx.get("satm_v") == 4 else None
    newest = max(have) if have else None
    if newest and not any(f.startswith(SATM) for f in files) and satm_at != newest:
        t = dt.datetime.strptime(newest, "%Y%m%d%H").replace(tzinfo=dt.timezone.utc)
        png = fetch(t); mask = fetch(t, MASK, width=700, tries=2, timeout=40) if png else None
        if png and mask:
            try:
                cth = fetch(t, CTH, width=700, tries=2, timeout=40)
                try: low = geo_low(s3.get_object(Bucket=bucket, Key=f"{PREFIX}geo/{newest}.jpg")["Body"].read(), t, Image.open(io.BytesIO(png)).size)
                except Exception: low = None
                files.update(satm_tiles(with_cth(to_overlay(png, mask, low)[2], cth), t, prev_grid(s3))); satm_at = newest
                print("Satellite : nuages mesurés (rattrapage)", newest)
            except Exception as e:
                print("Satellite : rattrapage en échec :", str(e)[:120])
    if satm_new:
        satm_at = satm_new
    # Image satellite « brute » (GeoColour), une par heure, avec le temps qui reste
    for k in range(HOURS + 1):
        t = now - dt.timedelta(hours=k)
        key = t.strftime("%Y%m%d%H")
        if key in have_geo:
            continue
        if time.time() - start > 15 * 60:
            print("Satellite : GeoColour, temps limite atteint"); break
        jpg = geo_cache.get(key) or fetch(t, GEO, tries=2, timeout=60, fmt="image/jpeg")
        if not jpg:
            continue
        # Recompression légère (≈ 300 ko) : l'app en garde 13 en mémoire
        im = Image.open(io.BytesIO(jpg)).convert("RGB"); b = io.BytesIO(); im.save(b, "JPEG", quality=78, optimize=True)
        files[f"{PREFIX}geo/{key}.jpg"] = b.getvalue()
        have_geo.add(key)
        print("Satellite : GeoColour", t.isoformat(), "→", len(b.getvalue()) // 1024, "ko")
    limit = (now - dt.timedelta(hours=26)).strftime("%Y%m%d%H")
    for h in sorted(x for x in have_geo if x < limit):
        try: s3.delete_object(Bucket=bucket, Key=f"{PREFIX}geo/{h}.jpg")
        except Exception: pass
    geo_keep = sorted(h for h in have_geo if h >= limit)
    old = sorted(h for h in have if h < limit)
    keep = sorted(h for h in have if h >= limit)
    for h in old:
        try: s3.delete_object(Bucket=bucket, Key=f"{PREFIX}{h}.png")
        except Exception: pass
    meta = {"source": "EUMETSAT Meteosat MTG-I FCI IR 10,5 µm (EUMETView)", "licence": "CC BY 4.0",
            "credit": f"Contient des données EUMETSAT Meteosat modifiées {now.year}",
            "updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "crs": "EPSG:3857", "render": RENDER, "bbox": [X0, Y0, X1, Y1], "size": [WIDTH, HEIGHT], "hours": keep, "geo": geo_keep, "satm": satm_at, "satm_v": 4}
    files[PREFIX + "index.json"] = json.dumps(meta).encode()
    # « latest.json » attendu en dernier par upload() : l'index joue ce rôle
    files[PREFIX + "latest.json"] = files.pop(PREFIX + "index.json")
    files_index = dict(files); files_index[PREFIX + "index.json"] = files[PREFIX + "latest.json"]
    upload(files_index)
    print("Satellite :", len(keep), "heures disponibles,", len(old), "effacées")

if __name__ == "__main__":
    run_sat()
