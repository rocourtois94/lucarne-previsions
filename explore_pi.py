"""Repérage AROME-PI et Package Observations (manuel). N'affiche jamais la clé."""
import os, re, urllib.request, urllib.error
from collections import Counter
KPI = (os.environ.get("MF_AROMEPI_KEY") or "").strip()
KPO = (os.environ.get("MF_PAQUETOBS_KEY") or "").strip()
print("Clés présentes :", bool(KPI), bool(KPO))
B = "https://public-api.meteofrance.fr/public"
def get(url, k):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"apikey": k, "accept": "*/*"}), timeout=120) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]
print("=== AROME-PI ===")
for g in ("001", "0025", "01"):
    for d in ("FRANCE",):
        wcs = f"MF-NWP-HIGHRES-AROMEPI-{g}-{d}-WCS"
        c, b = get(f"{B}/aromepi/1.0/wcs/{wcs}/GetCapabilities?service=WCS&version=2.0.1&language=eng", KPI)
        if c != 200:
            print(f"[{c}] {wcs}", b[:200].decode("utf-8", "replace")); continue
        ids = re.findall(r"<wcs:CoverageId>([^<]+)</wcs:CoverageId>", b.decode("utf-8", "replace"))
        fam = Counter(re.sub(r"___\d{4}-\d\d-\d\dT\d\d\.\d\d\.\d\dZ.*$", "", i) for i in ids)
        print(f"[200] {wcs} : {len(ids)} couvertures")
        for k, v in sorted(fam.items()): print("    ", k, "×", v)
        runs = sorted({m for i in ids for m in re.findall(r"\d{4}-\d\d-\d\dT\d\d\.\d\d\.\d\dZ", i)})
        print("     runs :", runs[-5:])
        neb = [i for i in ids if "NEBUL" in i or "CLOUD" in i]
        if neb:
            c2, b2 = get(f"{B}/aromepi/1.0/wcs/{wcs}/DescribeCoverage?service=WCS&version=2.0.1&coverageid={neb[-1]}", KPI)
            t = b2.decode("utf-8", "replace")
            print("     Describe", neb[-1], c2, re.findall(r"<gml:(?:lowerCorner|upperCorner)>[^<]+", t)[:2],
                  re.findall(r"<(?:gmlrgrid:)?coefficients>[^<]{0,200}", t)[:1], re.findall(r"timePosition>[^<]+", t)[:3])
print("=== Package Observations ===")
for v in ("v1", "v2"):
    for p in (f"/DPPaquetObs/{v}/paquet/horaire?id-departement=74&format=json",
              f"/DPPaquetObs/{v}/paquet/stations/horaire?format=json",
              f"/DPPaquetObs/{v}/paquet/infrahoraire-6m?id-departement=74&format=json",
              f"/DPPaquetObs/{v}/paquet/stations/infrahoraire-6m?format=json"):
        c, b = get(B + p, KPO)
        print(f"[{c}] {p} ({len(b)} o) :", b[:250].decode("utf-8", "replace").replace("\n", " "))
