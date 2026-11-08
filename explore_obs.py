import os, urllib.request, urllib.error
KEY = (os.environ.get("MF_OBS_KEY") or os.environ.get("MF_VIGILANCE_KEY") or "").strip()
B = "https://public-api.meteofrance.fr/public/DPObs/v2"
def get(p):
    try:
        with urllib.request.urlopen(urllib.request.Request(B + p, headers={"apikey": KEY, "accept": "*/*"}), timeout=90) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
for p in ("/paquet/horaire?id-departement=74&format=json", "/paquet/horaire?id_departement=74&format=json",
          "/paquet/infrahoraire-6m?id-departement=74&format=json", "/paquet/stations/horaire?format=json",
          "/paquet/stations/infrahoraire-6m?format=json", "/station/horaire?id_station=74182001&format=json"):
    c, b = get(p)
    print(f"[{c}] {p} ({len(b)} o) :", b[:500].decode("utf-8", "replace"))
c, b = get("/liste-stations")
import csv, io
rows = list(csv.reader(io.StringIO(b.decode("iso-8859-1")), delimiter=";"))
from collections import Counter
print("Packs :", Counter(r[-1] for r in rows[1:]))
print("Avec n° OMM :", sum(1 for r in rows[1:] if r[1].strip()))
print([r for r in rows[1:] if r[0].startswith("74")][:30])
