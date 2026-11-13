# Prévisions Lucarne

Calcule toutes les 6 heures la nébulosité (totale, basse, moyenne, haute) du modèle **MeteoSwiss ICON-CH2**
(2,1 km, 5 jours, membre de contrôle) et la dépose dans le stockage R2 de Lucarne, sous `fc1/ch2/`
(lue par l'app sur `https://carte.photily.fr/fc1/ch2/`).

Données : MeteoSwiss, licence CC BY 4.0 — mention obligatoire « Source : MeteoSwiss ».

## Mise en place (une fois)
1. Créer un dépôt **privé** sur GitHub (ex. `lucarne-previsions`) et y mettre ces fichiers.
2. Cloudflare › R2 › *Manage API tokens* › créer un jeton **Object Read & Write** limité au bucket de Lucarne.
3. GitHub › dépôt › *Settings › Secrets and variables › Actions* › ajouter :
   `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`.
4. Onglet *Actions* › « Prévisions Lucarne » › *Run workflow* pour un premier essai (≈ 7 min).

## Format d'un morceau (`<lat>_<lon>.bin`, 1° × 1°)
`LCFC` · version (u16) · run en secondes Unix (i64) · pas (u32, 3600) · heures (u16) · variables (u16)
· lat sud (f32) · lon ouest (f32) · pas de grille (f32, 0,02°) · lignes (u16) · colonnes (u16),
puis les octets compressés (DEFLATE brut, sans en-tête zlib) : [heure][variable][ligne du sud au nord][colonne d'ouest en est], en %,
255 = hors domaine. Variables : CLCT, CLCL, CLCM, CLCH (en %), puis CDCT depuis le 7/11/2026 : sommet des nuages
au-dessus de la mer par pas de 20 m (1…250 = 20…5000 m et plus), 0 = pas de nuage. Les anciennes versions de l'app
lisent seulement les 4 premières variables (le nombre de variables est dans l'en-tête).

## Seeing (`seeing.py`)
Modèle **GFS 0,25°** (NOAA/NCEP, domaine public) via le filtre NOMADS (Europe 34–72° N, 25° O–45° E, toutes les 3 h
jusqu'à 120 h). Méthode de Dewan (1993) sur les couches entre niveaux de pression + couche limite moyenne (0,9″,
plus avec le vent à 10 m). Morceaux 5° × 5° `fc1/seeing/<lat>_<lon>.bin`, en-tête « LCSE » identique à « LCFC »
(pas 3 h, 2 variables : seeing en 0,05″, vent du courant-jet en m/s ; grille 0,25°, bords inclus, 21 × 21 points).

### Nuages GFS (dans les morceaux seeing)
Depuis le 7/11/2026, chaque morceau `fc1/seeing/` contient 3 variables : seeing (0,05″), courant-jet (m/s) et nébulosité totale GFS (TCDC, % instantané, 255 = inconnu). Sert à la carte « Meilleur ciel » de l'app hors du domaine MeteoSwiss.


## Météo-France AROME (depuis le 7/11/2026)
`arome.py` : AROME 0,01° (≈ 1,3 km, 51 h), paquet SP2 (nébulosité basse, moyenne, haute ; seuls ces 3 champs sont
téléchargés, par requêtes partielles). Grille ramenée à 0,02° sur 41–52° N, 6° O–10° E. Morceaux 1° × 1°
`fc1/arome/<lat>_<lon>.bin`, format « LCFC » (4 variables : CLCT calculée par recouvrement aléatoire, CLCL, CLCM, CLCH ;
pas 1 h). Licence Ouverte 2.0 — « Source : Météo-France ».

## ECMWF IFS 0,1° (depuis le 7/11/2026)
`ecmwf.py` : données ouvertes ECMWF, nébulosité tcc, lcc, mcc, hcc, toutes les 3 h jusqu'à 72 h, Europe (35–72° N,
25° O–45° E). Morceaux 5° × 5° bords inclus (51 × 51 points) `fc1/ec/<lat>_<lon>.bin`, format « LCFC » (pas 3 h).
Remplace la nébulosité GFS pour la carte « Meilleur ciel ». CC BY 4.0 — « Source : ECMWF ».


## Variables météo ajoutées (7/11/2026)
Codage sur un octet décrit dans `codage.py` (255 = inconnu).
Fichiers séparés (la carte ne télécharge que les nuages) :
- `fc1/ch2/` (5 variables : CLCT, CLCL, CLCM, CLCH, CDCT) et `fc1/ch2w/` (6 : T2M, TD2M, WIND, GUST, TQV, TQI).
- `fc1/arome/` (4 : CLCT, CLCL, CLCM, CLCH) et `fc1/aromew/` (7 : T2M, TD2M calculé d'après l'humidité, WIND, GUST, TQV, TSURF, BLH — les trois derniers à 0,025°).
- `fc1/seeing/` : 4e variable = seeing de l'atmosphère libre seule (pour l'estimation expérimentale près du sol de l'app).

## « Satellite prévu » (depuis le 7/11/2026)
`arome.py` produit aussi `fc1/ir/<lat>_<lon>.bin` : morceaux 5° × 5° (35–60° N, 15° O–20° E), pas 0,05°, 52 h,
2 variables : (0) écart entre la température à 2 m et la température de brillance infrarouge 10,8 µm simulée par AROME
(paquet SP3), en 0,5 K (0…250), 255 = hors domaine ; (1) nébulosité basse AROME (%), pour montrer brouillard et stratus. ≈ 0 par ciel clair, grand pour un nuage haut et épais.
Limite : le brouillard et les stratus bas, presque à la température du sol, apparaissent à peine (comme sur une vraie
image infrarouge de nuit).

## Pluie AROME et vigilance (depuis le 8/11/2026)
- `fc1/aromew/` : 8e variable PRECIP = précipitations de l'heure écoulée (pluie + neige + grésil), mm × 10.
- `fc1/vigilance.json` (`vigilance.py`) : carte de vigilance Météo-France en cours, par département. Nécessite le secret
  GitHub `MF_VIGILANCE_KEY` (clé de l'API « Données publiques Vigilance », portail-api.meteofrance.fr) ; sans clé,
  l'étape est sautée.

## Carte des vents (depuis le 11/11/2026)
- `fc1/wind/<lat>_<lon>.bin` (`arome.py`) : morceaux 5° × 5° (même grille que `fc1/ir/`, 0,05°, 52 h), format « LCFC »,
  3 variables : rafales (km/h), vent moyen à 10 m U et V (km/h + 125 ; vers l'est, vers le nord). Source : Météo-France.
- `fc1/seeing/` : 5e et 6e variables = vent du courant-jet U et V (m/s + 125), au niveau (300, 250 ou 200 hPa) où il est le plus fort.
- `fc1/ir/` : depuis le 11/11/2026, 4 variables : écart infrarouge, puis nuages bas, moyens et hauts AROME (%).

## Horaires des calculs

Le planificateur de GitHub sautait la plupart des passages programmés. Les calculs sont donc lancés par le Worker
Cloudflare « lucarne-horloge » (code dans `horloge/worker.js`, secret `GITHUB_TOKEN`) :
vigilance.yml à h+47, meteoswiss.yml à h+40, previsions.yml à 4:20, 10:20, 16:20 et 22:20 UTC.
Seul entretien.yml (mensuel) garde un horaire GitHub. Chaque calcul reste lançable à la main (« Run workflow »).


## Sommet des nuages bas « LTOP » (depuis le 13/11/2026)
`lowtop.py` (appelé par `lucarne_forecast.py`, ICON-CH2 seulement) : 6e variable de `fc1/ch2/` (les morceaux ont
désormais 6 variables ; `fc1/ch1/` aussi, avec LTOP = 255 inconnu). Sommet de la première couche nuageuse partant
d'une base à moins de 2 500 m au-dessus du terrain (CLC ≥ 50 % pour commencer, puis tant que CLC ≥ 30 %), d'après la
nébulosité 3D CLC et la hauteur des demi-niveaux (constantes verticales). Codage comme CDCT : pas de 20 m, 0 = pas de
nuage bas, 255 = inconnu. Seulement les niveaux du bas (≈ 40, jusqu'à 4,5 km, requêtes partielles sur la fin des
fichiers) et les heures de nuit (14–07 UTC) des 72 premières heures : ≈ 1 Go téléchargé par run. CC BY 4.0, MeteoSwiss.
