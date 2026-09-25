"""
fuente_goes.py — Lluvia estimada por el satélite GOES-19 (producto ABI-L2-RRQPEF de NOAA).

Es el mismo dato que la página muestra como "radar" sobre Colombia (LibreWXR lo usa como relleno
donde no hay radares), pero leído directo de NOAA: tasa de lluvia en mm/h a ~2 km, cada 10 min,
sin pasar por colores de una imagen. Datos abiertos: s3://noaa-goes19/ABI-L2-RRQPEF/

Para reutilizar todo el análisis del motor (que trabaja en dBZ), la tasa se convierte a un
"dBZ equivalente" con Marshall-Palmer (Z = 200·R^1.6), redondeado a clases de 5 dBZ.
"""
from __future__ import annotations

import concurrent.futures as cf
import io
import logging
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from fuente_radar import Cuadro

log = logging.getLogger("goes")
BUCKET = "https://noaa-goes19.s3.amazonaws.com"
PRODUCTO = "ABI-L2-RRQPEF"
_INDICE = {}   # malla.clave -> (iy, ix, valido)


def _listar(prefijo: str) -> list[str]:
    xml = urllib.request.urlopen(f"{BUCKET}/?list-type=2&prefix={prefijo}&max-keys=1000", timeout=30).read().decode()
    return re.findall(r"<Key>([^<]+)</Key>", xml)


def _inicio(clave: str) -> datetime:
    y, j, h, mi, s = map(int, re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", clave).groups())
    return datetime(y, 1, 1, h, mi, s, tzinfo=timezone.utc) + timedelta(days=j - 1)


def _escalar(ds, recorte=None):
    v = ds[recorte] if recorte is not None else ds[()]
    a = ds.attrs
    fill = a.get("_FillValue")
    if a.get("_Unsigned") in (b"true", "true") and v.dtype.kind == "i":
        v = v.view(v.dtype.str.replace("i", "u"))
        if fill is not None:
            fill = np.array(fill).astype(v.dtype)
    malo = (v == np.ravel(fill)[0]) if fill is not None else np.zeros(v.shape, bool)
    v = v.astype("float64")
    if "scale_factor" in a:
        v = v * float(np.ravel(a["scale_factor"])[0])
    if "add_offset" in a:
        v = v + float(np.ravel(a["add_offset"])[0])
    return v, malo


def _indice_malla(malla, f):
    """Para cada píxel de la malla del motor, el píxel del disco GOES que le corresponde."""
    if malla.clave in _INDICE:
        return _INDICE[malla.clave]
    p = f["goes_imager_projection"].attrs
    req = float(np.ravel(p["semi_major_axis"])[0]); rpol = float(np.ravel(p["semi_minor_axis"])[0])
    H = float(np.ravel(p["perspective_point_height"])[0]) + req
    lon0 = np.radians(float(np.ravel(p["longitude_of_projection_origin"])[0]))
    x, _ = _escalar(f["x"]); y, _ = _escalar(f["y"])
    py, px = np.mgrid[0:malla.alto, 0:malla.ancho]
    lon, lat = malla.a_lonlat(px + 0.5, py + 0.5)
    lon, lat = np.radians(lon), np.radians(lat)
    e2 = (req ** 2 - rpol ** 2) / req ** 2
    latc = np.arctan((rpol ** 2 / req ** 2) * np.tan(lat))
    rc = rpol / np.sqrt(1 - e2 * np.cos(latc) ** 2)
    sx = H - rc * np.cos(latc) * np.cos(lon - lon0)
    sy = -rc * np.cos(latc) * np.sin(lon - lon0)
    sz = rc * np.sin(latc)
    ang_y = np.arctan(sz / sx)
    ang_x = np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2))
    ix = np.rint((ang_x - x[0]) / (x[1] - x[0])).astype(np.int32)
    iy = np.rint((ang_y - y[0]) / (y[1] - y[0])).astype(np.int32)
    valido = (ix >= 0) & (iy >= 0) & (ix < len(x)) & (iy < len(y))
    ix, iy = np.clip(ix, 0, len(x) - 1), np.clip(iy, 0, len(y) - 1)
    _INDICE[malla.clave] = (iy, ix, valido)
    return _INDICE[malla.clave]


