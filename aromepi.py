"""AROME Prévision Immédiate (Météo-France, relancé toutes les heures, 6 h d'échéance) → fc1/pi/ pour l'app.

Nuages des 6 prochaines heures, plus frais que les autres modèles (run de l'heure) :
CLCT (NEBUL), CLCL (0,01°), CLCM, CLCH (0,025°) en %, CDCT = altitude du sommet des nuages (pression du sommet
convertie en altitude, pas de 20 m, 0 = pas de nuage) — même disposition que fc1/ch2/ (lu par le même code).
Morceaux 1° « LCFC » sur la grille 0,025° (40 × 40), toutes les heures de run+1 h à run+6 h.
API WCS du portail (clé : secret GitHub MF_AROMEPI_KEY, jamais affichée). Licence Ouverte 2.0 (« Météo-France »).
"""
import datetime as dt
import json, os, re, struct, time, urllib.request, urllib.error, zlib

import numpy as np
import eccodes

B = "https://public-api.meteofrance.fr/public/aromepi/1.0/wcs"
W25, W01 = "MF-NWP-HIGHRES-AROMEPI-0025-FRANCE-WCS", "MF-NWP-HIGHRES-AROMEPI-001-FRANCE-WCS"
FIELDS = [("CLCT", W25, "NEBUL__GROUND_OR_WATER_SURFACE"),
          ("CLCL", W01, "LOW_CLOUD_COVER__GROUND_OR_WATER_SURFACE"),
          ("CLCM", W25, "MEDIUM_CLOUD_COVER__GROUND_OR_WATER_SURFACE"),
          ("CLCH", W25, "HIGH_CLOUD_COVER__GROUND_OR_WATER_SURFACE"),
          ("CDCT", W25, "PRESSURE__LEVEL_OF_CLOUD_TOP")]
LAT0, LAT1, LON0, LON1 = 41, 52, -6, 11          # France + Genève / Suisse romande (morceaux 1°)
RES = 0.025
HOURS = 6
PREFIX = "fc1/pi/"
PAUSE = 1.3                                       # ≤ 46 requêtes / minute


def key():
    return (os.environ.get("MF_AROMEPI_KEY") or "").strip()


def get(url, k, tries=4):
    for n in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"apikey": k, "accept": "*/*"}), timeout=120) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"AROME-PI : accès refusé ({e.code}) — vérifier MF_AROMEPI_KEY")
            if n == tries - 1:
                raise
            time.sleep(15 if e.code == 429 else 4)
        except Exception:
            if n == tries - 1:
                raise
            time.sleep(4)


def latest_run(k):
    """Dernier run dont la nébulosité, les nuages bas et le sommet sont tous publiés."""
    caps = {}
    for w in (W25, W01):
        txt = get(f"{B}/{w}/GetCapabilities?service=WCS&version=2.0.1&language=eng", k).decode("utf-8", "replace")
        caps[w] = set(re.findall(r"<wcs:CoverageId>([^<]+)</wcs:CoverageId>", txt))
        time.sleep(PAUSE)
    runs = None
    for _, w, f in FIELDS:
        r = {i.split("___")[1] for i in caps[w] if i.startswith(f + "___")}
        runs = r if runs is None else runs & r
    if not runs:
        raise SystemExit("AROME-PI : aucun run complet")
    return sorted(runs)[-1]


