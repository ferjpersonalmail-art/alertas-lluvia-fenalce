"""
fuente_ideam.py — Reflectividad de los radares del IDEAM en vivo (imágenes transparentes que
publica la OSPA en bart.ideam.gov.co, cada ~5 min).

Cada imagen (800×800) cubre un cuadrado centrado en el radar con semilado = alcance. La paleta
de colores se calibró contra los datos crudos del mismo radar (AWS) → datos/paleta_ideam.json.
Dentro del alcance de estos radares, el motor usa esta reflectividad en lugar de la del satélite.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from PIL import Image

from fuente_radar import Cuadro

log = logging.getLogger("ideam")
BASE_URL = "https://bart.ideam.gov.co"
BASE = Path(__file__).resolve().parent
RADARES = {  # centro y alcance verificados con los datos crudos del IDEAM en AWS
    "Barrancabermeja": (6.93276, -73.76251, 298.5),
    "Munchique": (2.51788, -76.95566, 224.4),
}
_MAPA = {}


def _paleta():
    t = json.loads((BASE / "datos" / "paleta_ideam.json").read_text(encoding="utf-8"))
    cols = np.array([[int(x) for x in k.split(",")] for k in t], dtype=np.int32)
    dbz = np.array([v[0] for v in t.values()], dtype=np.float32)
    return cols, dbz


def _a_dbz(rgba, cols, dbz):
    """Color de la imagen -> dBZ (color más parecido de la paleta calibrada), en clases de 5 dBZ."""
    a = rgba[:, :, 3] > 0
    out = np.zeros(a.shape, np.uint8)
    px = rgba[a][:, :3].astype(np.int32)
    if len(px):
        uniq, inv = np.unique(px, axis=0, return_inverse=True)
        d2 = ((uniq[:, None, :] - cols[None, :, :]) ** 2).sum(-1)
        val = dbz[d2.argmin(1)][inv.ravel()]
        out[a] = (np.floor(val / 5) * 5).clip(10, 75).astype(np.uint8)
    return out


def _mapeo(malla, nombre):
    """Para cada píxel de la malla del motor: fila/columna en la imagen del radar y si está en alcance."""
    k = (malla.clave, nombre)
    if k in _MAPA:
        return _MAPA[k]
    la0, lo0, km = RADARES[nombre]
    py, px = np.mgrid[0:malla.alto, 0:malla.ancho]
    lon, lat = malla.a_lonlat(px + 0.5, py + 0.5)
    hy = km / 111.32
    hx = hy / np.cos(np.radians(la0))
    c = ((lon - (lo0 - hx)) / (2 * hx) * 800).astype(np.int32)
    r = (((la0 + hy) - lat) / (2 * hy) * 800).astype(np.int32)
    dist = np.hypot((lat - la0) * 111.32, (lon - lo0) * 111.32 * np.cos(np.radians(la0)))
    dentro = (dist <= km * 0.97) & (c >= 0) & (c < 800) & (r >= 0) & (r < 800)
    _MAPA[k] = (np.clip(r, 0, 799), np.clip(c, 0, 799), dentro)
    return _MAPA[k]


def cobertura(malla) -> np.ndarray:
    m = np.zeros((malla.alto, malla.ancho), bool)
    for n in RADARES:
        m |= _mapeo(malla, n)[2]
    return m


def _cuadros_radar(nombre, dir_cache: Path, ventana_min: int):
    txt = urllib.request.urlopen(f"{BASE_URL}/ospa/gifs/Radar/Transp/{nombre}/{nombre}_z.txt", timeout=30).read().decode()
    rutas = [u.replace("/radares/interno/radarcol/", "/ospa/radarcol/") for u in txt.split() if u.endswith(".png")]
    lim = datetime.now(timezone.utc) - timedelta(minutes=ventana_min + 10)
    salida = []
    for ruta in rutas:
        nom = ruta.split("/")[-1]
        try:
            t = datetime.strptime(nom[3:13], "%y%m%d%H%M").replace(tzinfo=timezone.utc) + timedelta(hours=5)
        except ValueError:
            continue
        if t < lim:
            continue
        f = dir_cache / nom
        if not f.exists():
            try:
                f.write_bytes(urllib.request.urlopen(BASE_URL + ruta, timeout=30).read())
            except Exception as e:
                log.warning("IDEAM %s: %s", nom, e)
                continue
        salida.append((t, f))
    return salida


def cargar_cuadros(malla, dir_cache: Path, ventana_min: int = 120) -> tuple[list[Cuadro], np.ndarray]:
    """Cuadros de 10 min (mosaico de los radares IDEAM) y la máscara de cobertura."""
    dir_cache.mkdir(parents=True, exist_ok=True)
    cols, dbz = _paleta()
    por_hora = {}
    for nombre in RADARES:
        try:
            lista = _cuadros_radar(nombre, dir_cache, ventana_min)
        except Exception as e:
            log.warning("IDEAM %s sin lista: %s", nombre, e)
            continue
        r, c, dentro = _mapeo(malla, nombre)
        for t, f in lista:
            slot = int(t.timestamp()) // 600 * 600
            campo = por_hora.setdefault(slot, np.zeros((malla.alto, malla.ancho), np.uint8))
            img = _a_dbz(np.array(Image.open(f).convert("RGBA")), cols, dbz)
            np.maximum(campo, np.where(dentro, img[r, c], 0).astype(np.uint8), out=campo)
    lim = (datetime.now(timezone.utc) - timedelta(hours=3)).timestamp()
    for f in dir_cache.glob("*_z_Transp.png"):
        if f.stat().st_mtime < lim:
            f.unlink(missing_ok=True)
    cuadros = [Cuadro(t, d) for t, d in sorted(por_hora.items())]
    log.info("IDEAM: %d cuadros de radar (%s)", len(cuadros), ", ".join(RADARES))
    return cuadros, cobertura(malla)


def combinar(base: list[Cuadro], ideam: list[Cuadro], cob: np.ndarray, tolerancia_s: int = 600) -> list[Cuadro]:
    """Dentro del alcance de los radares IDEAM se usa su reflectividad; fuera, la fuente base."""
    if not ideam:
        return base
    out = []
    for b in base:
        cand = min(ideam, key=lambda c: abs(c.tiempo - b.tiempo))
        if abs(cand.tiempo - b.tiempo) <= tolerancia_s:
            out.append(Cuadro(b.tiempo, np.where(cob, cand.dbz, b.dbz).astype(np.uint8)))
        else:
            out.append(b)
    return out
