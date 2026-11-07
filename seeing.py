"""Seeing (turbulence) estimé à partir des vents et températures en altitude du modèle GFS (NOAA, domaine public).

Méthode (modèle de Dewan et al. 1993, utilisé par de nombreuses prévisions de seeing) :
  pour chaque couche entre deux niveaux de pression du modèle, au-dessus du sol du lieu,
  - gradient de température potentielle dθ/dz et cisaillement du vent S = |ΔV| / Δz ;
  - échelle externe de la turbulence : L0^(4/3) = 0,1^(4/3) × 10^(1,64 + 42·S) (troposphère),
                                        10^(0,506 + 50·S) (stratosphère, au-dessus de la tropopause) ;
  - Cn² = 2,8 × (79·10⁻⁶ · P / (T·θ) · dθ/dz)² × L0^(4/3)   (P en hPa) ;
  puis J = Σ Cn²·Δz, r0 = (0,423 k² J)^(−3/5) à 500 nm, seeing = 0,98 λ / r0 (au zénith, en secondes d'arc).
Le modèle global (≈ 25 km) ne voit pas la couche proche du sol (toits, bise, inversion locale) : on ajoute une
contribution moyenne de couche limite (0,9″ par vent faible, davantage avec le vent à 10 m), en quadrature
(seeing ∝ J^(3/5)). C'est une estimation, pas une mesure ; elle donne surtout les bonnes et mauvaises nuits.

Sortie : morceaux de 5° × 5° « LCSE » (pas de 0,25°, toutes les 3 h jusqu'à 120 h) : seeing en 0,05″ (u8)
et vent au niveau du courant-jet (max 300–200 hPa, m/s, u8).
"""
import concurrent.futures as cf
import datetime as dt
import struct, time, urllib.parse, urllib.request, zlib

import numpy as np
import eccodes

LEVELS = [1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450, 400, 350, 300, 250, 200, 150, 100, 70, 50]
BOX = dict(top=72, bottom=35, left=-25, right=45)       # Europe (bords sur des multiples de 5°)
STEPS = list(range(0, 121, 3))
TILE = 5
PREFIX = "fc1/seeing/"


