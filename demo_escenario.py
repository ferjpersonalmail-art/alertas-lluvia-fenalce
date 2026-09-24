"""
demo_escenario.py — Escenario ficticio para ver el sistema funcionando sin internet.

Genera cuadros de radar sintéticos con la misma paleta de RainViewer (tormentas que se
forman, se mueven y se disipan), los sirve desde un servidor local que imita la API y
corre el motor cada "10 minutos" simulados. Muestra los mensajes que llegarían al
celular. Nada se envía y no se toca el estado real.

    python motor_alertas.py --modo demo
"""
from __future__ import annotations

import copy
import io
import json
import logging
import math
import re
import shutil
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import numpy as np
from PIL import Image

import fuente_radar as fr

log = logging.getLogger("demo")

# Tormentas ficticias: posición inicial (lon, lat), minutos relativos a la hora de referencia,
# evolución del pico de dBZ, radio (km), rumbo hacia el que se mueven (grados) y velocidad (km/h).
TORMENTAS = [
    # Tolima: se forma sobre el valle del Magdalena, se intensifica y avanza hacia Ibagué
    dict(nombre="Tolima", lon=-74.93, lat=4.55, t_ini=-90, t_fin=70,
         pico=[(-90, 38), (10, 57), (20, 57), (60, 25)], radio=[(-90, 10), (10, 16), (60, 16)], rumbo=250, vel=18),
    # Meta: tormenta que avanza hacia el noroccidente, hacia el piedemonte
    dict(nombre="Meta", lon=-73.20, lat=3.93, t_ini=-70, t_fin=40,
         pico=[(-70, 40), (-40, 48), (20, 48), (40, 30)], radio=[(-70, 10), (-40, 14), (40, 14)], rumbo=300, vel=25),
    # Chocó: lluvia fuerte casi estacionaria y persistente cerca de Quibdó
    dict(nombre="Chocó", lon=-76.64, lat=5.70, t_ini=-40, t_fin=120,
         pico=[(-40, 30), (-10, 47), (120, 47)], radio=[(-40, 6), (-10, 9), (120, 9)], rumbo=0, vel=1),
    # Nariño: sistema amplio sobre la costa pacífica
    dict(nombre="Nariño", lon=-78.45, lat=1.75, t_ini=-120, t_fin=60,
         pico=[(-120, 42), (-30, 44), (60, 40)], radio=[(-120, 25), (-30, 28), (60, 28)], rumbo=90, vel=8),
    # Córdoba: aguacero pequeño (no debe generar alerta)
    dict(nombre="Córdoba", lon=-75.90, lat=8.70, t_ini=-60, t_fin=60,
         pico=[(-60, 33), (60, 33)], radio=[(-60, 6), (60, 6)], rumbo=45, vel=10),
    # Llanos: lluvias débiles dispersas (no deben generar alerta)
    dict(nombre="Casanare", lon=-71.8, lat=5.3, t_ini=-120, t_fin=120,
         pico=[(-120, 27), (120, 27)], radio=[(-120, 20), (120, 20)], rumbo=270, vel=15),
]


def _interp(serie, t):
    xs, ys = zip(*serie)
    return float(np.interp(t, xs, ys))


