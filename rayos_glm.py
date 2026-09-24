"""
rayos_glm.py — Rayos detectados por el satélite GOES-19 (sensor GLM), casi en tiempo real.

NOAA publica un archivo cada 20 s en s3://noaa-goes19/GLM-L2-LCFA/ (datos abiertos, ~4 min de
retraso). Se leen los "flashes" de los últimos minutos sobre Colombia y se guardan como
GeoJSON para el portal. El motor de alertas usa el conteo por departamento para confirmar
tormentas (y descartar ecos falsos del radar).
"""
from __future__ import annotations

import concurrent.futures as cf
import io
import json
import logging
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

log = logging.getLogger("rayos")
BUCKET = "https://noaa-goes19.s3.amazonaws.com"
CAJA = (-82.0, -5.0, -66.0, 14.0)   # lon_min, lat_min, lon_max, lat_max (Colombia + margen)


def _listar(prefijo: str) -> list[str]:
    xml = urllib.request.urlopen(f"{BUCKET}/?list-type=2&prefix={prefijo}&max-keys=1000", timeout=30).read().decode()
    return re.findall(r"<Key>([^<]+)</Key>", xml)


def _inicio(clave: str) -> datetime:
    m = re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", clave)
    y, j, h, mi, s = map(int, m.groups())
    return datetime(y, 1, 1, h, mi, s, tzinfo=timezone.utc) + timedelta(days=j - 1)


def _valor(ds):
    v = ds[()]
    a = ds.attrs
    if a.get("_Unsigned") in (b"true", "true") and v.dtype.kind == "i":
        v = v.astype(v.dtype.str.replace("i", "u"))
    v = v.astype("float64")
    if "scale_factor" in a:
        v = v * float(np.ravel(a["scale_factor"])[0])
    if "add_offset" in a:
        v = v + float(np.ravel(a["add_offset"])[0])
    return v


def _leer(clave: str):
    import h5py
    datos = urllib.request.urlopen(f"{BUCKET}/{clave}", timeout=60).read()
    with h5py.File(io.BytesIO(datos), "r") as f:
        if "flash_lat" not in f or f["flash_lat"].shape[0] == 0:
            return None
        lat, lon = _valor(f["flash_lat"]), _valor(f["flash_lon"])
        ener = _valor(f["flash_energy"]) if "flash_energy" in f else np.zeros_like(lat)
        cal = f["flash_quality_flag"][()] if "flash_quality_flag" in f else np.zeros(lat.shape, int)
    t = _inicio(clave).timestamp()
    ok = (cal == 0) & (lon >= CAJA[0]) & (lon <= CAJA[2]) & (lat >= CAJA[1]) & (lat <= CAJA[3])
    return np.column_stack([lon[ok], lat[ok], np.full(ok.sum(), t), ener[ok] * 1e15])


def descargar_rayos(minutos: int = 30, ahora: datetime | None = None) -> np.ndarray:
    """Matriz (n, 4): lon, lat, tiempo unix, energía (fJ)."""
    ahora = ahora or datetime.now(timezone.utc)
    desde = ahora - timedelta(minutes=minutos)
    claves = []
    for h in {desde.replace(minute=0, second=0, microsecond=0), ahora.replace(minute=0, second=0, microsecond=0)}:
        claves += _listar(f"GLM-L2-LCFA/{h:%Y}/{h.timetuple().tm_yday:03d}/{h:%H}/")
    claves = [c for c in claves if _inicio(c) >= desde]
    partes = []
    with cf.ThreadPoolExecutor(8) as ex:
        for r in ex.map(lambda c: _leer_seguro(c), claves):
            if r is not None and len(r):
                partes.append(r)
    log.info("GLM: %d archivos, %d rayos sobre Colombia", len(claves), sum(len(p) for p in partes))
    return np.vstack(partes) if partes else np.zeros((0, 4))


def _leer_seguro(c):
    try:
        return _leer(c)
    except Exception as e:
        log.warning("GLM %s: %s", c.split("/")[-1], e)
        return None


def guardar_geojson(rayos: np.ndarray, ruta: Path, ahora: datetime | None = None):
    ahora = ahora or datetime.now(timezone.utc)
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [round(lo, 3), round(la, 3)]},
              "properties": {"t": int(t), "edad_min": round((ahora.timestamp() - t) / 60, 1), "e_fj": round(e, 1)}}
             for lo, la, t, e in rayos]
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps({"type": "FeatureCollection", "generado": ahora.isoformat(),
                                "fuente": "NOAA GOES-19 GLM L2 (LCFA)", "ventana_min": 30,
                                "features": feats}, separators=(",", ":")), encoding="utf-8")


def conteo_por_departamento(rayos: np.ndarray, territorio, minutos: int = 15) -> dict[int, int]:
    """Rayos de los últimos `minutos` por índice de departamento del Territorio."""
    if not len(rayos):
        return {}
    t_lim = datetime.now(timezone.utc).timestamp() - minutos * 60
    r = rayos[rayos[:, 2] >= t_lim]
    if not len(r):
        return {}
    px, py = territorio.malla.a_pixel(r[:, 0], r[:, 1])
    px, py = px.astype(int), py.astype(int)
    ok = (px >= 0) & (py >= 0) & (px < territorio.malla.ancho) & (py < territorio.malla.alto)
    deps = territorio.dep_raster[py[ok], px[ok]]
    return {int(d): int(n) for d, n in zip(*np.unique(deps[deps > 0], return_counts=True))}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    r = descargar_rayos(30)
    guardar_geojson(r, Path("salida/rayos.geojson"))
    print(len(r), "rayos")
