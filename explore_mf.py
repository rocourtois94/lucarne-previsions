"""Repérage (lancé à la main) des API Météo-France PE-AROME et Données d'observation.
N'écrit jamais la clé : seuls les codes de réponse et des extraits de contenu sont affichés."""
import json, math, os, re, urllib.request, urllib.error
from collections import Counter

KEY = os.environ.get("MF_VIGILANCE_KEY", "").strip()
BASE = "https://public-api.meteofrance.fr/public"
SITE = (46.144, 6.081)   # Saint-Julien-en-Genevois

def get(url):
    req = urllib.request.Request(url, headers={"apikey": KEY, "accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]
    except Exception as e:
        return 0, str(e).encode()[:200]

def show(tag, code, body, n=300):
    print(f"[{code}] {tag} ({len(body)} o) :", body[:n].decode("utf-8", "replace").replace("\n", " "))

print("=== PE-AROME ===")
cands = [f"MF-NWP-HIGHRES-PEAROME-{g}-{d}-WCS" for g in ("0025", "001") for d in ("FRANCE",)]
for wcs in cands:
    url = f"{BASE}/pearome/1.0/wcs/{wcs}/GetCapabilities?service=WCS&version=2.0.1&language=eng"
    code, body = get(url)
    if code == 400:
        show(wcs, code, body, 400)
        for v in ("?service=WCS&version=2.0.1", "?service=WCS&version=2.0.1&language=fre"):
            c2, b2 = get(f"{BASE}/pearome/1.0/wcs/{wcs}/GetCapabilities{v}")
            show(wcs + v, c2, b2, 300)
            if c2 == 200:
                code, body = c2, b2
                break
    if code != 200:
        print(f"[{code}] {wcs}")
        continue
    txt = body.decode("utf-8", "replace")
    ids = re.findall(r"<wcs:CoverageId>([^<]+)</wcs:CoverageId>", txt)
    print(f"[200] {wcs} : {len(ids)} couvertures")
    fam = Counter(re.sub(r"___\d{4}-\d\d-\d\dT\d\d\.\d\d\.\d\dZ.*$", "", i) for i in ids)
    for k, v in sorted(fam.items()):
        print("    ", k, "×", v)
    runs = sorted({m for i in ids for m in re.findall(r"\d{4}-\d\d-\d\dT\d\d\.\d\d\.\d\dZ", i)})
    print("     runs :", runs[-6:])
for p in ("/pearome/1.0/", "/pearome/1.0/wms/MF-NWP-HIGHRES-PEARO-0025-FRANCE-WMS/GetCapabilities?service=WMS&version=1.3.0&language=eng"):
    show(p, *get(BASE + p), n=200)

print("=== Données d'observation ===")
for p in ("/DPObs/v1/station/horaire?id_station=74182001&format=json",
          "/DPObs/v1/station/infrahoraire-6m?id_station=74182001&format=json",
          "/DPObs/liste-stations", "/DPObs/v1/liste-stations?format=json"):
    show(p, *get(BASE + p), n=300)
code, body = get(f"{BASE}/DPObs/v1/liste-stations")
show("liste-stations", code, body, 200)
if code == 200:
    txt = body.decode("utf-8", "replace")
    rows = []
    try:
        data = json.loads(txt)
        for s in data:
            rows.append((s.get("id"), s.get("nom"), float(s.get("lat")), float(s.get("lon")), s.get("alt")))
    except Exception:
        lines = [l.split(";") for l in txt.splitlines() if l.strip()]
        head = [h.strip().lower() for h in lines[0]]
        print("     colonnes :", head)
        for l in lines[1:]:
            d = dict(zip(head, l))
            try:
                rows.append((d.get("id_station") or d.get("id"), d.get("nom_usuel") or d.get("nom"),
                             float(d.get("latitude") or d.get("lat")), float(d.get("longitude") or d.get("lon")), d.get("altitude") or d.get("alt")))
            except Exception:
                pass
    def km(r):
        dl = math.radians(r[2] - SITE[0]); dg = math.radians(r[3] - SITE[1]) * math.cos(math.radians(SITE[0]))
        return 6371 * math.hypot(dl, dg)
    print("     stations :", len(rows))
    near = sorted(rows, key=km)[:6]
    for r in near:
        print(f"     {r[0]}  {r[1]}  alt {r[4]}  à {km(r):.0f} km")
    if near:
        sid = near[0][0]
        for p in (f"/DPObs/v1/station/horaire?id_station={sid}&format=json",
                  f"/DPObs/v1/station/infrahoraire-6m?id_station={sid}&format=json"):
            show(p, *get(BASE + p), n=900)
