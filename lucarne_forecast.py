#!/usr/bin/env python3
"""Prévisions Lucarne — nébulosité MeteoSwiss ICON-CH2 en petits morceaux pour l'app.

Tourne sur GitHub Actions toutes les 6 heures :
 1. trouve le dernier run complet d'ICON-CH2-EPS (membre de contrôle, 2,1 km, 120 h) via l'API STAC de geo.admin.ch ;
 2. télécharge la nébulosité totale, basse, moyenne et haute (CLCT, CLCL, CLCM, CLCH) et l'altitude du sommet
    des nuages (CDCT) pour chaque heure ;
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
VARS = ["CLCT", "CLCL", "CLCM", "CLCH", "CDCT", "T2M", "TD2M", "WIND", "GUST", "TQV", "TQI"]
# Paramètres ICON à télécharger pour chaque variable de sortie, et codage sur un octet (voir codage.py)
import codage
INPUTS = {"CLCT": ["CLCT"], "CLCL": ["CLCL"], "CLCM": ["CLCM"], "CLCH": ["CLCH"], "CDCT": ["CDCT"],
          "T2M": ["T_2M"], "TD2M": ["TD_2M"], "WIND": ["U_10M", "V_10M"], "GUST": ["VMAX_10M"],
          "TQV": ["TQV"], "TQI": ["TQI"]}
ENCODE = {"T2M": codage.kelvin, "TD2M": codage.kelvin, "WIND": codage.wind_kmh, "GUST": codage.kmh_from_ms,
          "TQV": codage.vapour_mm, "TQI": codage.ice_gm2}
# CDCT : sommet des nuages au-dessus de la mer (m), codé par pas de 20 m (1…250 = 20…5000 m, 250 = 5000 m ou plus),
# 0 = pas de nuage. Sert à savoir si une mer de brouillard / stratus est SOUS le lieu (lieu en altitude).
TOP_STEP = 20
HOURS = 120
RES = 0.02          # degrés
TILE = 1            # degré
PREFIX = "fc1/ch2/"
PREFIX_H = "fc1/ch2h/"     # altitude du terrain du modèle (m / 20), pour choisir la case voisine à la bonne altitude
PREFIX_W = "fc1/ch2w/"     # météo (T2M, TD2M, WIND, GUST, TQV, TQI)
NCLOUD = 5                 # les 5 premières variables vont dans fc1/ch2/
MODEL = "ICON-CH2-EPS"
CONST = "horizontal_constants_icon-ch2-eps.grib2"
FAR_DEG = 0.05             # au-delà, la case est hors du domaine du modèle
ENS_PREFIX = "fc1/ch2e/"


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
    raw = download(d["assets"][CONST]["href"])
    lat = lon = hs = None
    f = io.BytesIO(raw)
    pos = 0
    while pos < len(raw):
        h = eccodes.codes_new_from_message(raw[pos:])
        n = eccodes.codes_get(h, "totalLength")
        key = (eccodes.codes_get(h, "discipline"), eccodes.codes_get(h, "parameterCategory"), eccodes.codes_get(h, "parameterNumber"))
        if key == (0, 191, 1): lat = eccodes.codes_get_values(h)
        if key == (0, 191, 2): lon = eccodes.codes_get_values(h)
        if key == (0, 3, 6): hs = eccodes.codes_get_values(h)          # HSURF : altitude du terrain (m)
        eccodes.codes_release(h)
        pos += n
    if lat is None or lon is None:
        sys.exit("CLAT/CLON introuvables")
    return lat, lon, hs


def unit(lat, lon):
    la, lo = np.radians(lat), np.radians(lon)
    return np.stack([np.cos(la) * np.cos(lo), np.cos(la) * np.sin(lo), np.sin(la)], -1)


def main():
    t0 = time.time()
    check_r2()
    ref = latest_reference()
    print("Run", ref)
    # Passage toutes les heures : rien à faire si ce run est déjà publié (sauf FORCE=1)
    if not os.environ.get("FORCE"):
        try:
            # Lu directement dans R2 (le site public refuse les robots sans navigateur)
            s3, bucket = r2_client()
            pub = json.loads(s3.get_object(Bucket=bucket, Key=PREFIX + "latest.json")["Body"].read()).get("reference")
            if pub == ref:
                print("MeteoSwiss : run", ref, "déjà publié, rien à faire"); return
        except Exception as e:
            print("MeteoSwiss : run publié inconnu (", str(e)[:80], "), calcul complet")
    lat, lon, hsurf = grid_coordinates()
    lat0, lat1 = np.floor(lat.min() + 0.2), np.floor(lat.max() - 0.2)       # bords exclus (moins fiables)
    lon0, lon1 = np.floor(lon.min() + 0.2), np.floor(lon.max() - 0.2)
    glat = np.arange(lat0, lat1 + 1, RES) + RES / 2                          # centres des cases
    glon = np.arange(lon0, lon1 + 1, RES) + RES / 2
    GLON, GLAT = np.meshgrid(glon, glat)
    tree = cKDTree(unit(lat, lon))
    dist, idx = tree.query(unit(GLAT.ravel(), GLON.ravel()))
    far = dist > np.radians(FAR_DEG)                                            # hors du domaine du modèle
    idx = idx.reshape(GLAT.shape); far = far.reshape(GLAT.shape)
    nh = HOURS + 1
    cube = np.full((nh, len(VARS)) + GLAT.shape, 255, np.uint8)

    jobs = []
    urls = {p: asset_urls(ref, p) for v in VARS for p in INPUTS[v]}
    for vi, v in enumerate(VARS):
        params = INPUTS[v]
        for h in range(HOURS + 1):
            if all(h in urls[p] for p in params):
                jobs.append((vi, h, [urls[p][h] for p in params]))
    print(len(jobs), "échéances × variables")

    def work(job):
        vi, h, us = job
        fields = [grib_values(download(u))[idx] for u in us]
        name = VARS[vi]
        if name == "CDCT":
            v = fields[0]
            g = np.where(v < 0, 0, np.clip(np.rint(v / TOP_STEP), 1, 250)).astype(np.uint8)
        elif name in ENCODE:
            g = ENCODE[name](*fields)
        else:
            g = np.clip(np.rint(fields[0]), 0, 100).astype(np.uint8)
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
            # Deux fichiers : nuages (fc1/ch2/, lus aussi par la carte) et météo (fc1/ch2w/, lus seulement pour les lieux)
            # En-tête : « LCFC », version, run (unix), pas (s), nb heures, nb variables, lat sud, lon ouest, pas (°), lignes, colonnes
            for pre, part in ((PREFIX, block[:, :NCLOUD]), (PREFIX_W, block[:, NCLOUD:])):
                if part.shape[1] == 0:
                    continue
                hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, nh, part.shape[1],
                                           float(la), float(lo), RES, n, n)
                c = zlib.compressobj(9, zlib.DEFLATED, -15)          # DEFLATE brut (lu par Apple « .zlib »)
                files[f"{pre}{int(la)}_{int(lo)}.bin"] = hdr + c.compress(np.ascontiguousarray(part).tobytes()) + c.flush()
    # Altitude du terrain (une seule « heure », une variable) : seulement là où il y a la météo (ICON-CH2)
    if hsurf is not None and len(VARS) > NCLOUD:
        hg = np.clip(np.rint(hsurf[idx] / 20), 0, 250).astype(np.uint8)
        hg[far] = 255
        for la in np.arange(lat0, lat1 + 1, TILE):
            for lo in np.arange(lon0, lon1 + 1, TILE):
                i0 = int(round((la - lat0) / RES)); j0 = int(round((lo - lon0) / RES))
                blk = hg[i0:i0 + n, j0:j0 + n]
                if blk.shape[0] < n or blk.shape[1] < n or (blk == 255).all():
                    continue
                hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, 1, 1, float(la), float(lo), RES, n, n)
                c = zlib.compressobj(9, zlib.DEFLATED, -15)
                files[f"{PREFIX_H}{int(la)}_{int(lo)}.bin"] = hdr + c.compress(np.ascontiguousarray(blk).tobytes()) + c.flush()
    meta = {"model": MODEL + " (contrôle)", "source": "MeteoSwiss", "licence": "CC BY 4.0",
            "reference": ref, "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "variables": VARS, "cdct_step_m": TOP_STEP, "hours": nh, "lat": [float(lat0), float(lat1 + 1)], "lon": [float(lon0), float(lon1 + 1)],
            "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    total = sum(len(b) for b in files.values())
    print(len(files), "fichiers,", round(total / 1e6, 1), "Mo")
    upload(files)
    print("Terminé en", round(time.time() - t0), "s")
    # Probabilité de ciel dégagé (21 scénarios) : réutilise la grille et le membre de contrôle déjà calculés
    try:
        import ensemble
        ensemble.run_ensemble(sys.modules[__name__], ref, idx, far, cube, lat0, lat1, lon0, lon1)
    except Exception:
        import traceback
        print("Échec (ensemble) :"); traceback.print_exc()


def run_ch1():
    """ICON-CH1-EPS : 1 km, run toutes les 3 h, 33 h d'échéance, contrôle + 10 scénarios — nuages seulement
    (fc1/ch1/, 0,01°) et probabilité de ciel dégagé (fc1/ch1e/). Même code que ICON-CH2, autres réglages."""
    g = globals()
    keep = {k: g[k] for k in ("COLL", "HOURS", "RES", "PREFIX", "PREFIX_W", "VARS", "MODEL", "CONST", "FAR_DEG", "ENS_PREFIX")}
    g.update(COLL="ch.meteoschweiz.ogd-forecasting-icon-ch1", HOURS=33, RES=0.01, PREFIX="fc1/ch1/", PREFIX_W="fc1/ch1w/",
             VARS=VARS[:NCLOUD], MODEL="ICON-CH1-EPS", CONST="horizontal_constants_icon-ch1-eps.grib2",
             FAR_DEG=0.02, ENS_PREFIX="fc1/ch1e/")
    try:
        main()
    finally:
        g.update(keep)


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
    import traceback
    errors = 0
    jobs = (("nuages", main),
            ("ch1", run_ch1),
            ("seeing", lambda: __import__("seeing").run_seeing(upload)),
            ("arome", lambda: __import__("arome").run_arome(upload)),     # dépôt public : minutes d'Actions illimitées
            ("ecmwf", lambda: __import__("ecmwf").run_ecmwf(upload)),
            ("ens", lambda: __import__("ensemble_ec").run_ensemble_ec(upload)),
            ("vigilance", lambda: __import__("vigilance").run_vigilance(upload)))
    only = {j.strip() for j in os.environ.get("JOBS", "").split(",") if j.strip()}
    for name, job in jobs:
        if only and name not in only:
            continue
        try:
            job()
        except SystemExit as e:
            if e.code not in (None, 0):
                print(f"Échec ({name}) :", e.code); errors += 1
        except Exception:
            print(f"Échec ({name}) :"); traceback.print_exc(); errors += 1
    sys.exit(1 if errors else 0)