def field(k, w, f, run, t):
    u = (f"{B}/{w}/GetCoverage?service=WCS&version=2.0.1&coverageid={f}___{run}"
         f"&subset=time({t:%Y-%m-%dT%H:%M:%SZ})&subset=lat({LAT0},{LAT1})&subset=long({LON0},{LON1})&format=application/wmo-grib")
    raw = get(u, k)
    h = eccodes.codes_new_from_message(raw)
    try:
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        la1, lo1 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees"), eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
        di, dj = eccodes.codes_get(h, "iDirectionIncrementInDegrees"), eccodes.codes_get(h, "jDirectionIncrementInDegrees")
        jpos = eccodes.codes_get(h, "jScansPositively")
        eccodes.codes_set(h, "missingValue", 1e20)
        v = eccodes.codes_get_values(h).reshape(nj, ni)
    finally:
        eccodes.codes_release(h)
    v = np.where(v > 1e19, np.nan, v)
    lo1 = (lo1 + 180) % 360 - 180                   # le GRIB donne 354° pour −6° : sans ça, toutes les colonnes valaient la 1re
    if not jpos:                                    # nord → sud : on remet du sud au nord
        v = v[::-1]; la1 = la1 - dj * (nj - 1)
    # Échantillonnage au plus proche sur la grille cible (0,025°, points aux multiples de 0,025)
    glat = LAT0 + RES * np.arange(int(round((LAT1 - LAT0) / RES)))
    glon = LON0 + RES * np.arange(int(round((LON1 - LON0) / RES)))
    ii = np.clip(np.rint((glat - la1) / dj).astype(int), 0, nj - 1)
    jj = np.clip(np.rint((glon - lo1) / di).astype(int), 0, ni - 1)
    return v[np.ix_(ii, jj)]


def run_aromepi(put):
    k = key()
    if not k:
        print("AROME-PI : pas de clé, étape sautée"); return
    t0 = time.time()
    run = latest_run(k)
    r = dt.datetime.strptime(run, "%Y-%m-%dT%H.%M.%SZ").replace(tzinfo=dt.timezone.utc)
    print("AROME-PI : run", r.isoformat())
    ny, nx = int(round((LAT1 - LAT0) / RES)), int(round((LON1 - LON0) / RES))
    cube = np.full((HOURS, len(FIELDS), ny, nx), 255, np.uint8)
    for hi in range(HOURS):
        t = r + dt.timedelta(hours=hi + 1)
        for vi, (name, w, f) in enumerate(FIELDS):
            time.sleep(PAUSE)
            try:
                v = field(k, w, f, run, t)
            except SystemExit:
                raise
            except Exception as e:
                print("AROME-PI :", name, t, "indisponible :", e); continue
            if name == "CDCT":
                # Pression du sommet (Pa) → altitude standard (m) ; pas de nuage = 0
                p = np.where(np.isfinite(v) & (v > 1000), v, np.nan)
                z = 44330.8 * (1 - (p / 101325.0) ** 0.190263)
                g = np.where(np.isfinite(z), np.clip(np.rint(z / 20), 1, 250), 0)
            else:
                g = np.where(np.isfinite(v), np.clip(np.rint(v), 0, 100), 255)
            cube[hi, vi] = g.astype(np.uint8)
    # Pas de nuage total → pas de sommet (évite un sommet fantôme)
    cube[:, 4][cube[:, 0] == 0] = 0
    print("AROME-PI : téléchargé en", round(time.time() - t0), "s")
    ref_unix = int((r + dt.timedelta(hours=1)).timestamp())
    n = int(round(1 / RES))
    files = {}
    for la in range(LAT0, LAT1):
        for lo in range(LON0, LON1):
            i0, j0 = int(round((la - LAT0) / RES)), int(round((lo - LON0) / RES))
            block = cube[:, :, i0:i0 + n, j0:j0 + n]
            if (block[:, 0] == 255).all():
                continue
            hdr = b"LCFC" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3600, HOURS, len(FIELDS),
                                       la - RES / 2, lo - RES / 2, RES, n, n)
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{PREFIX}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    meta = {"model": "AROME-PI (prévision immédiate)", "source": "Météo-France", "licence": "Licence Ouverte 2.0",
            "reference": r.isoformat(), "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "variables": [f[0] for f in FIELDS], "cdct_step_m": 20, "hours": HOURS, "tiles": len(files)}
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    print("AROME-PI :", len(files), "fichiers,", round(sum(map(len, files.values())) / 1e6, 1), "Mo")
    put(files)


if __name__ == "__main__":
    import lucarne_forecast
    run_aromepi(lucarne_forecast.upload)
