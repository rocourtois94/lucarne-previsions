"""Probabilité de ciel dégagé partout dans le monde — ensemble ECMWF ENS 0,25° (50 scénarios perturbés).

Complète l'ensemble MeteoSwiss (fc1/ch2e/, 2 km, Alpes) pour tous les autres lieux.
 1. dernier run ENS (00 ou 12 UTC) dont l'échéance 144 h est publiée (data.ecmwf.int, données ouvertes) ;
 2. toutes les 3 h de 0 à 144 h : nébulosité totale (tcc) des 50 membres, en ne téléchargeant que ce champ
    grâce au fichier d'index (requêtes partielles) ;
 3. part des scénarios où le ciel est dégagé (tcc < 25 %), en % (0…100) ;
 4. morceaux 10° × 10° « LCFC » (41 × 41 points, bords inclus, pas 3 h) : fc1/ens/<lat>_<lon>.bin.
Pas de prise en compte de l'altitude du lieu ici (maille de 25 km : la mer de brouillard n'y est pas résolue).
Données : ECMWF, licence CC BY 4.0 (« Source : ECMWF »).
"""
import concurrent.futures as cf
import datetime as dt
import json, struct, time, zlib

import numpy as np
import eccodes

import ecmwf as E

STEPS = list(range(0, 145, 3))
TILE = 10
RES = 0.25
CLEAR = 0.25
PREFIX = "fc1/ens/"


def stem(run, step):
    return f"{E.BASE}/{run:%Y%m%d}/{run:%H}z/ifs/0p25/enfo/{run:%Y%m%d%H}0000-{step}h-enfo-ef"


def latest_run():
    now = dt.datetime.now(dt.timezone.utc)
    t = now.replace(hour=now.hour // 12 * 12, minute=0, second=0, microsecond=0)
    for k in range(4):
        run = t - dt.timedelta(hours=12 * k)
        try:
            E.get(stem(run, STEPS[-1]) + ".index", (0, 100))
            return run
        except Exception:
            continue
    raise SystemExit("ENS : aucun run trouvé")


def run_ensemble_ec(put):
    t0 = time.time()
    run = latest_run()
    print("ENS : run", run.isoformat())

    def work(step):
        idx = [json.loads(l) for l in E.get(stem(run, step) + ".index").decode().splitlines()]
        msgs = [m for m in idx if m.get("param") == "tcc"]
        clear = None; n = 0
        for m in msgs:
            raw = E.get(stem(run, step) + ".grib2", (m["_offset"], m["_offset"] + m["_length"] - 1))
            h = eccodes.codes_new_from_message(raw)
            try:
                v = eccodes.codes_get_values(h).reshape(721, 1440)
            finally:
                eccodes.codes_release(h)
            c = (v < CLEAR).astype(np.uint16)
            clear = c if clear is None else clear + c
            n += 1
        if not n:
            return step, None, 0
        pct = np.rint(clear * 100.0 / n).astype(np.uint8)
        # Grille ECMWF : latitude 90 → −90, longitude −180 → 179,75 (premier point à 180°)
        return step, pct[::-1], n          # lignes du sud au nord

    cube = np.full((len(STEPS), 721, 1440), 255, np.uint8)
    nm = 0
    with cf.ThreadPoolExecutor(12) as ex:
        for step, g, n in ex.map(work, STEPS):
            if g is not None:
                cube[STEPS.index(step)] = g; nm = max(nm, n)
    print("ENS : téléchargé en", round(time.time() - t0), "s,", nm, "scénarios")

    ref_unix = int(run.timestamp())
    n = int(round(TILE / RES)) + 1
    files = {}
    for la in range(-60, 80, TILE):
        for lo in range(-180, 180, TILE):
            i0 = int(round((la + 90) / RES))
            j = (np.arange(n) + int(round((lo + 180) / RES))) % 1440
            block = cube[:, i0:i0 + n][:, :, j]
            if block.shape[1] < n:
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 10800, len(STEPS), 1,
                                       la - RES / 2, lo - RES / 2, RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{PREFIX}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block[:, None]).tobytes()) + c.flush()
    meta = {"model": "ECMWF ENS 0,25° (ensemble)", "source": "ECMWF", "licence": "CC BY 4.0", "reference": run.isoformat(),
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "variables": ["PCLR"],
            "unit": "% des scénarios", "members": nm, "clear_below_pct": int(CLEAR * 100), "step_h": 3, "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    print("ENS :", len(files), "fichiers,", round(sum(map(len, files.values())) / 1e6, 1), "Mo")
    put(files)


if __name__ == "__main__":
    import lucarne_forecast
    run_ensemble_ec(lucarne_forecast.upload)
