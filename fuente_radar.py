"""
fuente_radar.py — Lectura del radar de RainViewer (API pública).

Descarga los cuadros de radar (uno cada 10 min, últimas 2 h), convierte los colores
del esquema "Universal Blue" a dBZ con la tabla oficial de RainViewer y guarda cada
cuadro en caché para no volver a descargarlo en la siguiente ejecución.

Si más adelante se cambia la fuente (radares del IDEAM, satélite GOES, etc.), solo hay
que reemplazar este módulo manteniendo `cargar_cuadros()` y la clase `Malla`.
"""
from __future__ import annotations

import io
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
from PIL import Image

log = logging.getLogger("radar")

CIRCUNFERENCIA_KM = 40075.016686

# Esquema 2 "Universal Blue" (lluvia). Color RGB -> dBZ (límite inferior de la clase de 5 dBZ).
# Fuente: tabla oficial https://www.rainviewer.com/files/rainviewer_api_colors_table.csv
# Los ecos débiles (< 10 dBZ, colores grises semitransparentes) se toman como "sin lluvia" (0).
PALETA_UNIVERSAL_BLUE = {
    (0x63, 0x61, 0x59): 0, (0x79, 0x74, 0x60): 0, (0x92, 0x88, 0x71): 0, (0xCE, 0xC0, 0x87): 0,
    (0x88, 0xDD, 0xEE): 10, (0x00, 0x99, 0xCC): 15, (0x00, 0x77, 0xAA): 20, (0x00, 0x55, 0x88): 25,
    (0xFF, 0xEE, 0x00): 30, (0xFF, 0xAA, 0x00): 35, (0xFF, 0x77, 0x00): 40, (0xFF, 0x44, 0x00): 45,
    (0xEE, 0x00, 0x00): 50, (0x99, 0x00, 0x00): 55, (0xFF, 0xAA, 0xFF): 60, (0xFF, 0x77, 0xFF): 65,
    (0xFF, 0x44, 0xFF): 70, (0xFF, 0x00, 0xFF): 75, (0xAA, 0x00, 0xAA): 80,
}
_COLORES = np.array(list(PALETA_UNIVERSAL_BLUE.keys()), dtype=np.int32)
_VALORES = np.array(list(PALETA_UNIVERSAL_BLUE.values()), dtype=np.uint8)
ESQUEMA_COLOR = 2          # único esquema disponible en la API gratuita desde 2026
OPCIONES_TESELA = "0_0"     # sin suavizado, sin distinguir nieve (datos "crudos")


