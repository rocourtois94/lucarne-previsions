"""Observations des stations Météo-France (API « Données d'observation » v2) → fc1/obs.json pour l'app.

On garde les stations principales (celles qui ont un indicatif OMM, ~300 en France) : ce sont elles qui mesurent
humidité, vent, visibilité et souvent la nébulosité. Une requête par station, au rythme de 45 par minute
(quota du portail). Clé : secret GitHub MF_OBS_KEY (à défaut MF_VIGILANCE_KEY), jamais affichée.
Licence Ouverte 2.0 : source « Météo-France » et heure de la mesure affichées dans l'app.
"""
import csv, datetime as dt, io, json, os, time, urllib.request, urllib.error

BASE = "https://public-api.meteofrance.fr/public/DPObs/v2"
OUT = "fc1/obs.json"
PAUSE = 60 / 45


def key():
    return (os.environ.get("MF_OBS_KEY") or os.environ.get("MF_VIGILANCE_KEY") or "").strip()


def get(path, k):
    req = urllib.request.Request(BASE + path, headers={"apikey": k, "accept": "*/*"})
    for n in range(4):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"Observations : accès refusé ({e.code}) — vérifier MF_OBS_KEY et l'abonnement")
            if e.code == 404:
                return None
            time.sleep(10 if e.code == 429 else 3)
        except Exception:
            time.sleep(3)
    return None


def num(v, f=lambda x: x, nd=1):
    return None if v is None else round(f(float(v)), nd)


PAQUET = "https://public-api.meteofrance.fr/public/DPPaquetObs/v2"


def paquet():
    """Toutes les stations (~2 000) en une requête : API « Package Observations » (secret MF_PAQUETOBS_KEY).
    Dernière heure complète (ou la précédente si elle n'est pas encore publiée)."""
    k = (os.environ.get("MF_PAQUETOBS_KEY") or "").strip()
    if not k:
        return None
    h = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0)
    for back in (0, 1, 2):
        d = (h - dt.timedelta(hours=back)).strftime("%Y-%m-%dT%H:%M:%SZ")
        req = urllib.request.Request(f"{PAQUET}/paquet/stations/horaire?date={d}&format=json", headers={"apikey": k, "accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                a = json.loads(r.read())
            if isinstance(a, list) and len(a) > 500:
                print("Observations : paquet national,", len(a), "stations à", d)
                return {x.get("geo_id_insee"): x for x in a if x.get("geo_id_insee")}
        except Exception as e:
            print("Observations : paquet", d, "indisponible :", str(e)[:120])
    return None


def run_obs(put):
    k = key()
    if not k:
        print("Observations : pas de clé, étape sautée"); return
    raw = get("/liste-stations", k)
    rows = list(csv.reader(io.StringIO(raw.decode("iso-8859-1")), delimiter=";"))
    t0 = time.time()
    pq = paquet()
    # Paquet : toutes les stations ; sinon une requête par station principale (indicatif OMM), plus lent
    stations = [r for r in rows[1:] if len(r) >= 6 and (pq is not None or r[1].strip())]
    print("Observations :", len(stations), "stations")
    out = []
    for r in stations:
        if pq is not None:
            d = pq.get(r[0])
        else:
            time.sleep(PAUSE)
            b = get(f"/station/horaire?id_station={r[0]}&format=json", k)
            try:
                d = json.loads(b) if b else None
                d = d[-1] if isinstance(d, list) and d else d
            except Exception:
                d = None
        if not isinstance(d, dict) or d.get("t") is None and d.get("n") is None:
            continue
        out.append({"id": r[0], "name": r[2].strip().replace("_SAPC", "").replace("_", " ").title(), "lat": float(r[3]), "lon": float(r[4]), "alt": int(float(r[5] or 0)),
                    "time": d.get("validity_time"),
                    "t": num(d.get("t"), lambda x: x - 273.15), "td": num(d.get("td"), lambda x: x - 273.15),
                    "u": num(d.get("u"), nd=0), "ff": num(d.get("ff"), lambda x: x * 3.6, 0),
                    "raf": num(d.get("raf"), lambda x: x * 3.6, 0), "rr1": num(d.get("rr1")),
                    "n": num(d.get("n"), nd=0), "vv": num(d.get("vv"), lambda x: x / 1000, 1),
                    "pmer": num(d.get("pmer"), lambda x: x / 100, 1)})
    doc = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "source": "Météo-France",
           "licence": "Licence Ouverte 2.0", "stations": out}
    for o in out:                                   # champs vides retirés (fichier plus léger)
        for kk in [kk for kk, v in o.items() if v is None]:
            del o[kk]
    put({OUT: json.dumps(doc, separators=(",", ":"), ensure_ascii=False).encode()})
    print("Observations :", len(out), "stations en", round(time.time() - t0), "s ; avec nébulosité :", sum(1 for s in out if s["n"] is not None))


if __name__ == "__main__":
    import lucarne_forecast
    run_obs(lucarne_forecast.upload)
