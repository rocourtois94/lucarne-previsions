"""Diagnostic des vignettes : temps de réponse du CDS (hips2fits) pour quelques objets, avec pauses (usage raisonnable)."""
import time, urllib.request, urllib.parse
OBJ = [("NGC 869", 34.75, 57.13, 0.51), ("NGC 281", 13.2, 56.6, 0.77), ("NGC 6960", 311.4, 30.7, 1.7),
       ("NGC 1499", 60.0, 36.4, 3.0), ("NGC 7000", 314.7, 44.3, 3.0), ("M 101 témoin", 210.8, 54.35, 0.6)]
SURVEYS = ["CDS/P/PanSTARRS/DR1/color-z-zg-g", "CDS/P/DSS2/color"]
for host in ["alasky.cds.unistra.fr", "alaskybis.cds.unistra.fr"]:
    for hips in SURVEYS:
        for name, ra, dec, fov in OBJ:
            q = urllib.parse.urlencode(dict(hips=hips, width=128, height=128, fov=fov, projection="TAN", ra=ra, dec=dec, format="jpg"))
            t = time.time()
            try:
                with urllib.request.urlopen(f"https://{host}/hips-image-services/hips2fits?{q}", timeout=60) as r:
                    d = r.read(); print(f"{host:26} {hips[6:20]:14} {name:13} fov {fov:4}° : {r.status} {time.time()-t:5.1f} s {len(d)} o", flush=True)
            except Exception as e:
                print(f"{host:26} {hips[6:20]:14} {name:13} fov {fov:4}° : ÉCHEC {time.time()-t:5.1f} s {str(e)[:80]}", flush=True)
            time.sleep(2)
