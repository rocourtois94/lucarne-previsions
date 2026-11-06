#!/usr/bin/env python3
"""Prévisions Lucarne — nébulosité MeteoSwiss ICON-CH2 en petits morceaux pour l'app.

Tourne sur GitHub Actions toutes les 6 heures :
 1. trouve le dernier run complet d'ICON-CH2-EPS (membre de contrôle, 2,1 km, 120 h) via l'API STAC de geo.admin.ch ;
 2. télécharge la nébulosité totale, basse, moyenne et haute (CLCT, CLCL, CLCM, CLCH) pour chaque heure ;
 3. la ramène sur une grille régulière de 0,02° (plus proche point de la grille icosaédrique) ;
 4. découpe en morceaux de 1° × 1° (« LCFC ») et les dépose dans R2 (fc1/ch2/<lat>_<lon>.bin + latest.json).

Données : MeteoSwiss, licence CC BY 4.0 (« Source : MeteoSwiss »).
Secrets attendus (réglages du dépôt GitHub) : R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET.
Sans secrets, le script écrit les fichiers dans ./sortie (essai local).
"""
import concurrent.futures as cf
import datetime as dt
import io, json, os, struct, sys, time, urllib.request, zlib

import numpy as np
import eccodes
from scipy.spatial import cKDTree

STAC = "https://data.geo.admin.ch/api/stac/v1"
COLL = "ch.meteoschweiz.ogd-forecasting-icon-ch2"
VARS = ["CLCT", "CLCL", "CLCM", "CLCH"]
HOURS = 120
RES = 0.02          # degrés
TILE = 1            # degré
PREFIX = "fc1/ch2/"


def http_json(url, body=None, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                         headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def search(body):
    """Recherche STAC avec pagination (liens « next » en POST)."""
    out, url, b = [], STAC + "/search", dict(body)
    while True:
        d = http_json(url, b)
        out += d.get("features", [])
        nxt = next((l for l in d.get("links", []) if l.get("rel") == "next"), None)
        if not nxt:
            return out
        url = nxt["href"]
        # « merge: true » : le curseur s'ajoute aux filtres de la recherche (sinon on recevrait tout le catalogue)
        b = {**body, **nxt.get("body", {})} if nxt.get("merge", True) else nxt.get("body", body)


def iso_horizon(h):
    return f"P{h // 24}DT{h % 24:02d}H00M00S"


def latest_reference():
    """Dernier run dont l'échéance 120 h de CLCT est publiée (le run est alors complet)."""
    feats = search({"collections": [COLL], "forecast:variable": "CLCT", "forecast:perturbed": False,
                    "forecast:horizon": iso_horizon(HOURS), "limit": 100})
    refs = sorted({f["properties"]["forecast:reference_datetime"] for f in feats})
    if not refs:
        sys.exit("Aucun run complet trouvé")
    return refs[-1]


def asset_urls(ref, var):
    feats = search({"collections": [COLL], "forecast:variable": var, "forecast:perturbed": False,
                    "forecast:reference_datetime": ref, "limit": 200})
    urls = {}
    for f in feats:
        p = f["properties"]
        if (p.get("forecast:perturbed") or p.get("forecast:variable") != var or not f.get("assets")
                or p.get("forecast:reference_datetime") != ref):
            continue
        # Échéance : « P0DT12H00M00S », sinon date de validité − date du run
        hz = p.get("forecast:horizon")
        if hz:
            d = int(hz[1:hz.index("D")]); h = int(hz[hz.index("T") + 1:hz.index("H")]); step = d * 24 + h
        else:
            t = dt.datetime.fromisoformat(p["datetime"].replace("Z", "+00:00"))
            r = dt.datetime.fromisoformat(ref.replace("Z", "+00:00"))
            step = int(round((t - r).total_seconds() / 3600))
        urls[step] = next(iter(f["assets"].values()))["href"]
    return urls


def download(url, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def grib_values(data):
    h = eccodes.codes_new_from_message(data)
    try:
        return eccodes.codes_get_values(h)
    finally:
        eccodes.codes_release(h)


def grid_coordinates():
    """Latitude et longitude des points de la grille ICON (fichier des constantes horizontales)."""
    d = http_json(f"{STAC}/collections/{COLL}")
    raw = download(d["assets"]["horizontal_constants_icon-ch2-eps.grib2"]["href"])
    lat = lon = None
    f = io.BytesIO(raw)
    pos = 0
    while pos < len(raw):
        h = eccodes.codes_new_from_message(raw[pos:])
        n = eccodes.codes_get(h, "totalLength")
        key = (eccodes.codes_get(h, "discipline"), eccodes.codes_get(h, "parameterCategory"), eccodes.codes_get(h, "parameterNumber"))
        if key == (0, 191, 1): lat = eccodes.codes_get_values(h)
        if key == (0, 191, 2): lon = eccodes.codes_get_values(h)
        eccodes.codes_release(h)
        pos += n
    if lat is None or lon is None:
        sys.exit("CLAT/CLON introuvables")
    return lat, lon


def unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)


def main():
    t0 = time.time()
    check_r2()
    ref = latest_reference()
    print("Run", ref)
    lat, lon = grid_coordinates()
    lat0, lat1 = np.floor(lat.min() + 0.2), np.floor(lat.max() - 0.2)       # bords exclus (moins fiables)
    lon0, lon1 = np.floor(lon.min() + 0.2), np.floor(lon.max() - 0.2)
    glat = np.arange(lat0, lat1 + 1, RES) + RES / 2                          # centres des cases
    glon = np.arange(lon0, lon1 + 1, RES) + RES / 2
    GLON, GLAT = np.meshgrid(glon, glat)
    tree = cKDTree(unit(lat, lon))
    dist, idx = tree.query(unit(GLAT.ravel(), GLON.ravel()))
    far = dist > np.radians(0.05)                                            # hors du domaine du modèle
    idx = idx.reshape(GLAT.shape); far = far.reshape(GLAT.shape)
    nh = HOURS + 1
    cube = np.full((nh, len(VARS)) + GLAT.shape, 255, np.uint8)

    jobs = []
    for vi, v in enumerate(VARS):
        for h, u in asset_urls(ref, v).items():
            if 0 <= h <= HOURS:
                jobs.append((vi, h, u))
    print(len(jobs), "fichiers")

    def work(job):
        vi, h, u = job
        vals = grib_values(download(u))
        g = np.clip(np.rint(vals[idx]), 0, 100).astype(np.uint8)
        g[far] = 255
        return vi, h, g

    with cf.ThreadPoolExecutor(16) as ex:
        for vi, h, g in ex.map(work, jobs):
            cube[h, vi] = g
    print("Téléchargé et regrillé en", round(time.time() - t0), "s")

    ref_unix = int(dt.datetime.fromisoformat(ref.replace("Z", "+00:00")).timestamp())
    n = int(round(TILE / RES))
    files = {}
    for la in np.arange(lat0, lat1 + 1, TILE):
        for lo in np.arange(lon0, lon1 + 1, TILE):
            i0 = int(round((la - lat0) / RES)); j0 = int(round((lo - lon0) / RES))
            block = cube[:, :, i0:i0 + n, j0:j0 + n]
            if block.shape[2] < n or block.shape[3] < n or (block == 255).all():
                continue
            # En-tête : « LCFC », version, run (unix), pas (s), nb heures, nb variables, lat sud, lon ouest, pas (°), lignes, colonnes
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, nh, len(VARS),
                                       float(la), float(lo), RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)          # DEFLATE brut (lu par Apple « .zlib »)
            files[f"{PREFIX}{int(la)}_{int(lo)}.bin"] = hdr + c.compress(block.tobytes()) + c.flush()
    meta = {"model": "ICON-CH2-EPS (contrôle)", "source": "MeteoSwiss", "licence": "CC BY 4.0",
            "reference": ref, "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "variables": VARS, "hours": nh, "lat": [float(lat0), float(lat1 + 1)], "lon": [float(lon0), float(lon1 + 1)],
            "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    total = sum(len(b) for b in files.values())
    print(len(files), "fichiers,", round(total / 1e6, 1), "Mo")
    upload(files)
    print("Terminé en", round(time.time() - t0), "s")


def r2_settings():
    # .strip() : un espace ou un retour à la ligne collé avec le secret le rend invalide
    return tuple((os.environ.get(k) or "").strip() for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))


