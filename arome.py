"""Nébulosité Météo-France AROME 0,01° (≈ 1,3 km) en petits morceaux pour l'app.

 1. trouve le dernier run AROME 0,01° complet (échéance 51 h publiée) sur l'entrepôt public de Météo-France ;
 2. pour chaque heure, ne télécharge que les 3 champs utiles du paquet SP2 (nébulosité basse, moyenne, haute)
    grâce aux requêtes partielles (Range) : environ 5 Mo par heure au lieu de 17 ;
 3. ramène la grille à 0,02° (moyenne de 2 × 2 points) sur la France et ses abords ;
 4. découpe en morceaux de 1° × 1° « LCFC » (même format que MeteoSwiss) dans R2 : fc1/arome/<lat>_<lon>.bin.
Variables : CLCT (calculée : recouvrement aléatoire des trois étages, AROME ne la publie pas dans ce paquet),
CLCL, CLCM, CLCH, en %, 255 = hors domaine.

Données : Météo-France, Licence Ouverte / Open Licence 2.0 (« Source : Météo-France »).
"""
import concurrent.futures as cf
import datetime as dt
import json, struct, time, urllib.request, zlib

import numpy as np
import eccodes

BASE = "https://meteofrance-pnt.s3.rbx.io.cloud.ovh.net/pnt"
HOURS = 51
BOX = dict(bottom=41, top=52, left=-6, right=10)        # France métropolitaine, Corse, Suisse romande, Benelux sud
RES = 0.02
TILE = 1
PREFIX = "fc1/arome/"
# Grille EURW1S100 : 55,4° N → 37,5° N (du nord au sud), 12° O → 16° E, pas 0,01°
LAT_N, LON_W, STEP, NJ, NI = 55.4, -12.0, 0.01, 1791, 2801
WANT = {(6, 3): 1, (6, 4): 2, (6, 5): 3}                # (catégorie, numéro) GRIB2 → variable : basse, moyenne, haute


def url(run, h):
    r = run.strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{BASE}/{r}/arome/001/SP2/arome__001__SP2__{h:02d}H__{r}.grib2"


def get(u, rng=None, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(u, headers={"Range": f"bytes={rng[0]}-{rng[1]}"} if rng else {})
            with urllib.request.urlopen(req, timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (403, 404) or k == tries - 1:
                raise
            time.sleep(3 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def latest_run():
    now = dt.datetime.now(dt.timezone.utc)
    t = now.replace(hour=now.hour // 3 * 3, minute=0, second=0, microsecond=0)
    for k in range(10):
        run = t - dt.timedelta(hours=3 * k)
        try:
            get(url(run, HOURS), (0, 15))
            return run
        except Exception:
            continue
    raise SystemExit("AROME : aucun run complet trouvé")


def messages(u):
    """Position, longueur et (catégorie, numéro) de chaque message GRIB2 du fichier, en lisant seulement les en-têtes."""
    out, off = [], 0
    while True:
        try:
            head = get(u, (off, off + 1023))
        except urllib.error.HTTPError as e:
            if e.code == 416:
                return out
            raise
        if len(head) < 16 or head[:4] != b"GRIB":
            return out
        total = int.from_bytes(head[8:16], "big")
        p, key = 16, None
        while p + 5 <= len(head):
            ln = int.from_bytes(head[p:p + 4], "big"); num = head[p + 4]
            if num == 4:
                key = (head[p + 9], head[p + 10]); break
            if ln <= 0:
                break
            p += ln
        out.append((off, total, key))
        off += total


def field(data):
    h = eccodes.codes_new_from_message(data)
    try:
        v = eccodes.codes_get_values(h).reshape(NJ, NI)
    finally:
        eccodes.codes_release(h)
    v = np.where(v > 100.5, np.nan, v)                    # 9999 = hors domaine du modèle
    return v[::-1]                                       # du sud au nord


def run_arome(put):
    t0 = time.time()
    run = latest_run()
    print("AROME : run", run.isoformat())
    # Fenêtre de la France dans la grille (du sud au nord), points d'angle inclus
    i0 = int(round((BOX["bottom"] - (LAT_N - (NJ - 1) * STEP)) / STEP))
    j0 = int(round((BOX["left"] - LON_W) / STEP))
    ni_, nj_ = int(round((BOX["right"] - BOX["left"]) / STEP)), int(round((BOX["top"] - BOX["bottom"]) / STEP))
    shape = (nj_ // 2, ni_ // 2)
    cube = np.full((HOURS + 1, 4) + shape, 255, np.uint8)

    def work(h):
        u = url(run, h)
        msgs = {k: (o, n) for o, n, k in messages(u) if k in WANT}
        if len(msgs) < 3:
            return h, None
        out = {}
        for k, (o, n) in msgs.items():
            v = field(get(u, (o, o + n - 1)))[i0:i0 + nj_, j0:j0 + ni_]
            v = v.reshape(shape[0], 2, shape[1], 2).mean(axis=(1, 3)) / 100          # 0,02° : moyenne de 2 × 2
            out[WANT[k]] = v
        return h, out

    with cf.ThreadPoolExecutor(8) as ex:
        for h, out in ex.map(work, range(HOURS + 1)):
            if out is None:
                print("AROME : échéance", h, "incomplète")
                continue
            lo, mi, hi = out[1], out[2], out[3]
            tot = 1 - (1 - lo) * (1 - mi) * (1 - hi)
            for vi, v in enumerate((tot, lo, mi, hi)):
                g = np.where(np.isnan(v), 255, np.clip(np.rint(v * 100), 0, 100)).astype(np.uint8)
                cube[h, vi] = g
    print("AROME : téléchargé et regrillé en", round(time.time() - t0), "s")
    ref_unix = int(run.timestamp())
    n = int(round(TILE / RES))
    files = {}
    for la in range(BOX["bottom"], BOX["top"], TILE):
        for lo in range(BOX["left"], BOX["right"], TILE):
            a = int(round((la - BOX["bottom"]) / RES)); b = int(round((lo - BOX["left"]) / RES))
            block = cube[:, :, a:a + n, b:b + n]
            if block.shape[2] < n or block.shape[3] < n or (block == 255).all():
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, HOURS + 1, 4, float(la), float(lo), RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{PREFIX}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    meta = {"model": "AROME 0,01°", "source": "Météo-France", "licence": "Licence Ouverte 2.0",
            "reference": run.isoformat(), "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "variables": ["CLCT (calculée)", "CLCL", "CLCM", "CLCH"], "hours": HOURS + 1, "res": RES,
            "box": BOX, "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    put(files)
    print("AROME : terminé en", round(time.time() - t0), "s,", len(files), "fichiers,",
          round(sum(len(v) for v in files.values()) / 1e6, 1), "Mo")
