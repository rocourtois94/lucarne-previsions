import os, re, urllib.request, urllib.error, datetime as dt, json
KPI = os.environ["MF_AROMEPI_KEY"].strip(); KPO = os.environ["MF_PAQUETOBS_KEY"].strip()
B = "https://public-api.meteofrance.fr/public"
def get(url, k):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"apikey": k, "accept": "*/*"}), timeout=120) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300], {}
h = dt.datetime.now(dt.timezone.utc).replace(minute=0, second=0, microsecond=0) - dt.timedelta(hours=1)
for d in (h.strftime("%Y-%m-%dT%H:%M:%SZ"),):
    c, b, hd = get(f"{B}/DPPaquetObs/v2/paquet/stations/horaire?date={d}&format=json", KPO)
    print(c, len(b), b[:300])
    if c == 200:
        a = json.loads(b); print("stations:", len(a), "avec n:", sum(1 for x in a if x.get("n") is not None), "avec td:", sum(1 for x in a if x.get("td") is not None))
wcs = "MF-NWP-HIGHRES-AROMEPI-0025-FRANCE-WCS"
c, b, _ = get(f"{B}/aromepi/1.0/wcs/{wcs}/GetCapabilities?service=WCS&version=2.0.1&language=eng", KPI)
ids = re.findall(r"<wcs:CoverageId>([^<]+)</wcs:CoverageId>", b.decode())
neb = sorted(i for i in ids if i.startswith("NEBUL__"))[-1]
print(neb)
c, b, _ = get(f"{B}/aromepi/1.0/wcs/{wcs}/DescribeCoverage?service=WCS&version=2.0.1&coverageid={neb}", KPI)
print(c, b.decode()[:2500])
run = re.search(r"___(.*?Z)", neb).group(1)
t = dt.datetime.strptime(run, "%Y-%m-%dT%H.%M.%SZ") + dt.timedelta(hours=2)
for fmt in ("application/wmo-grib",):
    u = (f"{B}/aromepi/1.0/wcs/{wcs}/GetCoverage?service=WCS&version=2.0.1&coverageid={neb}"
         f"&subset=time({t:%Y-%m-%dT%H:%M:%SZ})&subset=lat(45,47.5)&subset=long(5,8)&format={fmt}")
    c, b, hd = get(u, KPI); print("GetCoverage", c, len(b), hd.get("Content-Type"), b[:120])
    if c == 200: open("pi.grib", "wb").write(b)
for name in ("LOW_CLOUD_COVER__GROUND_OR_WATER_SURFACE", "PRESSURE__LEVEL_OF_CLOUD_TOP", "DIAG_FOG__GROUND_OR_WATER_SURFACE"):
    print(name, sorted(i for i in ids if i.startswith(name))[-1:])
import eccodes
if os.path.exists("pi.grib"):
    raw = open("pi.grib","rb").read(); hh = eccodes.codes_new_from_message(raw)
    print({k: eccodes.codes_get(hh, k) for k in ("Ni","Nj","latitudeOfFirstGridPointInDegrees","longitudeOfFirstGridPointInDegrees","iDirectionIncrementInDegrees","units","shortName")})
    v = eccodes.codes_get_values(hh); print(v.min(), v.max(), v.mean())
