"""Repérage EUMETView (WMS EUMETSAT) : couches infrarouges Meteosat, dimension temps, une image d'essai."""
import urllib.request, re, time
B = "https://view.eumetsat.int/geoserver/wms"
def get(u, n=None):
    req = urllib.request.Request(u, headers={"User-Agent": "Lucarne-previsions (contact info@photily.fr)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.headers.get("Content-Type"), r.read() if n is None else r.read(n)
s, ct, cap = get(B + "?service=WMS&version=1.3.0&request=GetCapabilities")
cap = cap.decode("utf-8", "ignore")
print("capabilities", s, ct, len(cap))
names = re.findall(r"<Layer[^>]*queryable[^>]*>\s*<Name>([^<]+)</Name>\s*<Title>([^<]+)</Title>", cap)
for n, t in names:
    if re.search(r"ir|IR|10\.8|10\.5|cloud|Cloud", n + t): print("  ", n, "|", t)
for lay in ["msg_fes:ir108", "mtg_fd:ir105_hrfi", "msg_fes:rgb_naturalenhncd"]:
    i = cap.find("<Name>" + lay + "</Name>")
    if i >= 0:
        seg = cap[i:i + 3000]
        m = re.search(r'<Dimension name="time"[^>]*>([^<]{0,300})', seg)
        print(lay, "time:", m.group(1)[:200] if m else None, "...", (m.group(1)[-80:] if m else ""))
for lay in ["msg_fes:ir108", "mtg_fd:ir105_hrfi"]:
    u = (B + "?service=WMS&version=1.3.0&request=GetMap&layers=" + lay + "&styles=&crs=EPSG:4326&bbox=40,-6,52,18"
         "&width=600&height=300&format=image/png")
    try:
        t = time.time(); s, ct, d = get(u); print("GetMap", lay, s, ct, len(d), round(time.time() - t, 1), "s")
    except Exception as e:
        print("GetMap", lay, "échec", e)
    time.sleep(2)
