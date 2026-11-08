"""Probabilité de ciel dégagé — ensemble ICON-CH2-EPS de MeteoSwiss (contrôle + 20 membres perturbés).

Pour chaque heure et chaque case de 0,02°, on compte parmi les 21 scénarios :
  PCLR  : ciel dégagé (nébulosité totale < 25 %) ;
  Bxxx  : ciel couvert MAIS sommet des nuages sous xxx m d'altitude (mer de brouillard / stratus).
L'app ajoute à PCLR les scénarios dont les nuages restent sous le lieu (selon l'altitude du lieu),
comme pour la prévision de contrôle. Valeurs = nombre de scénarios (0…21) ; 255 = inconnu.
Sortie : fc1/ch2e/<lat>_<lon>.bin (même format « LCFC » que fc1/ch2/) + fc1/ch2e/latest.json.
Données : MeteoSwiss, licence CC BY 4.0.
"""
import concurrent.futures as cf
import datetime as dt
import json, struct, time, zlib

import numpy as np
import eccodes

CLEAR = 25                                   # % de nébulosité totale sous lequel on considère le ciel dégagé
TOPS = [500, 700, 900, 1100, 1400, 1800]     # seuils d'altitude du sommet des nuages (m)
NAMES = ["PCLR"] + [f"B{t}" for t in TOPS]
PREFIX_E = "fc1/ch2e/"


def perturbed_urls(L, ref, var):
    feats = L.search({"collections": [L.COLL], "forecast:variable": var, "forecast:perturbed": True,
                      "forecast:reference_datetime": ref, "limit": 200})
    urls = {}
    for f in feats:
        p = f["properties"]
        if not p.get("forecast:perturbed") or p.get("forecast:variable") != var or not f.get("assets"):
            continue
        hz = p["forecast:horizon"]
        step = int(hz[1:hz.index("D")]) * 24 + int(hz[hz.index("T") + 1:hz.index("H")])
        urls[step] = next(iter(f["assets"].values()))["href"]
    return urls


def members(raw):
    """Valeurs de chaque membre d'un fichier GRIB multi-messages → {numéro: tableau}."""
    out, pos = {}, 0
    while pos < len(raw):
        h = eccodes.codes_new_from_message(raw[pos:])
        try:
            out[eccodes.codes_get(h, "perturbationNumber")] = eccodes.codes_get_values(h)
            pos += eccodes.codes_get(h, "totalLength")
        finally:
            eccodes.codes_release(h)
    return out


def run_ensemble(L, ref, idx, far, cube, lat0, lat1, lon0, lon1):
    t0 = time.time()
    nh = cube.shape[0]
    uc, ut = perturbed_urls(L, ref, "CLCT"), perturbed_urls(L, ref, "CDCT")
    hours = [h for h in range(nh) if h in uc and h in ut]
    print("Ensemble :", len(hours), "échéances")
    out = np.full((nh, len(NAMES)) + idx.shape, 255, np.uint8)

    def work(h):
        mc, mt = members(L.download(uc[h])), members(L.download(ut[h]))
        # Membre de contrôle (déjà dans le cube : CLCT en %, CDCT par pas de 20 m, 0 = pas de nuage)
        cc = cube[h, 0].astype(np.float32); ct = cube[h, 4].astype(np.float32) * L.TOP_STEP
        ok = (cube[h, 0] != 255)
        clr = (cc < CLEAR).astype(np.uint8)
        below = [((cc >= CLEAR) & (ct > 0) & (ct < t)).astype(np.uint8) for t in TOPS]
        n = 1
        for m in mc:
            if m not in mt:
                continue
            c = mc[m][idx]; top = mt[m][idx]
            clr += (c < CLEAR)
            for k, t in enumerate(TOPS):
                below[k] += (c >= CLEAR) & (top > 0) & (top < t)
            n += 1
        g = np.stack([clr] + below)
        g[:, far | ~ok] = 255
        return h, g, n

    nm = 0
    with cf.ThreadPoolExecutor(8) as ex:
        for h, g, n in ex.map(work, hours):
            out[h] = g; nm = max(nm, n)
    print("Ensemble : téléchargé en", round(time.time() - t0), "s,", nm, "scénarios")

    ref_unix = int(dt.datetime.fromisoformat(ref.replace("Z", "+00:00")).timestamp())
    n = int(round(L.TILE / L.RES))
    files = {}
    for la in np.arange(lat0, lat1 + 1, L.TILE):
        for lo in np.arange(lon0, lon1 + 1, L.TILE):
            i0 = int(round((la - lat0) / L.RES)); j0 = int(round((lo - lon0) / L.RES))
            block = out[:, :, i0:i0 + n, j0:j0 + n]
            if block.shape[2] < n or block.shape[3] < n or (block == 255).all():
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, nh, len(NAMES), float(la), float(lo), L.RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{L.ENS_PREFIX}{int(la)}_{int(lo)}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    meta = {"model": L.MODEL + " (ensemble)", "source": "MeteoSwiss", "licence": "CC BY 4.0", "reference": ref,
            "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "variables": NAMES,
            "members": nm, "clear_below_pct": CLEAR, "tops_m": TOPS, "hours": nh, "tiles": len(files)}
    files[L.ENS_PREFIX + "latest.json"] = json.dumps(meta).encode()
    print("Ensemble :", len(files), "fichiers,", round(sum(map(len, files.values())) / 1e6, 1), "Mo")
    L.upload(files)
