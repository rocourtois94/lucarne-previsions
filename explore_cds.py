"""Diagnostic des vignettes : sources de secours quand le CDS (hips2fits) ne répond pas."""
import time, urllib.request, urllib.parse
OBJ = [("NGC 869", 34.75, 57.13, 0.51), ("NGC 7000", 314.7, 44.3, 3.0), ("NGC 6960", 311.4, 30.7, 1.7)]
def get(url, label):
    t = time.time()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Lucarne-diagnostic"})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = r.read(); print(f"{label:48} : {r.status} {time.time()-t:5.1f} s {len(d)} o {r.headers.get('Content-Type')}", flush=True)
    except Exception as e:
        print(f"{label:48} : ÉCHEC {time.time()-t:5.1f} s {str(e)[:70]}", flush=True)
    time.sleep(1.5)
get("https://alasky.cds.unistra.fr/", "CDS alasky accueil")
get("https://alaskybis.cds.unistra.fr/", "CDS alaskybis accueil")
get("https://alasky.u-strasbg.fr/", "CDS ancien domaine")
for name, ra, dec, fov in OBJ:
    for layer in ["ls-dr10", "sdss", "unwise-neo7", "halpha", "ps1", "dss2", "mellinger"]:
        q = urllib.parse.urlencode(dict(ra=ra, dec=dec, layer=layer, pixscale=f"{fov*3600/128:.2f}", size=128))
        get(f"https://www.legacysurvey.org/viewer/jpeg-cutout?{q}", f"legacysurvey {layer} {name}")
    get(f"https://skyserver.sdss.org/dr18/SkyServerWS/ImgCutout/getjpeg?ra={ra}&dec={dec}&scale={fov*3600/128:.2f}&width=128&height=128", f"SDSS SkyServer {name}")
    get(f"https://ps1images.stsci.edu/cgi-bin/ps1filenames.py?ra={ra}&dec={dec}&filters=gri", f"STScI PS1 (liste) {name}")