# --------------------------------------------------------------------------- malla
@dataclass(frozen=True)
class Malla:
    """Conjunto rectangular de teselas Web Mercator (zoom z, teselas de `tam` px)."""
    z: int
    tam: int
    x0: int
    x1: int
    y0: int
    y1: int

    @staticmethod
    def _global(lon, lat, z, tam):
        n = tam * (2 ** z)
        lat_r = np.radians(np.clip(lat, -85.0, 85.0))
        gx = (np.asarray(lon) + 180.0) / 360.0 * n
        gy = (1.0 - np.log(np.tan(lat_r) + 1.0 / np.cos(lat_r)) / math.pi) / 2.0 * n
        return gx, gy

    @classmethod
    def desde_bbox(cls, lon_min, lat_min, lon_max, lat_max, z=6, tam=512):
        gx0, gy0 = cls._global(lon_min, lat_max, z, tam)
        gx1, gy1 = cls._global(lon_max, lat_min, z, tam)
        return cls(z, tam, int(gx0 // tam), int(gx1 // tam), int(gy0 // tam), int(gy1 // tam))

    @property
    def ancho(self) -> int:
        return (self.x1 - self.x0 + 1) * self.tam

    @property
    def alto(self) -> int:
        return (self.y1 - self.y0 + 1) * self.tam

    @property
    def clave(self) -> str:
        return f"z{self.z}_{self.tam}_{self.x0}-{self.x1}_{self.y0}-{self.y1}"

    def a_pixel(self, lon, lat):
        """lon/lat -> coordenadas de píxel dentro de la malla (float)."""
        gx, gy = self._global(lon, lat, self.z, self.tam)
        return gx - self.x0 * self.tam, gy - self.y0 * self.tam

    def a_lonlat(self, px, py):
        """Píxel local -> lon/lat."""
        n = self.tam * (2 ** self.z)
        gx = np.asarray(px, dtype=float) + self.x0 * self.tam
        gy = np.asarray(py, dtype=float) + self.y0 * self.tam
        lon = gx / n * 360.0 - 180.0
        lat = np.degrees(np.arctan(np.sinh(math.pi * (1.0 - 2.0 * gy / n))))
        return lon, lat

    def km_por_pixel_filas(self) -> np.ndarray:
        """Tamaño del píxel (km) para cada fila, según la latitud del centro de la fila."""
        filas = np.arange(self.alto) + 0.5
        _, lat = self.a_lonlat(np.zeros_like(filas), filas)
        n = self.tam * (2 ** self.z)
        return CIRCUNFERENCIA_KM * np.cos(np.radians(lat)) / n

    def teselas(self):
        for ty in range(self.y0, self.y1 + 1):
            for tx in range(self.x0, self.x1 + 1):
                yield tx, ty


# --------------------------------------------------------------------------- descarga
class Descargador:
    """HTTP con límite de solicitudes por minuto y reintentos (RainViewer: 100/IP/min)."""

    def __init__(self, max_por_minuto: int = 60, timeout: int = 20, reintentos: int = 3):
        self.intervalo = 60.0 / max(1, max_por_minuto)
        self.timeout = timeout
        self.reintentos = reintentos
        self._ultimo = 0.0
        self.sesion = requests.Session()
        self.sesion.headers["User-Agent"] = "FENALCE-alertas-lluvia/1.0"
        self.solicitudes = 0

    def obtener(self, url: str) -> bytes | None:
        """Devuelve el contenido, o None si el recurso no existe (404)."""
        ultimo_error = None
        for intento in range(self.reintentos + 1):
            espera = self._ultimo + self.intervalo - time.monotonic()
            if espera > 0:
                time.sleep(espera)
            self._ultimo = time.monotonic()
            self.solicitudes += 1
            try:
                r = self.sesion.get(url, timeout=self.timeout)
            except requests.RequestException as e:
                ultimo_error = e
                time.sleep(2 * (intento + 1))
                continue
            if r.status_code == 404:
                return None
            if r.status_code == 429:
                ultimo_error = "429 (límite de solicitudes)"
                log.warning("RainViewer respondió 429; esperando antes de reintentar…")
                time.sleep(20 * (intento + 1))
                continue
            if r.status_code >= 500:
                ultimo_error = f"HTTP {r.status_code}"
                time.sleep(3 * (intento + 1))
                continue
            r.raise_for_status()
            return r.content
        raise RuntimeError(f"No se pudo descargar {url}: {ultimo_error}")

    def obtener_json(self, url: str):
        contenido = self.obtener(url)
        if contenido is None:
            raise RuntimeError(f"No existe {url}")
        import json
        return json.loads(contenido)


# --------------------------------------------------------------------------- decodificación
def decodificar_png(contenido: bytes) -> np.ndarray:
    """PNG de RainViewer (Universal Blue) -> matriz uint8 de dBZ (0 = sin lluvia)."""
    a = np.asarray(Image.open(io.BytesIO(contenido)).convert("RGBA"))
    rgb = a[..., :3].astype(np.int32)
    clave = (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]
    clave = np.where(a[..., 3] >= 128, clave, -1).ravel()
    unicos, inversa = np.unique(clave, return_inverse=True)
    valores = np.zeros(len(unicos), dtype=np.uint8)
    for i, k in enumerate(unicos):
        if k < 0:
            continue
        color = ((k >> 16) & 255, (k >> 8) & 255, k & 255)
        v = PALETA_UNIVERSAL_BLUE.get(color)
        if v is None:  # color no exacto (p. ej., bordes suavizados): el más cercano de la tabla
            d = ((_COLORES - np.array(color)) ** 2).sum(axis=1)
            j = int(np.argmin(d))
            v = int(_VALORES[j]) if d[j] <= 40 ** 2 else 0
        valores[i] = v
    return valores[inversa].reshape(a.shape[:2])


def _descargar_mosaico(descargador: Descargador, malla: Malla, plantilla: str, decodificar) -> np.ndarray:
    """Descarga todas las teselas de la malla y arma el mosaico."""
    mosaico = np.zeros((malla.alto, malla.ancho), dtype=np.uint8)
    faltantes = 0
    for tx, ty in malla.teselas():
        contenido = descargador.obtener(plantilla.format(z=malla.z, x=tx, y=ty, tam=malla.tam))
        if contenido is None:
            faltantes += 1
            continue
        tesela = decodificar(contenido)
        if tesela.shape != (malla.tam, malla.tam):  # por si el servidor entrega otro tamaño
            tesela = np.asarray(Image.fromarray(tesela).resize((malla.tam, malla.tam), Image.NEAREST))
        fy, fx = (ty - malla.y0) * malla.tam, (tx - malla.x0) * malla.tam
        mosaico[fy:fy + malla.tam, fx:fx + malla.tam] = tesela
    total = (malla.x1 - malla.x0 + 1) * (malla.y1 - malla.y0 + 1)
    if faltantes == total:
        raise RuntimeError("Ninguna tesela disponible (¿cambió el formato de la API?)")
    return mosaico


# --------------------------------------------------------------------------- cuadros
@dataclass
class Cuadro:
    tiempo: int          # Unix UTC (inicio del cuadro de 10 min)
    dbz: np.ndarray      # uint8 (alto, ancho); múltiplos de 5; 0 = sin lluvia


def cargar_cuadros(api_url: str, malla: Malla, dir_cache: Path, descargador: Descargador,
                   ventana_min: int = 120) -> list[Cuadro]:
    """Lista los cuadros disponibles y los carga (desde caché o descargándolos)."""
    info = descargador.obtener_json(api_url)
    host = info["host"].rstrip("/")
    pasados = sorted(info.get("radar", {}).get("past", []), key=lambda f: f["time"])
    if not pasados:
        raise RuntimeError("La API no devolvió cuadros de radar")
    t_ultimo = pasados[-1]["time"]
    pasados = [f for f in pasados if f["time"] >= t_ultimo - ventana_min * 60]

    dir_cache = Path(dir_cache)
    dir_cache.mkdir(parents=True, exist_ok=True)
    cuadros: list[Cuadro] = []
    for f in pasados:
        archivo = dir_cache / f"{f['time']}_{malla.clave}.npz"
        if archivo.exists():
            try:
                cuadros.append(Cuadro(f["time"], np.load(archivo)["dbz"]))
                continue
            except Exception:  # caché dañada: se vuelve a descargar
                archivo.unlink(missing_ok=True)
        plantilla = f"{host}{f['path']}/{{tam}}/{{z}}/{{x}}/{{y}}/{ESQUEMA_COLOR}/{OPCIONES_TESELA}.png"
        try:
            dbz = _descargar_mosaico(descargador, malla, plantilla, decodificar_png)
        except Exception as e:
            log.warning("Cuadro %s omitido: %s", f["time"], e)
            continue
        np.savez_compressed(archivo, dbz=dbz)
        cuadros.append(Cuadro(f["time"], dbz))
        log.info("Cuadro %s descargado (%d píxeles con lluvia)", f["time"], int((dbz >= 20).sum()))

    # limpiar caché vieja
    limite = t_ultimo - (ventana_min + 60) * 60
    for archivo in dir_cache.glob("*.npz"):
        try:
            if int(archivo.name.split("_")[0]) < limite:
                archivo.unlink()
        except ValueError:
            pass
    return cuadros


def cargar_cobertura(api_url: str, malla: Malla, descargador: Descargador) -> np.ndarray:
    """Máscara booleana: True donde RainViewer tiene cobertura de radar."""
    host = descargador.obtener_json(api_url)["host"].rstrip("/")
    plantilla = f"{host}/v2/coverage/0/{{tam}}/{{z}}/{{x}}/{{y}}/0/0_0.png"

    def alfa(contenido: bytes) -> np.ndarray:
        a = np.asarray(Image.open(io.BytesIO(contenido)).convert("RGBA"))
        return (a[..., 3] > 0).astype(np.uint8)

    return _descargar_mosaico(descargador, malla, plantilla, alfa).astype(bool)
