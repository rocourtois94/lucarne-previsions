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
255 = hors domaine. Variables : CLCT, CLCL, CLCM, CLCH.

## Seeing (`seeing.py`)
Modèle **GFS 0,25°** (NOAA/NCEP, domaine public) via le filtre NOMADS (Europe 34–72° N, 25° O–45° E, toutes les 3 h
jusqu'à 120 h). Méthode de Dewan (1993) sur les couches entre niveaux de pression + couche limite moyenne (0,9″,
plus avec le vent à 10 m). Morceaux 5° × 5° `fc1/seeing/<lat>_<lon>.bin`, en-tête « LCSE » identique à « LCFC »
(pas 3 h, 2 variables : seeing en 0,05″, vent du courant-jet en m/s ; grille 0,25°, bords inclus, 21 × 21 points).
