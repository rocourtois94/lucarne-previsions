"""Nébulosité DWD ICON-EU (0,0625° ≈ 6,5 km, toute l'Europe) en morceaux pour l'app → fc1/eu/<lat>_<lon>.bin

Couvre ce que MeteoSwiss (Alpes) et AROME (France) ne couvrent pas. Données ouvertes du Deutscher Wetterdienst
(opendata.dwd.de), licence CC BY 4.0 : « Datenbasis : Deutscher Wetterdienst, données modifiées » (regrillées,
découpées). Dernier run complet parmi 00, 06, 12, 18 UTC ; échéances horaires 0 à 72 h ; 4 variables
(CLCT, CLCL, CLCM, CLCH, en %). Morceaux 5° × 5° « LCFC » sur 35–60° N, 15° O–30° E, centres des cases sur la
grille du modèle. ≈ 290 fichiers de 1 Mo téléchargés par run.
"""
import bz2, concurrent.futures as cf, datetime as dt, json, struct, time, urllib.request, zlib
import numpy as np
import eccodes

BASE = "https://opendata.dwd.de/weather/nwp/icon-eu/grib"
VARS = ["CLCT", "CLCL", "CLCM", "CLCH"]
HOURS = 72
PREFIX = "fc1/eu/"
TILE = 5
BOX = (35, 60, -15, 30)
LAT0, LON0, RES, NJ, NI = 29.5, -23.5, 0.0625, 657, 1377
UA = {"User-Agent": "Lucarne-previsions (info@photily.fr)"}


def url(run, h, var):
    return (f"{BASE}/{run:%H}/{var.lower()}/icon-eu_europe_regular-lat-lon_single-level_"
            f"{run:%Y%m%d%H}_{h:03d}_{var}.grib2.bz2")


def get(u, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == tries - 1:
                raise
            time.sleep(3 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def exists(u):
    try:
        urllib.request.urlopen(urllib.request.Request(u, method="HEAD", headers=UA), timeout=30); return True
    except Exception:
        return False


def latest_run():
    now = dt.datetime.now(dt.timezone.utc)
    t = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for k in range(5):
        run = t - dt.timedelta(hours=6 * k)
        if all(exists(url(run, HOURS, v)) for v in VARS):
            return run
    raise SystemExit("ICON-EU : aucun run complet")


def field(u):
    h = eccodes.codes_new_from_message(bz2.decompress(get(u)))
    try:
        assert eccodes.codes_get(h, "Ni") == NI and eccodes.codes_get(h, "Nj") == NJ
        return eccodes.codes_get_values(h).reshape(NJ, NI)      # du sud au nord, d'ouest en est
    finally:
        eccodes.codes_release(h)


def run_iconeu(upload):
    from lucarne_forecast import r2_client
    t0 = time.time()
    run = latest_run()
    ref = run.isoformat().replace("+00:00", "Z")
    try:
        s3, bucket = r2_client()
        pub = json.loads(s3.get_object(Bucket=bucket, Key=PREFIX + "latest.json")["Body"].read()).get("reference")
        if pub == ref:
            print("ICON-EU : run", ref, "déjà publié"); return
    except Exception:
        pass
    print("ICON-EU : run", ref)
    la0, la1, lo0, lo1 = BOX
    j0 = int(round((la0 - LAT0) / RES)); j1 = int(round((la1 - LAT0) / RES))
    i0 = int(round((lo0 - LON0) / RES)); i1 = int(round((lo1 - LON0) / RES))
    nh = HOURS + 1
    cube = np.full((nh, len(VARS), j1 - j0, i1 - i0), 255, np.uint8)
    jobs = [(h, vi, v) for h in range(nh) for vi, v in enumerate(VARS)]

    def work(job):
        h, vi, v = job
        try:
            f = field(url(run, h, v))[j0:j1, i0:i1]
            return h, vi, np.clip(np.rint(f), 0, 100).astype(np.uint8)
        except Exception as e:
            print("ICON-EU :", v, h, str(e)[:80]); return h, vi, None
    with cf.ThreadPoolExecutor(12) as ex:
        for h, vi, g in ex.map(work, jobs):
            if g is not None:
                cube[h, vi] = g
    print("ICON-EU : téléchargé en", round(time.time() - t0), "s")
    n = int(round(TILE / RES))
    files = {}
    ref_unix = int(run.timestamp())
    for la in range(la0, la1, TILE):
        for lo in range(lo0, lo1, TILE):
            a = int(round((la - la0) / RES)); b = int(round((lo - lo0) / RES))
            block = cube[:, :, a:a + n, b:b + n]
            if block.shape[2] < n or block.shape[3] < n or (block == 255).all():
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, nh, len(VARS),
                                       float(la - RES / 2), float(lo - RES / 2), RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{PREFIX}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    meta = {"model": "DWD ICON-EU", "source": "Deutscher Wetterdienst", "licence": "CC BY 4.0",
            "credit": "Datenbasis : Deutscher Wetterdienst, données modifiées", "reference": ref,
            "variables": VARS, "hours": nh, "res": RES, "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    print("ICON-EU :", len(files), "fichiers,", round(sum(len(x) for x in files.values()) / 1e6, 1), "Mo")
    upload(files)
    print("ICON-EU : terminé en", round(time.time() - t0), "s")
