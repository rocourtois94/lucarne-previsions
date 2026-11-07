"""Codage sur un octet des grandeurs météo des morceaux « LCFC » (255 = inconnu). Lu par l'app (ICONClouds.swift).

Nuages (%)                 : 0…100
CDCT, sommet des nuages    : altitude / 20 m (1…250), 0 = pas de sommet calculé
T2M, TD2M (°C)             : (°C + 60) × 2, soit −60 … +65 °C au demi-degré
WIND, GUST (km/h)          : km/h arrondis, 0…250
TQV, vapeur d'eau (mm)     : mm × 4, 0…62,5 mm (= kg/m²)
TQI, glace nuageuse (g/m²) : g/m² / 2, 0…500 g/m²
"""
import numpy as np


def _u8(x, lo=0, hi=250):
    x = np.asarray(x, dtype=float)
    out = np.clip(np.rint(x), lo, hi)
    return np.where(np.isfinite(x), out, 255).astype(np.uint8)


def kelvin(k):
    return _u8((np.asarray(k, float) - 273.15 + 60) * 2)


def celsius(c):
    return _u8((np.asarray(c, float) + 60) * 2)


def kmh_from_ms(ms):
    return _u8(np.asarray(ms, float) * 3.6)


def wind_kmh(u, v):
    return _u8(np.hypot(u, v) * 3.6)


def vapour_mm(kgm2):
    return _u8(np.asarray(kgm2, float) * 4)


def ice_gm2(kgm2):
    return _u8(np.asarray(kgm2, float) * 1000 / 2)


def dewpoint_c(t_c, rh_pct):
    """Point de rosée (Magnus) à partir de la température (°C) et de l'humidité relative (%)."""
    rh = np.clip(np.asarray(rh_pct, float), 1, 100)
    a, b = 17.62, 243.12
    g = np.log(rh / 100) + a * t_c / (b + t_c)
    return b * g / (a - g)
