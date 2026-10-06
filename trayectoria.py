"""
trayectoria.py - Municipios a donde podría llegar la lluvia, según hacia dónde y qué tan rápido se mueve la tormenta.

Se toman los municipios donde ya llueve (o los de mayor riesgo), se desplaza su centro en la dirección de las
nubes a 30, 60, 90 y 120 minutos, y se mira en qué municipios caen esos puntos. La velocidad se limita a
10-40 km/h (el seguimiento de nubes a veces da valores irreales).

   python trayectoria.py 20 "La Jagua de Ibirico,San Alberto" 90 25
"""
from __future__ import annotations

import json
import math
import sys
import unicodedata
from pathlib import Path

BASE = Path(__file__).resolve().parent
VEL_MIN, VEL_MAX = 10.0, 40.0
_M = None


def _norm(s):
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower().strip()


def _titulo(s):
    out = []
    for i, w in enumerate(str(s).lower().split()):
        out.append(w if i and w in ("de", "del", "la", "las", "los", "el", "y", "e") else w[:1].upper() + w[1:])
    return " ".join(out)


def _centro(r):
    a = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
        c = x0 * y1 - x1 * y0
        a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c
    if abs(a) < 1e-12:
        return sum(p[0] for p in r) / len(r), sum(p[1] for p in r) / len(r)
    return cx / (3 * a), cy / (3 * a)


def _area(r):
    return abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))) / 2


def _carga():
    global _M
    if _M is None:
        F = json.loads((BASE / "datos" / "municipios_mgn2024.geojson").read_text(encoding="utf-8"))["features"]
        _M = []
        for f in F:
            p, g = f["properties"], f["geometry"]
            anillos = [pl[0] for pl in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]])]
            xs = [c[0] for r in anillos for c in r]
            ys = [c[1] for r in anillos for c in r]
            _M.append({"cod": str(p["MPIO_CCNCT"]), "dep": str(p["DPTO_CCDGO"]).zfill(2), "nom": _titulo(p["MPIO_CNMBR"]),
                       "dep_nom": _titulo(p["DPTO_CNMBR"]), "anillos": anillos,
                       "caja": (min(xs), min(ys), max(xs), max(ys)), "c": _centro(max(anillos, key=_area))})
    return _M


def _dentro(x, y, r):
    d = False
    j = len(r) - 1
    for i in range(len(r)):
        xi, yi = r[i][0], r[i][1]
        xj, yj = r[j][0], r[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            d = not d
        j = i
    return d


def municipio_en(lon, lat):
    for m in _carga():
        x0, y0, x1, y1 = m["caja"]
        if x0 <= lon <= x1 and y0 <= lat <= y1 and any(_dentro(lon, lat, r) for r in m["anillos"]):
            return m
    return None


def camino(cod_dep: str, origen: list[str], grados: float | None, vel_kmh: float | None,
           minutos=(30, 60, 90, 120), maximo=4):
    """Municipios (dicts con nom, dep, dep_nom) en el camino de la tormenta, en orden de llegada."""
    if grados is None or not origen:
        return []
    cod_dep = str(cod_dep).zfill(2)
    nombres = {_norm(n) for n in origen}
    M = _carga()
    ini = [m for m in M if m["dep"] == cod_dep and _norm(m["nom"]) in nombres] or \
          [m for m in M if _norm(m["nom"]) in nombres]
    if not ini:
        return []
    v = min(max(vel_kmh or VEL_MIN, VEL_MIN), VEL_MAX)
    vistos = {m["cod"] for m in ini}
    out = []
    for mins in minutos:
        d = v * mins / 60.0
        for m in ini:
            lon, lat = m["c"]
            dlat = d * math.cos(math.radians(grados)) / 111.0
            dlon = d * math.sin(math.radians(grados)) / (111.0 * math.cos(math.radians(lat)))
            mm = municipio_en(lon + dlon, lat + dlat)
            if mm and mm["cod"] not in vistos:
                vistos.add(mm["cod"])
                out.append(mm)
    return out[:maximo]


def rumbo_y_velocidad(r: dict):
    """(grados hacia donde van las nubes, km/h) a partir del seguimiento de nubes o del viento en altura."""
    mov = r.get("movimiento") or {}
    if mov.get("grados") is not None and mov.get("vel_kmh", 0) >= 5:
        return float(mov["grados"]), float(mov["vel_kmh"])
    v = r.get("viento") or {}
    if v.get("alto_dir") is not None and (v.get("alto_kmh") or 0) >= 5:
        return float((v["alto_dir"] + 180) % 360), float(v["alto_kmh"])
    return None, None


if __name__ == "__main__":
    cod, nombres, g, vel = sys.argv[1], sys.argv[2].split(","), float(sys.argv[3]), float(sys.argv[4])
    for m in camino(cod, nombres, g, vel):
        print(m["nom"], "-", m["dep_nom"])