class Escenario:
    def __init__(self, malla: fr.Malla, t_ref: int, semilla: int = 7):
        self.malla = malla
        self.t_ref = t_ref
        rng = np.random.default_rng(semilla)
        # textura suave (±1) para que las tormentas no sean círculos perfectos
        base = rng.normal(size=(malla.alto // 32 + 2, malla.ancho // 32 + 2)).astype(np.float32)
        img = Image.fromarray(base).resize((malla.ancho, malla.alto), Image.BILINEAR)
        self.textura = np.asarray(img) / 1.5
        self.km_px = malla.km_por_pixel_filas()
        self.rng = rng
        self._cache: dict[int, np.ndarray] = {}

    def dbz(self, ts: int) -> np.ndarray:
        if ts in self._cache:
            return self._cache[ts]
        m = self.malla
        campo = np.zeros((m.alto, m.ancho), dtype=np.float32)
        minutos = (ts - self.t_ref) / 60
        for s in TORMENTAS:
            if not (s["t_ini"] <= minutos <= s["t_fin"]):
                continue
            h = (minutos - s["t_ini"]) / 60
            dist = s["vel"] * h
            lat = s["lat"] + dist * math.cos(math.radians(s["rumbo"])) / 110.57
            lon = s["lon"] + dist * math.sin(math.radians(s["rumbo"])) / (111.32 * math.cos(math.radians(lat)))
            pico, radio = _interp(s["pico"], minutos), _interp(s["radio"], minutos)
            cx, cy = m.a_pixel(lon, lat)
            km = float(self.km_px[int(np.clip(cy, 0, m.alto - 1))])
            rp = 2.0 * radio / km
            y0, y1 = int(max(0, cy - rp)), int(min(m.alto, cy + rp + 1))
            x0, x1 = int(max(0, cx - rp)), int(min(m.ancho, cx + rp + 1))
            if y0 >= y1 or x0 >= x1:
                continue
            yy, xx = np.mgrid[y0:y1, x0:x1]
            r_km = np.hypot((xx - cx) * km, (yy - cy) * km * 1.25)   # ligeramente elíptica
            valor = pico - 20 * (r_km / radio) ** 2 + 3 * self.textura[y0:y1, x0:x1]
            np.maximum(campo[y0:y1, x0:x1], valor, out=campo[y0:y1, x0:x1])
        # píxeles espurios aislados (el motor debe descartarlos)
        rng = np.random.default_rng(ts % 100000)
        ys, xs = rng.integers(0, m.alto, 400), rng.integers(0, m.ancho, 400)
        campo[ys, xs] = np.maximum(campo[ys, xs], rng.integers(35, 50, 400))
        dbz = (np.floor(np.clip(campo, 0, 80) / 5) * 5).astype(np.uint8)
        self._cache[ts] = dbz
        return dbz

    def rgba(self, ts: int) -> np.ndarray:
        dbz = self.dbz(ts)
        rgba = np.zeros(dbz.shape + (4,), dtype=np.uint8)
        for color, valor in fr.PALETA_UNIVERSAL_BLUE.items():
            if valor >= 10:
                rgba[dbz == valor] = (*color, 255)
        rgba[dbz == 5] = (0xCE, 0xC0, 0x87, 0xCC)       # eco débil (<10 dBZ): debe ignorarse
        return rgba


def _servidor(escenario: Escenario, reloj: dict):
    patron = re.compile(r"^/v2/radar/(\d+)/(\d+)/(\d+)/(\d+)/(\d+)/\d+/[01]_[01]\.png$")
    teselas: dict[tuple, bytes] = {}

    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            if self.path.startswith("/public/weather-maps.json"):
                ahora = reloj["t"]
                pasados = [{"time": t, "path": f"/v2/radar/{t}"} for t in range(ahora - 7200, ahora + 1, 600)]
                cuerpo = json.dumps({"version": "2.0", "generated": ahora, "host": reloj["host"],
                                     "radar": {"past": pasados, "nowcast": []}}).encode()
                return self._enviar(cuerpo, "application/json")
            m = patron.match(self.path)
            if not m:
                self.send_response(404)
                self.end_headers()
                return
            ts, tam, z, x, y = map(int, m.groups())
            mm = escenario.malla
            if z != mm.z or tam != mm.tam or not (mm.x0 <= x <= mm.x1 and mm.y0 <= y <= mm.y1):
                self.send_response(404)
                self.end_headers()
                return
            clave = (ts, x, y)
            if clave not in teselas:
                fy, fx = (y - mm.y0) * tam, (x - mm.x0) * tam
                recorte = escenario.rgba(ts)[fy:fy + tam, fx:fx + tam]
                buf = io.BytesIO()
                Image.fromarray(recorte).save(buf, "PNG")
                teselas[clave] = buf.getvalue()
            self._enviar(teselas[clave], "image/png")

        def _enviar(self, cuerpo: bytes, tipo: str):
            self.send_response(200)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Manejador)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _viento_ficticio(puntos, incluir_pronostico=False):
    return [{"viento_kmh": 14.0, "viento_dir": 75.0, "rafaga_kmh": 31.0} for _ in puntos]


def correr(cfg: dict, pasos=range(-60, 101, 10), dir_salida: Path | None = None) -> int:
    import motor_alertas as motor

    cfg = copy.deepcopy(cfg)
    cfg["radar"]["max_solicitudes_por_minuto"] = 100000
    activos = sorted(motor.departamentos_activos(cfg))
    malla = motor.construir_malla(cfg, activos)
    t_ref = int(time.time()) // 600 * 600
    escenario = Escenario(malla, t_ref)
    reloj = {"t": t_ref}
    srv = _servidor(escenario, reloj)
    reloj["host"] = f"http://127.0.0.1:{srv.server_address[1]}"
    api = reloj["host"] + "/public/weather-maps.json"

    dir_salida = Path(dir_salida or motor.BASE / "demo_salida")
    if dir_salida.exists():
        shutil.rmtree(dir_salida)
    dir_salida.mkdir(parents=True)
    print("\nDEMO · escenario ficticio (tormentas en Tolima, Meta, Chocó y Nariño; aguaceros menores en "
          "Córdoba y Casanare). Cada paso equivale a 10 minutos.\n")
    try:
        for paso in pasos:
            reloj["t"] = t_ref + paso * 60
            print(f"\n──────── Paso: hora de referencia {'+' if paso >= 0 else ''}{paso} min ────────")
            codigo = motor.ejecutar(cfg, dir_salida, persistir=True, solo_consola=True, api_url=api,
                                    ahora=reloj["t"] + 240, dir_cache=dir_salida / "cache",
                                    clima_fn=_viento_ficticio)
            if codigo != 0:
                return codigo
    finally:
        srv.shutdown()
    print(f"\nBitácora del demo: {dir_salida / 'historial'}")
    return 0
