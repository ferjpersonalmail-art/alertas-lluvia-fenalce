"""
clima_openmeteo.py — Viento actual (y opcionalmente lluvia pronosticada) en el centro de la lluvia.

El radar no mide viento; se consulta Open-Meteo (gratuito, sin clave) en una sola
solicitud para todos los departamentos que se van a notificar.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("clima")
URL = "https://api.open-meteo.com/v1/forecast"


def consultar(puntos: list[tuple[float, float]], incluir_pronostico: bool = False) -> list[dict | None]:
    """puntos: [(lon, lat), ...] -> [{viento_kmh, viento_dir, rafaga_kmh, [lluvia_3h_mm, prob_max]}, ...]"""
    if not puntos:
        return []
    params = {
        "latitude": ",".join(f"{lat:.3f}" for _, lat in puntos),
        "longitude": ",".join(f"{lon:.3f}" for lon, _ in puntos),
        "current": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "wind_speed_unit": "kmh",
        "timezone": "America/Bogota",
    }
    if incluir_pronostico:
        params["hourly"] = "precipitation,precipitation_probability"
        params["forecast_hours"] = 4
    try:
        r = requests.get(URL, params=params, timeout=20)
        r.raise_for_status()
        datos = r.json()
    except Exception as e:  # sin viento el mensaje sale igual, solo omite esa línea
        log.warning("Open-Meteo no disponible: %s", e)
        return [None] * len(puntos)
    if isinstance(datos, dict):
        datos = [datos]
    salida = []
    for d in datos:
        actual = d.get("current") or {}
        res = {"viento_kmh": actual.get("wind_speed_10m"), "viento_dir": actual.get("wind_direction_10m"),
               "rafaga_kmh": actual.get("wind_gusts_10m")}
        if incluir_pronostico:
            horas = d.get("hourly") or {}
            lluvia = (horas.get("precipitation") or [])[1:4]         # las 3 horas siguientes
            prob = (horas.get("precipitation_probability") or [])[1:4]
            res["lluvia_3h_mm"] = float(sum(x or 0 for x in lluvia)) if lluvia else None
            res["prob_max"] = max((x for x in prob if x is not None), default=None)
        salida.append(res)
    while len(salida) < len(puntos):
        salida.append(None)
    return salida
