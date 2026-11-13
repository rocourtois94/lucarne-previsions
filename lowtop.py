"""Sommet des NUAGES BAS (« LTOP ») d'après la nébulosité 3D d'ICON-CH2 (CLC sur les niveaux du modèle).

Le modèle ne publie que le sommet du nuage le plus haut (CDCT) : avec des cirrus au-dessus d'une mer de nuages,
on ne sait plus où s'arrête la mer de nuages. Ici, pour chaque case, on part du sol et on monte niveau par niveau :
première couche nuageuse (CLC ≥ 50 %) dont la base est à moins de 2 500 m au-dessus du terrain, puis on continue
tant que CLC ≥ 30 % ; le sommet est le haut du dernier niveau nuageux. 0 = pas de nuage bas.

Volume : seuls les niveaux du bas (≈ 30 sur 80, jusqu'à ≈ 4,5 km) sont téléchargés, par une requête partielle sur
la fin de chaque fichier (les niveaux y sont rangés du haut vers le bas, messages de taille fixe), et seulement
pour les heures de nuit des 3 prochains jours. Données : MeteoSwiss, CC BY 4.0.
"""
import concurrent.futures as cf
import datetime as dt
import urllib.request, time

import numpy as np
import eccodes

START_CLC, KEEP_CLC = 50.0, 30.0       # % : début d'une couche nuageuse, puis tant qu'elle continue
MAX_BASE_AGL = 2500.0                  # m : au-dessus, ce n'est plus un nuage bas
MAX_HEIGHT = 4500.0                    # m au-dessus de la mer : niveaux téléchargés
NIGHT_UTC = set(range(14, 24)) | set(range(0, 8))
MAX_HOUR = 72


def _get(url, rng=None, tries=4):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Range": rng} if rng else {})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(3 * (k + 1))


def _messages(raw):
    """(niveau, valeurs) de chaque message GRIB d'un bloc d'octets commençant sur un message"""
    out, pos = [], 0
    while pos + 4 <= len(raw) and raw[pos:pos + 4] == b"GRIB":
        h = eccodes.codes_new_from_message(raw[pos:])
        try:
            n = eccodes.codes_get(h, "totalLength")
            out.append((eccodes.codes_get(h, "level"), eccodes.codes_get_values(h)))
        finally:
            eccodes.codes_release(h)
        pos += n
    return out


def _msg_size(url):
    """Taille d'un message « plein » : le dernier message du fichier (niveau du sol, jamais constant)"""
    raw = _get(url, "bytes=-2000000")
    return len(raw) - raw.rfind(b"GRIB")


def _tail(url, nlevels, msg_size):
    """Les nlevels derniers niveaux (les plus bas) ; repli sur le fichier entier si le découpage échoue"""
    raw = _get(url, f"bytes=-{nlevels * msg_size}")
    msgs = _messages(raw)
    if len(msgs) < nlevels:
        msgs = _messages(_get(url))
    return {lev: v for lev, v in msgs}


def run(L, ref, idx, far, cube, vi, hsurf):
    """Remplit cube[h, vi] (codé par pas de L.TOP_STEP m, 0 = pas de nuage bas, 255 = inconnu)."""
    t0 = time.time()
    coll = L.http_json(f"{L.STAC}/collections/{L.COLL}")
    vkey = next((k for k in coll["assets"] if k.startswith("vertical_constants")), None)
    if vkey is None:
        print("LTOP : constantes verticales introuvables"); return
    vurl = coll["assets"][vkey]["href"]
    urls = L.asset_urls(ref, "CLC")
    ref_t = dt.datetime.fromisoformat(ref.replace("Z", "+00:00"))
    hours = [h for h in sorted(urls) if h <= MAX_HOUR and (ref_t + dt.timedelta(hours=h)).hour in NIGHT_UTC]
    if not hours:
        print("LTOP : pas d'échéance CLC"); return
    msg_size = _msg_size(urls[hours[0]])
    # Hauteur des demi-niveaux (HHL) : 81 valeurs par case, 1 = sommet du modèle … 81 = sol
    hhl = _tail(vurl, 60, _msg_size(vurl))
    levs = sorted(hhl)                                   # demi-niveaux disponibles (du haut vers le bas)
    H = {k: hhl[k][idx] for k in levs}
    # Niveaux pleins à garder : ceux dont le haut est sous MAX_HEIGHT au-dessus des plaines
    low_cells = (hsurf[idx] < 600) if hsurf is not None else np.ones(idx.shape, bool)
    full = [k for k in range(1, 81) if (k in H and k + 1 in H and np.median(H[k][low_cells]) < MAX_HEIGHT)]
    K = len(full)
    if K < 5:
        print("LTOP : niveaux insuffisants", K); return
    print(f"LTOP : {K} niveaux bas, {len(hours)} échéances de nuit, message {msg_size} o")
    ground = H[81] if 81 in H else (hsurf[idx] if hsurf is not None else np.zeros(idx.shape))

    def work(h):
        clc = _tail(urls[h], K, msg_size)
        started = np.zeros(idx.shape, bool); done = np.zeros(idx.shape, bool)
        top = np.zeros(idx.shape, np.float32)
        for k in sorted(full, reverse=True):              # du bas vers le haut
            if k not in clc:
                continue
            c = clc[k][idx]
            base_agl = H[k + 1] - ground
            start = ~started & ~done & (c >= START_CLC) & (base_agl < MAX_BASE_AGL)
            started |= start
            cont = started & ~done & (c >= KEEP_CLC)
            top = np.where(cont, H[k], top)
            done |= started & ~cont
        g = np.where(top > 0, np.clip(np.rint(top / L.TOP_STEP), 1, 250), 0).astype(np.uint8)
        g[far] = 255
        return h, g

    with cf.ThreadPoolExecutor(8) as ex:
        for h, g in ex.map(work, hours):
            cube[h, vi] = g
    print("LTOP : calculé en", round(time.time() - t0), "s")