def r2_client():
    acc, key, sec, bucket = r2_settings()
    import boto3
    return boto3.client("s3", endpoint_url=f"https://{acc}.r2.cloudflarestorage.com",
                        aws_access_key_id=key, aws_secret_access_key=sec, region_name="auto"), bucket


def check_r2():
    """Essai d'écriture dans R2 avant le long calcul (affiche la cause en clair, jamais les secrets)."""
    acc, key, sec, bucket = r2_settings()
    if not all((acc, key, sec, bucket)):
        return
    print(f"R2 : compte {len(acc)} car., clé {len(key)} car. (32 attendus), secret {len(sec)} car. (64 attendus), bucket « {bucket} »")
    from botocore.exceptions import ClientError
    s3, bucket = r2_client()
    try:
        s3.put_object(Bucket=bucket, Key=PREFIX + "_essai.txt", Body=b"ok", ContentType="text/plain")
        print("R2 : écriture OK")
    except ClientError as e:
        err = e.response.get("Error", {})
        sys.exit(f"R2 refuse l'écriture : {err.get('Code')} — {err.get('Message')}")


def upload(files):
    acc, key, sec, bucket = r2_settings()
    if not all((acc, key, sec, bucket)):
        os.makedirs("sortie", exist_ok=True)
        for name, data in files.items():
            p = os.path.join("sortie", name)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            open(p, "wb").write(data)
        print("Pas de secrets R2 : fichiers écrits dans ./sortie")
        return
    s3, bucket = r2_client()
    def put(item):
        name, data = item
        s3.put_object(Bucket=bucket, Key=name, Body=data, CacheControl="public, max-age=1800",
                      ContentType="application/json" if name.endswith(".json") else "application/octet-stream")
    # Les morceaux d'abord, latest.json en dernier (l'app ne voit le nouveau run qu'une fois tout en place)
    items = sorted(files.items(), key=lambda kv: kv[0].endswith("latest.json"))
    with cf.ThreadPoolExecutor(16) as ex:
        list(ex.map(put, items[:-1]))
    put(items[-1])
    print("Déposé dans R2 :", len(files), "fichiers")


if __name__ == "__main__":
    main()
