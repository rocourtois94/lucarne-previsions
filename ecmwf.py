"""Nébulosité ECMWF IFS 0,1° (données ouvertes, ≈ 10 km) en morceaux pour l'app, hors des domaines fins.

Remplace la nébulosité GFS (0,25°) pour la carte « Meilleur ciel » sur l'Europe.
 1. dernier run IFS 0,1° dont l'échéance 72 h est publiée (data.ecmwf.int) ;
 2. toutes les 3 h de 0 à 72 h : nébulosité totale, basse, moyenne, haute (tcc, lcc, mcc, hcc), en ne
    téléchargeant que ces 4 champs grâce au fichier d'index (requêtes partielles) ;
 3. morceaux 5° × 5° « LCFC » (bords inclus, 51 × 51 points, pas 3 h) dans R2 : fc1/ec/<lat>_<lon>.bin.

Données : ECMWF, licence CC BY 4.0 (« Source : ECMWF »).
"""
import concurrent.futures as cf
import datetime as dt
import json, struct, time, urllib.request, zlib

import numpy as np
import eccodes

BASE = "https://data.ecmwf.int/forecasts"
BOX = dict(top=72, bottom=35, left=-25, right=45)       # Europe (comme le seeing)
STEPS = list(range(0, 73, 3))
TILE = 5
RES = 0.1
PREFIX = "fc1/ec/"
PARAMS = ["tcc", "lcc", "mcc", "hcc"]


def stem(run, step):
    return f"{BASE}/{run:%Y%m%d}/{run:%H}z/ifs/0p1/oper/{run:%Y%m%d%H}0000-{step}h-oper-fc-sfc"


def get(u, rng=None, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(u, headers={"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else {})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404 or k == tries - 1:
                raise
            time.sleep(3 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def latest_run():
    now = dt.datetime.now(dt.timezone.utc)
    t = now.replace(hour=now.hour // 6 * 6, minute=0, second=0, microsecond=0)
    for k in range(8):
        run = t - dt.timedelta(hours=6 * k)
        try:
            get(stem(run, STEPS[-1]) + ".index", (0, 100))
            return run
        except Exception:
            continue
    raise SystemExit("ECMWF : aucun run trouvé")


def run_ecmwf(put):
    t0 = time.time()
    run = latest_run()
    print("ECMWF : run", run.isoformat())
    grid = {}

    def work(step):
        s = stem(run, step)
        idx = [json.loads(l) for l in get(s + ".index").decode().splitlines() if l.strip()]
        out = {}
        for e in idx:
            p = e.get("param")
            if p in PARAMS and p not in out:
                data = get(s + ".grib2", (e["_offset"], e["_offset"] + e["_length"] - 1))
                h = eccodes.codes_new_from_message(data)
                try:
                    v = eccodes.codes_get_values(h)
                    nj, ni = eccodes.codes_get(h, "Nj"), eccodes.codes_get(h, "Ni")
                    la1 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
                    lo1 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
                    dj = eccodes.codes_get(h, "jDirectionIncrementInDegrees")
                    di = eccodes.codes_get(h, "iDirectionIncrementInDegrees")
                    jpos = eccodes.codes_get(h, "jScansPositively")
                finally:
                    eccodes.codes_release(h)
                grid["g"] = (nj, ni, la1, lo1, dj, di, jpos)
                out[p] = v.reshape(nj, ni)
        return step, out

    res = {}
    with cf.ThreadPoolExecutor(6) as ex:
        for step, out in ex.map(work, STEPS):
            res[step] = out
    nj, ni, la1, lo1, dj, di, jpos = grid["g"]
    lats = la1 + (1 if jpos else -1) * dj * np.arange(nj)
    lons = (lo1 + di * np.arange(ni) + 180) % 360 - 180
    io = np.argsort(lats); jo = np.argsort(lons)
    lats, lons = lats[io], lons[jo]
    ia, ib = np.searchsorted(lats, BOX["bottom"] - 1e-6), np.searchsorted(lats, BOX["top"] + 1e-6)
    ja, jb = np.searchsorted(lons, BOX["left"] - 1e-6), np.searchsorted(lons, BOX["right"] + 1e-6)
    sub_la, sub_lo = lats[ia:ib], lons[ja:jb]
    cube = np.full((len(STEPS), 4, ib - ia, jb - ja), 255, np.uint8)
    scale = None
    for si, st in enumerate(STEPS):
        for vi, p in enumerate(PARAMS):
            if p not in res[st]:
                continue
            v = res[st][p][io][:, jo][ia:ib, ja:jb]
            if scale is None:
                scale = 100.0 if np.nanmax(v) <= 1.01 else 1.0      # fractions 0…1 ou déjà en %
            cube[si, vi] = np.clip(np.rint(v * scale), 0, 100).astype(np.uint8)
    print("ECMWF : téléchargé en", round(time.time() - t0), "s ; Saint-Julien +24 h :",
          [int(x) for x in cube[STEPS.index(24), :, np.argmin(abs(sub_la - 46.14)), np.argmin(abs(sub_lo - 6.08))]])
    files = {}
    ref_unix = int(run.timestamp())
    n = int(round(TILE / RES)) + 1                                  # bords inclus (interpolation sans couture)
    for la in range(BOX["bottom"], BOX["top"], TILE):
        for lo in range(BOX["left"], BOX["right"], TILE):
            i0 = int(np.searchsorted(sub_la, la - 1e-6)); j0 = int(np.searchsorted(sub_lo, lo - 1e-6))
            block = cube[:, :, i0:i0 + n, j0:j0 + n]
            if block.shape[2] < 2 or block.shape[3] < 2:
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3 * 3600, len(STEPS), 4,
                                       float(sub_la[i0]), float(sub_lo[j0]), RES, block.shape[2], block.shape[3])
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{PREFIX}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    meta = {"model": "ECMWF IFS 0,1°", "source": "ECMWF", "licence": "CC BY 4.0", "reference": run.isoformat(),
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "variables": ["CLCT", "CLCL", "CLCM", "CLCH"], "steps_h": 3, "hours": STEPS[-1], "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    put(files)
    print("ECMWF : terminé en", round(time.time() - t0), "s,", len(files), "fichiers,",
          round(sum(len(v) for v in files.values()) / 1e6, 1), "Mo")