def latest_gfs_run():
    """Dernier run GFS dont l'échéance 120 h est publiée (fichier d'index présent sur le miroir AWS NOAA)."""
    now = dt.datetime.now(dt.timezone.utc)
    for back in range(0, 30, 6):
        t = now - dt.timedelta(hours=back)
        run = t.replace(hour=t.hour // 6 * 6, minute=0, second=0, microsecond=0)
        u = (f"https://noaa-gfs-bdp-pds.s3.amazonaws.com/gfs.{run:%Y%m%d}/{run:%H}/atmos/"
             f"gfs.t{run:%H}z.pgrb2.0p25.f120.idx")
        try:
            with urllib.request.urlopen(u, timeout=20) as r:
                if r.status == 200:
                    return run
        except Exception:
            pass
    raise SystemExit("Aucun run GFS complet trouvé")


def filter_url(run, step):
    q = {"dir": f"/gfs.{run:%Y%m%d}/{run:%H}/atmos", "file": f"gfs.t{run:%H}z.pgrb2.0p25.f{step:03d}",
         "var_UGRD": "on", "var_VGRD": "on", "var_TMP": "on", "var_HGT": "on", "var_TCDC": "on", "subregion": "",
         "toplat": BOX["top"], "bottomlat": BOX["bottom"], "leftlon": BOX["left"], "rightlon": BOX["right"]}
    for l in LEVELS:
        q[f"lev_{l}_mb"] = "on"
    q["lev_10_m_above_ground"] = "on"
    q["lev_surface"] = "on"
    q["lev_entire_atmosphere"] = "on"                     # nébulosité totale (carte du meilleur ciel)
    return "https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl?" + urllib.parse.urlencode(q)


def fetch(url, tries=5):
    for k in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(5 * (k + 1))


def decode(data):
    """{(nom, niveau): champ 2D} ; niveaux en hPa, 'sfc' pour la surface, 10 pour 10 m."""
    out, pos, shape = {}, 0, None
    while pos < len(data):
        h = eccodes.codes_new_from_message(data[pos:])
        n = eccodes.codes_get(h, "totalLength")
        name = eccodes.codes_get(h, "shortName")
        tl = eccodes.codes_get(h, "typeOfLevel")
        lev = eccodes.codes_get(h, "level")
        ni, nj = eccodes.codes_get(h, "Ni"), eccodes.codes_get(h, "Nj")
        step_type = eccodes.codes_get(h, "stepType")
        v = eccodes.codes_get_values(h).reshape(nj, ni)
        if shape is None:
            lat1 = eccodes.codes_get(h, "latitudeOfFirstGridPointInDegrees")
            lat2 = eccodes.codes_get(h, "latitudeOfLastGridPointInDegrees")
            lon1 = eccodes.codes_get(h, "longitudeOfFirstGridPointInDegrees")
            shape = (nj, ni, lat1, lat2, lon1)
        if name == "tcc" and tl in ("atmosphere", "entireAtmosphere"):
            if step_type == "instant":                    # on garde la valeur à l'heure dite, pas la moyenne sur 3–6 h
                out[("tcc", "atm")] = v
        elif name != "tcc":
            out[(name, "sfc" if tl == "surface" else lev)] = v
        eccodes.codes_release(h)
        pos += n
    return out, shape


def seeing_field(f):
    """Seeing (″) et vent max 300–200 hPa (m/s) sur la grille."""
    g = 9.80665
    zs = f[("orog", "sfc")] if ("orog", "sfc") in f else f.get(("gh", "sfc"), None)
    if zs is None:
        zs = np.zeros_like(f[("gh", 500)])
    levs = [l for l in LEVELS if ("gh", l) in f and ("t", l) in f and ("u", l) in f and ("v", l) in f]
    J = np.zeros_like(zs, dtype=float)
    for a, b in zip(levs[:-1], levs[1:]):                 # a : niveau du bas (pression plus forte)
        z1, z2 = f[("gh", a)], f[("gh", b)]
        dz = np.maximum(z2 - z1, 1.0)
        above = z1 > zs + 30                              # couche au-dessus du sol du lieu
        T1, T2 = f[("t", a)], f[("t", b)]
        th1, th2 = T1 * (1000 / a) ** 0.286, T2 * (1000 / b) ** 0.286
        dthdz = (th2 - th1) / dz
        S = np.hypot(f[("u", b)] - f[("u", a)], f[("v", b)] - f[("v", a)]) / dz
        P = (a + b) / 2; T = (T1 + T2) / 2; th = (th1 + th2) / 2
        strat = dthdz > 0.01                              # forte stabilité : stratosphère
        L43 = np.where(strat, 10 ** (0.506 + 50 * S), 10 ** (1.64 + 42 * S)) * 0.1 ** (4 / 3)
        M = 79e-6 * P / (T * th) * dthdz
        Cn2 = 2.8 * M ** 2 * L43
        J += np.where(above, Cn2 * dz, 0.0)
    lam = 500e-9; k = 2 * np.pi / lam
    J = np.maximum(J, 1e-16)
    r0 = (0.423 * k ** 2 * J) ** (-3 / 5)
    free = 0.98 * lam / r0 * 206265
    # Couche limite (non résolue par le modèle) : contribution moyenne, croissante avec le vent à 10 m
    w10 = np.hypot(f.get(("10u", 10), 0 * zs), f.get(("10v", 10), 0 * zs))
    bl = 0.9 + 0.08 * np.clip(w10, 0, 15)
    total = (free ** (5 / 3) + bl ** (5 / 3)) ** (3 / 5)
    jet = np.maximum.reduce([np.hypot(f[("u", l)], f[("v", l)]) for l in (300, 250, 200) if ("u", l) in f])
    return total, jet, free


def run_seeing(put):
    t0 = time.time()
    run = latest_gfs_run()
    print("Seeing : run GFS", run.isoformat())
    results = {}

    def work(step):
        data = fetch(filter_url(run, step))
        f, shape = decode(data)
        s, j, fr = seeing_field(f)
        return step, s, j, f.get(("tcc", "atm")), shape, fr

    with cf.ThreadPoolExecutor(4) as ex:                  # NOMADS limite le nombre de requêtes
        for step, s, j, c, shape, fr in ex.map(work, STEPS):
            results[step] = (s, j, c, fr)
            grid = shape
    nj, ni, lat1, lat2, lon1 = grid
    # Lignes dans l'ordre du fichier (sud→nord ou nord→sud selon le sens de balayage) ; on remet du sud au nord
    lats = np.linspace(lat1, lat2, nj)
    order = np.argsort(lats)
    lats = lats[order]
    lons = ((lon1 + 0.25 * np.arange(ni)) + 180) % 360 - 180
    cube = np.zeros((len(STEPS), 4, nj, ni), np.uint8)
    for si, st in enumerate(STEPS):
        s, j, c, fr = results[st]
        cube[si, 0] = np.clip(np.rint(s[order] / 0.05), 0, 254)
        cube[si, 1] = np.clip(np.rint(j[order]), 0, 254)
        cube[si, 2] = 255 if c is None else np.clip(np.rint(c[order]), 0, 100)     # nuages %, 255 = inconnu
        cube[si, 3] = np.clip(np.rint(fr[order] / 0.05), 0, 254)    # seeing de l'atmosphère libre (sans la couche limite)
    print("Seeing calculé en", round(time.time() - t0), "s ; Saint-Julien +12 h :",
          round(float(results[12][0][order][np.argmin(abs(lats - 46.14)), np.argmin(abs(lons - 6.08))]), 2), "″")
    files = {}
    ref_unix = int(run.timestamp())
    for la in range(BOX["bottom"], BOX["top"], TILE):
        for lo in range(BOX["left"], BOX["right"], TILE):
            i0 = int(np.searchsorted(lats, la - 1e-6)); j0 = int(np.searchsorted(lons, lo - 1e-6))
            n = TILE * 4 + 1                              # bords inclus (interpolation sans couture)
            block = cube[:, :, i0:i0 + n, j0:j0 + n]
            if block.shape[2] < 2 or block.shape[3] < 2:
                continue
            hdr = b"LCSE" + struct.pack("<HqIHHfffHH", 1, ref_unix, 3 * 3600, len(STEPS), 4,
                                       float(lats[i0]), float(lons[j0]), 0.25, block.shape[2], block.shape[3])
            c = zlib.compressobj(9, zlib.DEFLATED, -15)
            files[f"{PREFIX}{la}_{lo}.bin"] = hdr + c.compress(np.ascontiguousarray(block).tobytes()) + c.flush()
    meta = {"model": "GFS 0,25°", "source": "NOAA/NCEP (domaine public)", "method": "Dewan 1993 + couche limite moyenne",
            "reference": run.isoformat(), "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "steps_h": 3, "variables": ["seeing_0.05arcsec", "jet_m_s", "TCDC_percent", "free_seeing_0.05arcsec"], "tiles": len(files)}
    import json
    files[PREFIX + "latest.json"] = json.dumps(meta).encode()
    put(files)
    print("Seeing : terminé en", round(time.time() - t0), "s,", len(files), "fichiers")