def a_dbz(rr: np.ndarray) -> np.ndarray:
    """mm/h -> dBZ equivalente (Marshall-Palmer), clases de 5 dBZ; 0 = sin lluvia."""
    with np.errstate(divide="ignore"):
        z = 10 * np.log10(200 * np.power(np.maximum(rr, 1e-6), 1.6))
    d = (np.floor(z / 5) * 5).clip(0, 75)
    d[(rr < 0.1) | ~np.isfinite(z) | (d < 10)] = 0
    return d.astype(np.uint8)


def _leer(clave: str, malla) -> np.ndarray:
    import h5py
    datos = urllib.request.urlopen(f"{BUCKET}/{clave}", timeout=120).read()
    with h5py.File(io.BytesIO(datos), "r") as f:
        iy, ix, valido = _indice_malla(malla, f)
        y0, y1, x0, x1 = iy[valido].min(), iy[valido].max() + 1, ix[valido].min(), ix[valido].max() + 1
        rr, malo = _escalar(f["RRQPE"], (slice(y0, y1), slice(x0, x1)))
        if "DQF" in f:
            malo |= f["DQF"][y0:y1, x0:x1] != 0
    rr[malo] = 0
    campo = rr[iy - y0, ix - x0] if valido.all() else np.where(valido, rr[np.clip(iy - y0, 0, y1 - y0 - 1), np.clip(ix - x0, 0, x1 - x0 - 1)], 0)
    return a_dbz(campo)


def cargar_cuadros(malla, dir_cache: Path, ventana_min: int = 120, ahora: datetime | None = None) -> list[Cuadro]:
    """Cuadros de las últimas `ventana_min` (uno cada 10 min), desde caché o NOAA."""
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(minutes=ventana_min + 10)
    claves, h = [], desde.replace(minute=0, second=0, microsecond=0)
    while h <= ahora:
        claves += _listar(f"{PRODUCTO}/{h:%Y}/{h.timetuple().tm_yday:03d}/{h:%H}/")
        h += timedelta(hours=1)
    claves = sorted(c for c in claves if _inicio(c) >= desde)
    dir_cache.mkdir(parents=True, exist_ok=True)
    cuadros, faltan = [], []
    for c in claves:
        t = int(_inicio(c).timestamp()) // 600 * 600
        archivo = dir_cache / f"goes_{t}_{malla.clave}.npz"
        if archivo.exists():
            cuadros.append(Cuadro(t, np.load(archivo)["dbz"]))
        else:
            faltan.append((c, t, archivo))
    if faltan:   # el primero solo, para calcular el índice de la malla una vez
        def bajar(par):
            c, t, archivo = par
            try:
                d = _leer(c, malla)
                np.savez_compressed(archivo, dbz=d)
                log.info("GOES %s: %d píxeles con lluvia", datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M"), int((d >= 10).sum()))
                return Cuadro(t, d)
            except Exception as e:
                log.warning("GOES %s: %s", c.split("/")[-1], e)
                return None
        primero = bajar(faltan[0])
        resto = []
        with cf.ThreadPoolExecutor(4) as ex:
            resto = list(ex.map(bajar, faltan[1:]))
        cuadros += [c for c in [primero] + resto if c is not None]
    lim = (ahora - timedelta(hours=4)).timestamp()
    for a in dir_cache.glob("goes_*.npz"):
        if int(a.name.split("_")[1]) < lim:
            a.unlink(missing_ok=True)
    cuadros.sort(key=lambda c: c.tiempo)
    return cuadros
