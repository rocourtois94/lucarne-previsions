"""Diagnostic : au-dessus de Genève, valeur IR, masque nuageux, type de nuage, hauteur du sommet (EUMETView)."""
import urllib.request, io, math, datetime as dt
from PIL import Image
B="https://view.eumetsat.int/geoserver/wms"; UA={"User-Agent":"Lucarne-previsions (info@photily.fr)"}
t=(dt.datetime.utcnow().replace(minute=0,second=0,microsecond=0)-dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00.000Z")
pts={"Genève":(46.20,6.15),"St-Julien":(46.14,6.08),"Lyon":(45.76,4.84),"Paris":(48.86,2.35),"Marseille":(43.3,5.37)}
def get(layer,style=""):
    u=f"{B}?service=WMS&version=1.3.0&request=GetMap&layers={layer}&styles={style}&crs=EPSG:4326&bbox=42,0,50,12&width=600&height=400&format=image/png&time={t}"
    with urllib.request.urlopen(urllib.request.Request(u,headers=UA),timeout=90) as r: return Image.open(io.BytesIO(r.read())).convert("RGBA")
print("heure",t)
for lay in ["mtg_fd:ir105_hrfi","msg_fes:clm","msg_fes:cth","mtg_fd:rgb_fog","mtg_fd:rgb_cloudtype","msg_fes:vis006"]:
    try:
        im=get(lay)
        vals={n:im.getpixel((int((lo-0)/12*600),int((50-la)/8*400))) for n,(la,lo) in pts.items()}
        print(lay, vals)
    except Exception as e: print(lay,"échec",str(e)[:80])
