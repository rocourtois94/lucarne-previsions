"""Vérification des modèles de nuages contre le satellite, chaque heure de nuit → fc1/verif/scores.json

Pour l'heure de la dernière image Meteosat (fc1/satm/, heure 0 = nuages mesurés), on compare en ≈ 3 500 points
(tous les 0,25° sur 41–51° N, 5° O–17° E) la couverture prévue par chaque modèle pour cette heure à la couverture
mesurée : MeteoSwiss ICON-CH1 et ICON-CH2, Météo-France AROME et AROME-PI, ECMWF. Seulement là où il fait nuit
(Soleil sous −6°) : le masque nuageux de jour peut confondre neige et nuages.

Scores par case de 1° : erreur absolue moyenne (0 à 1) et taux de bonne réponse « dégagé / couvert » (seuil 30 %),
avec un oubli progressif (× 0,97 à chaque heure vérifiée : les ≈ 2 à 3 dernières nuits comptent le plus).
L'app s'en sert pour donner plus de poids, case par case, au modèle qui a eu raison récemment.
"""
import datetime as dt, io, json, math, struct, zlib
import numpy as np

KEY = "fc1/verif/scores.json"
LAT0, LAT1, LON0, LON1, STEP = 41.0, 51.0, -5.0, 17.0, 0.25
DECAY = 0.97
CLEAR = 0.30
MODELS = {   # nom : (préfixe, taille des morceaux en degrés)
    "ch1": ("fc1/ch1/", 1), "ch2": ("fc1/ch2/", 1), "arome": ("fc1/arome/", 1), "pi": ("fc1/pi/", 1), "ec": ("fc1/ec/", 5)}


def parse(b):
    if not b or b[:4] != b"LCFC":
        return None
    ver, run, step, hours, nv, lat, lon, res, rows, cols = struct.unpack("<HqIHHfffHH", b[4:38])
    d = np.frombuffer(zlib.decompress(b[38:], -15), np.uint8).reshape(hours, nv, rows, cols)
    return dict(run=run, step=step, hours=hours, lat=lat, lon=lon, res=res, rows=rows, cols=cols, d=d)


def cell(t, la, lo):
    i = int(math.floor((la - t["lat"]) / t["res"] + 1e-9)); j = int(math.floor((lo - t["lon"]) / t["res"] + 1e-9))
    # morceaux dont les centres sont sur les degrés ronds (AROME-PI, ECMWF) : rattacher la demi-case du bord
    i = min(max(i, 0), t["rows"] - 1); j = min(max(j, 0), t["cols"] - 1)
    return i, j


def value(t, la, lo, when, var=0):
    """Couverture (0–1) à l'heure la plus proche, None si hors domaine ou hors échéances"""
    x = (when - t["run"]) / t["step"]
    h = int(round(x))
    if h < 0 or h >= t["hours"] or abs(x - h) > 0.5:
        return None
    i, j = cell(t, la, lo)
    b = int(t["d"][h, var, i, j])
    return None if b > 100 else b / 100


def sun_alt(la, lo, when):
    d = dt.datetime.fromtimestamp(when, dt.timezone.utc)
    n = (d - dt.datetime(2000, 1, 1, 12, tzinfo=dt.timezone.utc)).total_seconds() / 86400
    L = math.radians((280.46 + 0.9856474 * n) % 360); g = math.radians((357.528 + 0.9856003 * n) % 360)
    lam = L + math.radians(1.915) * math.sin(g) + math.radians(0.02) * math.sin(2 * g)
    eps = math.radians(23.439 - 4e-7 * n)
    dec = math.asin(math.sin(eps) * math.sin(lam)); ra = math.atan2(math.cos(eps) * math.sin(lam), math.cos(lam))
    gmst = math.radians((280.46061837 + 360.98564736629 * n) % 360)
    H = gmst + math.radians(lo) - ra
    return math.degrees(math.asin(math.sin(math.radians(la)) * math.sin(dec) + math.cos(math.radians(la)) * math.cos(dec) * math.cos(H)))


def run_verif():
    from lucarne_forecast import r2_client, upload
    s3, bucket = r2_client()
    def get(k):
        try: return s3.get_object(Bucket=bucket, Key=k)["Body"].read()
        except Exception: return None
    old = json.loads(get(KEY) or b"{}")
    sat_tiles, mod_tiles = {}, {m: {} for m in MODELS}
    def tile(cache, prefix, size, la, lo):
        k = f"{int(math.floor(la / size) * size)}_{int(math.floor(lo / size) * size)}"
        if k not in cache:
            cache[k] = parse(get(f"{prefix}{k}.bin"))
        return cache[k]
    probe = tile(sat_tiles, "fc1/satm/", 5, 46, 6)
    if probe is None:
        print("Vérification : pas de nuages mesurés"); return
    when = probe["run"]
    if old.get("last") == when:
        print("Vérification : heure", dt.datetime.fromtimestamp(when, dt.timezone.utc), "déjà vérifiée"); return
    cells = old.get("cells", {})
    # Oubli progressif
    for c in cells.values():
        for m, v in c.items():
            v[0] *= DECAY; v[1] *= DECAY; v[2] *= DECAY
    pts = 0
    for la in np.arange(LAT0 + STEP / 2, LAT1, STEP):
        for lo in np.arange(LON0 + STEP / 2, LON1, STEP):
            if sun_alt(la, lo, when) > -6:
                continue
            st = tile(sat_tiles, "fc1/satm/", 5, la, lo)
            if st is None:
                continue
            obs = value(st, la, lo, when)
            if obs is None:
                continue
            pts += 1
            key = f"{int(math.floor(la))}_{int(math.floor(lo))}"
            c = cells.setdefault(key, {})
            for m, (prefix, size) in MODELS.items():
                t = tile(mod_tiles[m], prefix, size, la, lo)
                if t is None:
                    continue
                f = value(t, la, lo, when)
                if f is None:
                    continue
                v = c.setdefault(m, [0.0, 0.0, 0.0])            # poids, somme des erreurs, somme des bonnes réponses
                v[0] += 1; v[1] += abs(f - obs); v[2] += 1.0 if (f < CLEAR) == (obs < CLEAR) else 0.0
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "last": when,
           "decay": DECAY, "clear": CLEAR, "fields": ["poids", "erreur_totale", "bonnes_reponses"],
           "cells": {k: {m: [round(x, 2) for x in v] for m, v in c.items()} for k, c in cells.items()}}
    # Résumé lisible dans le journal (zone Genève – Annecy)
    g = cells.get("46_6", {})
    print("Vérification :", dt.datetime.fromtimestamp(when, dt.timezone.utc).isoformat(), pts, "points de nuit ;",
          " ".join(f"{m} {100 * v[1] / v[0]:.0f}%" for m, v in g.items() if v[0] > 0) or "Genève : pas de point")
    upload({KEY: json.dumps(out, separators=(",", ":")).encode(), "fc1/verif/latest.json": b'{"ok":1}'})


if __name__ == "__main__":
    run_verif()
