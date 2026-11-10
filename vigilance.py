"""Vigilance météorologique Météo-France (France métropolitaine) → fc1/vigilance.json pour l'app.

API « Données publiques Vigilance » du portail https://portail-api.meteofrance.fr (accès avec compte, gratuit,
60 requêtes/minute). La clé est un secret GitHub : MF_VIGILANCE_KEY (créée par le propriétaire du dépôt sur le
portail ; jamais écrite dans le code). Sans clé, l'étape est simplement sautée.

Sortie (petite, lue par l'app) :
{"updated": "...", "source": "Météo-France", "periods": [{"begin": "...", "end": "...",
  "depts": {"74": {"max": 2, "ph": {"3": 2, "5": 1}}, ...}}]}
Couleurs : 1 vert, 2 jaune, 3 orange, 4 rouge. Phénomènes : 1 vent, 2 pluie-inondation, 3 orages, 4 crues,
5 neige-verglas, 6 canicule, 7 grand froid, 8 avalanches, 9 vagues-submersion.
"""
import datetime as dt
import json, os, time, urllib.request

BASE = "https://public-api.meteofrance.fr/public/DPVigilance/v1"
OUT = "fc1/vigilance.json"


def get(path, key):
    req = urllib.request.Request(BASE + path, headers={"apikey": key, "accept": "application/json"})
    for k in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise SystemExit(f"Vigilance : accès refusé ({e.code}) — vérifier la clé MF_VIGILANCE_KEY et l'abonnement à l'API")
            if k == 2:
                raise
            time.sleep(5)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            # Serveur Météo-France momentanément injoignable (ex. 10/11/2026 vers 00h20 UTC : « Connection refused ») :
            # on réessaie un peu, puis on abandonne sans faire échouer tout le calcul (relancé chaque heure)
            if k == 2:
                return None
            time.sleep(20)


def parse(doc):
    """Lit la carte de vigilance en cours (structure documentée du produit « cartevigilance/encours »)."""
    product = doc.get("product", doc)
    periods = []
    for per in product.get("periods", []):
        depts = {}
        for d in (per.get("timelaps", {}) or {}).get("domain_ids", []) or []:
            did = str(d.get("domain_id", ""))
            if not did or len(did) > 3 or did.startswith("99"):      # départements seulement (pas les zones côtières)
                continue
            ph = {}
            for it in d.get("phenomenon_items", []) or []:
                try:
                    ph[str(it["phenomenon_id"])] = int(it.get("phenomenon_max_color_id", 1))
                except (KeyError, ValueError, TypeError):
                    continue
            mx = int(d.get("max_color_id", max(ph.values(), default=1)))
            depts[did] = {"max": mx, "ph": {k: v for k, v in ph.items() if v >= 2}}
        periods.append({"begin": per.get("begin_validity_time"), "end": per.get("end_validity_time"), "depts": depts})
    return periods


def run_vigilance(put):
    key = os.environ.get("MF_VIGILANCE_KEY", "").strip()
    if not key:
        print("Vigilance : pas de clé MF_VIGILANCE_KEY, étape sautée")
        return
    doc = get("/cartevigilance/encours", key)
    if doc is None:
        print("Vigilance : Météo-France injoignable pour l'instant ; carte précédente conservée, nouvel essai à la prochaine heure")
        return
    periods = parse(doc)
    n = sum(len(p["depts"]) for p in periods)
    if n == 0:
        print("Vigilance : structure inattendue, clés reçues :", list((doc.get("product", doc) or {}).keys())[:20])
        return
    prod = doc.get("product", doc) or {}
    # Licence : la source et la date de la dernière mise à jour des données doivent être affichées
    out = {"updated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "mf_update": prod.get("update_time") or doc.get("update_time"), "source": "Météo-France",
           "licence": "Licence Ouverte 2.0", "periods": periods}
    put({OUT: json.dumps(out, separators=(",", ":")).encode()})
    alerts = sorted({(d, v["max"]) for p in periods for d, v in p["depts"].items() if v["max"] >= 2})
    print("Vigilance :", len(periods), "périodes,", n, "départements ; jaune ou plus :", alerts[:12])


if __name__ == "__main__":
    # Passage seul (toutes les heures, workflow « vigilance.yml »)
    import lucarne_forecast
    run_vigilance(lucarne_forecast.upload)
