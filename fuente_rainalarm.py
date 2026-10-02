"""
fuente_rainalarm.py — Lluvia que está cayendo ahora según el radar Rain-Alarm (el mismo del portal).

Descarga las teselas z7 de images.rain-alarm.com que cubren la malla del motor y las convierte a
clases de lluvia:  0 = nada / solo nube (gris), 1 = débil (azul, cian), 2 = moderada (verdes),
3 = fuerte (amarillo y más). Se usa para nombrar los municipios donde ya llueve y para guardar
una bitácora que luego se valida contra las estaciones del IDEAM.
"""
from __future__ import annotations

import io
import logging
import math
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np
from PIL import Image

log = logging.getLogger("rainalarm")
Z = 7
URL = "https://images.rain-alarm.com/rain/g2/z{z}/{bx}_{by}/{x}_{y}.png"
H = {"User-Agent": "Mozilla/5.0 (FENALCE agroclimatologia)", "Referer": "https://agroclima-fenalce-portal.vercel.app/"}
_MAPEO = {}


def _clase(rgb: np.ndarray) -> np.ndarray:
    """Color -> clase de lluvia (0-3)."""
    r, g, b = (rgb[..., i].astype(int) for i in range(3))
    gris = (abs(r - g) < 12) & (abs(g - b) < 12)
    out = np.zeros(r.shape, np.uint8)
    azul = (b > 150) & (r < 90)                                    # azul y cian: débil
    verde = (g > 100) & (r < 90) & (b < 90)                        # verdes: moderada
    fuerte = (r > 180) & (b < 120)                                 # amarillo, naranja, rojo
    magenta = (r > 150) & (b > 150) & (g < 120)                    # morado/magenta: muy fuerte
    out[azul] = 1
    out[verde] = 2
    out[fuerte | magenta] = 3
    out[gris] = 0
    return out


def _tesela(x, y):
    u = URL.format(z=Z, bx=x // 8, by=y // 8, x=x, y=y)
    try:
        b = urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=30).read()
        im = np.array(Image.open(io.BytesIO(b)).convert("RGBA"))
        c = _clase(im[..., :3])
        c[im[..., 3] == 0] = 0
        return x, y, c
    except Exception as e:
        log.debug("Rain-Alarm %s: %s", u, e)
        return x, y, None


def _mapeo(malla):
    """Posición global (píxel z7) de cada píxel de la malla del motor."""
    if malla.clave in _MAPEO:
        return _MAPEO[malla.clave]
    py, px = np.mgrid[0:malla.alto, 0:malla.ancho]
    lon, lat = malla.a_lonlat(px + 0.5, py + 0.5)
    n = 256 * 2 ** Z
    gx = ((lon + 180) / 360 * n).astype(np.int64)
    s = np.sin(np.radians(np.clip(lat, -85, 85)))
    gy = ((0.5 - np.log((1 + s) / (1 - s)) / (4 * math.pi)) * n).astype(np.int64)
    _MAPEO[malla.clave] = (gx, gy)
    return gx, gy


def lluvia_actual(malla) -> np.ndarray | None:
    """Clases de lluvia (0-3) sobre la malla del motor con la imagen más reciente de Rain-Alarm."""
    gx, gy = _mapeo(malla)
    tx0, tx1, ty0, ty1 = gx.min() // 256, gx.max() // 256, gy.min() // 256, gy.max() // 256
    pares = [(x, y) for x in range(tx0, tx1 + 1) for y in range(ty0, ty1 + 1)]
    mosaico = np.zeros(((ty1 - ty0 + 1) * 256, (tx1 - tx0 + 1) * 256), np.uint8)
    ok = 0
    with ThreadPoolExecutor(8) as ex:
        for x, y, c in ex.map(lambda p: _tesela(*p), pares):
            if c is not None:
                ok += 1
                mosaico[(y - ty0) * 256:(y - ty0 + 1) * 256, (x - tx0) * 256:(x - tx0 + 1) * 256] = c
    if ok < len(pares) * 0.7:
        log.warning("Rain-Alarm: solo %d de %d teselas", ok, len(pares))
        return None
    log.info("Rain-Alarm: %d teselas", ok)
    return mosaico[gy - ty0 * 256, gx - tx0 * 256]
