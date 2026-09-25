"""
analisis.py — Del radar a los indicadores por municipio y departamento.

Para cada departamento calcula: área con lluvia por intensidad, intensidad máxima,
municipios afectados, duración del evento, acumulado de la última hora, desplazamiento
de la tormenta (correlación de fase entre cuadros) y municipios a los que podría llegar
en la próxima hora (extrapolación del movimiento). Con eso asigna el nivel de alerta.
"""
from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from fuente_radar import Cuadro, Malla

log = logging.getLogger("analisis")

NIVELES = ["amarilla", "naranja", "roja"]            # nivel 1, 2, 3
UMBRALES_DBZ = (10, 20, 30, 40, 50, 55)
RUMBOS = ["norte", "nororiente", "oriente", "suroriente", "sur", "suroccidente", "occidente", "noroccidente"]
_MINUSCULAS = {"de", "del", "la", "las", "los", "el", "y", "e", "en"}


def rumbo(grados: float) -> str:
    """Grados (0 = norte, sentido horario) -> nombre del rumbo en 8 direcciones."""
    return RUMBOS[int(((grados % 360) + 22.5) // 45) % 8]


def nombre_propio(texto: str) -> str:
    """'SAN JOSÉ DEL GUAVIARE' -> 'San José del Guaviare'."""
    texto = texto.strip()
    if texto.upper().startswith("BOGOTÁ") or texto.upper().startswith("BOGOTA"):
        return "Bogotá D.C."
    palabras = texto.lower().split()
    salida = []
    for i, p in enumerate(palabras):
        if i > 0 and p in _MINUSCULAS:
            salida.append(p)
        else:
            salida.append("-".join(s[:1].upper() + s[1:] for s in p.split("-")))
    return " ".join(salida)


def _anillos_exteriores(geom: dict):
    if geom["type"] == "Polygon":
        yield np.asarray(geom["coordinates"][0], dtype=float)
    elif geom["type"] == "MultiPolygon":
        for poligono in geom["coordinates"]:
            yield np.asarray(poligono[0], dtype=float)


def _area_anillo(anillo: np.ndarray) -> float:
    x, y = anillo[:, 0], anillo[:, 1]
    return abs(float(np.dot(x, np.roll(y, 1)) - np.dot(y, np.roll(x, 1)))) / 2


# =========================================================================== territorio
class Territorio:
    """Municipios y departamentos rasterizados sobre la malla del radar."""

    def __init__(self, ruta_geojson: Path, malla: Malla, campos: dict):
        self.malla = malla
        datos = json.loads(Path(ruta_geojson).read_text(encoding="utf-8"))
        feats = [f for f in datos["features"] if f.get("geometry")]
        p = lambda f, k: f["properties"][campos[k]]
        self.n_mun = len(feats)
        self.mun_codigo = [""] + [str(p(f, "codigo_municipio")) for f in feats]
        self.mun_nombre = [""] + [nombre_propio(str(p(f, "nombre_municipio"))) for f in feats]
        dep_de_mun = [str(p(f, "codigo_departamento")).zfill(2) for f in feats]
        self.dep_codigos = sorted(set(dep_de_mun))
        self.dep_indice = {c: i + 1 for i, c in enumerate(self.dep_codigos)}
        self.n_dep = len(self.dep_codigos)

        # --- raster de municipios (0 = fuera de Colombia). Primero los grandes, así los
        #     municipios pequeños o enclavados quedan encima.
        img = Image.new("I", (malla.ancho, malla.alto), 0)
        dib = ImageDraw.Draw(img)
        anillos = [list(_anillos_exteriores(f["geometry"])) for f in feats]
        area_aprox = [sum(_area_anillo(a) for a in ans) for ans in anillos]
        for i in sorted(range(self.n_mun), key=lambda k: -area_aprox[k]):
            for anillo in anillos[i]:
                xs, ys = malla.a_pixel(anillo[:, 0], anillo[:, 1])
                puntos = list(zip(xs.tolist(), ys.tolist()))
                if len(puntos) >= 3:
                    dib.polygon(puntos, fill=i + 1)
        etiquetas = np.asarray(img, dtype=np.int32).copy()

        # municipios demasiado pequeños para ocupar un píxel: se marca el píxel del centroide
        presentes = np.bincount(etiquetas.ravel(), minlength=self.n_mun + 1) > 0
        for i in range(self.n_mun):
            if not presentes[i + 1] and anillos[i]:
                a = max(anillos[i], key=_area_anillo)
                px, py = malla.a_pixel(a[:, 0].mean(), a[:, 1].mean())
                if 0 <= int(py) < malla.alto and 0 <= int(px) < malla.ancho:
                    etiquetas[int(py), int(px)] = i + 1
        self.etiquetas = etiquetas

        kmpx = malla.km_por_pixel_filas().astype(np.float32)
        self.km_px_filas = kmpx
        self.area_px = np.broadcast_to((kmpx ** 2)[:, None], etiquetas.shape).copy()

        self.mun_dep = np.zeros(self.n_mun + 1, dtype=np.int16)
        for i, c in enumerate(dep_de_mun):
            self.mun_dep[i + 1] = self.dep_indice[c]
        self.dep_raster = self.mun_dep[etiquetas]

        self.mun_area = np.bincount(etiquetas.ravel(), weights=self.area_px.ravel(), minlength=self.n_mun + 1)
        self.dep_area = np.bincount(self.mun_dep, weights=self.mun_area, minlength=self.n_dep + 1)
        self.mun_por_dep = {d: np.nonzero(self.mun_dep == d)[0] for d in range(1, self.n_dep + 1)}

        # caja y centro (en píxeles) de cada departamento
        self.dep_caja, self.dep_centro = {}, {}
        yy, xx = np.indices(etiquetas.shape)
        sx = np.bincount(self.dep_raster.ravel(), weights=(xx * self.area_px).ravel(), minlength=self.n_dep + 1)
        sy = np.bincount(self.dep_raster.ravel(), weights=(yy * self.area_px).ravel(), minlength=self.n_dep + 1)
        for d in range(1, self.n_dep + 1):
            m = self.dep_raster == d
            filas, cols = np.nonzero(m.any(axis=1))[0], np.nonzero(m.any(axis=0))[0]
            if len(filas) == 0:
                continue
            self.dep_caja[d] = (int(filas[0]), int(filas[-1]) + 1, int(cols[0]), int(cols[-1]) + 1)
            self.dep_centro[d] = (sx[d] / self.dep_area[d], sy[d] / self.dep_area[d])

    def ventana(self, d: int, margen_km: float) -> tuple[int, int, int, int]:
        y0, y1, x0, x1 = self.dep_caja[d]
        m = int(margen_km / float(self.km_px_filas[(y0 + y1) // 2]))
        return max(0, y0 - m), min(self.malla.alto, y1 + m), max(0, x0 - m), min(self.malla.ancho, x1 + m)


# =========================================================================== utilidades
def quitar_pixeles_aislados(d: np.ndarray) -> np.ndarray:
    """Recorta picos de un solo píxel: ningún píxel supera en más de 5 dBZ a su vecino más alto.
    Un eco aislado (vecinos sin lluvia) queda por debajo de 10 dBZ y se descarta."""
    h, w = d.shape
    p = np.pad(d, 1)
    vecino = np.zeros_like(d)
    for dy in range(3):
        for dx in range(3):
            if dy == 1 and dx == 1:
                continue
            np.maximum(vecino, p[dy:dy + h, dx:dx + w], out=vecino)
    return np.minimum(d, vecino.astype(np.int16) + 5).astype(np.uint8)


def _vecindad(m, op):
    h, w = m.shape
    p = np.pad(m, 1, constant_values=bool(op is np.logical_and))
    r = m.copy()
    for dy in range(3):
        for dx in range(3):
            if dy != 1 or dx != 1:
                op(r, p[dy:dy + h, dx:dx + w], out=r)
    return r


def limpiar_mascara(m):
    """Apertura morfológica 3x3: quita bordes suavizados y ecos sueltos que inflan el área."""
    return _vecindad(_vecindad(m, np.logical_and), np.logical_or)


def correlacion_fase(a: np.ndarray, b: np.ndarray, blanqueo: float = 0.5):
    """Desplazamiento (dy, dx) en píxeles que lleva el campo `a` al campo `b`, y la calidad
    del pico (qué tanto sobresale del ruido). blanqueo=1 es correlación de fase pura."""
    h, w = a.shape
    ventana = np.outer(np.hanning(h), np.hanning(w)).astype(np.float32)
    fa = np.fft.rfft2((a - a.mean()) * ventana)
    fb = np.fft.rfft2((b - b.mean()) * ventana)
    x = fb * np.conj(fa)
    x /= np.abs(x) ** blanqueo + 1e-9
    r = np.fft.irfft2(x, s=(h, w))
    py, px = divmod(int(np.argmax(r)), w)
    if py > h // 2:
        py -= h
    if px > w // 2:
        px -= w
    calidad = float((r.max() - r.mean()) / (r.std() + 1e-12))
    return py, px, calidad


def desplazar(m: np.ndarray, sy: int, sx: int) -> np.ndarray:
    """Mueve una máscara sy filas y sx columnas, rellenando con False."""
    h, w = m.shape
    out = np.zeros_like(m)
    if abs(sy) >= h or abs(sx) >= w:
        return out
    out[max(sy, 0):h + min(sy, 0), max(sx, 0):w + min(sx, 0)] = m[max(-sy, 0):h + min(-sy, 0),
                                                                   max(-sx, 0):w + min(-sx, 0)]
    return out


# =========================================================================== evaluación
@dataclass
class Evaluacion:
    codigo: str
    indice: int
    nivel: int = 0
    motivo: str = ""
    activo: bool = False
    terminado: bool = False
    t0: int = 0
    inicio_ts: int | None = None
    ultimo_activo_ts: int | None = None
    duracion_min: int = 0
    viene_de_antes: bool = False
    area_dep_km2: float = 0.0
    area: dict = field(default_factory=dict)          # dBZ -> km² en el cuadro actual
    pct_lluvia: float = 0.0
    area_lluvia_km2: float = 0.0
    dbz_max: int = 0
    tasa_max: float = 0.0
    granizo: bool = False
    acum_1h_max: float = 0.0                          # mm, máximo puntual de la última hora
    acum_1h_municipio: str = ""                        # municipio donde ocurre ese máximo
    acum_evento_max: float = 0.0                      # mm, máximo puntual desde que empezó (hasta 2 h)
    acum_mun_max: float = 0.0                         # mm, mayor promedio municipal de la última hora
    acum_mun_nombre: str = ""
    area_acum: dict = field(default_factory=dict)     # ("1h"|"2h", mm) -> km² que superan ese acumulado
    municipios: list = field(default_factory=list)
    sector: str = ""
    centro_lluvia: tuple | None = None                  # (lon, lat)
    movimiento: dict | None = None
    proxima_hora: list = field(default_factory=list)   # [(municipio, minutos)]
    rayos_15min: int | None = None                      # rayos GOES-GLM en el depto. (None = sin dato)


class Analizador:
    def __init__(self, territorio: Territorio, cfg: dict):
        self.t = territorio
        self.cfg = cfg
        rel = cfg["radar"].get("relacion_zr", "marshall_palmer")
        a, b = (250.0, 1.2) if rel == "tropical" else (200.0, 1.6)
        factor = float(cfg["radar"].get("factor_calibracion", 1.0))
        tasa = np.zeros(256, dtype=np.float32)
        for v in range(10, 256):
            dbz = min(v + 2.5, 55.0)       # centro de la clase de 5 dBZ; tope 55 dBZ (granizo)
            tasa[v] = ((10 ** (dbz / 10)) / a) ** (1 / b) * factor
        self.tasa = tasa

    # ------------------------------------------------------------------ por cuadro
    def _estadisticas(self, d: np.ndarray):
        t = self.t
        m = d >= 10
        idx, a, v = t.etiquetas[m], t.area_px[m], d[m]
        areas = {u: np.bincount(idx[v >= u], weights=a[v >= u], minlength=t.n_mun + 1) for u in UMBRALES_DBZ}
        lluvia = np.bincount(idx, weights=self.tasa[v] * a, minlength=t.n_mun + 1)   # (mm/h)·km²
        return areas, lluvia

    def _nivel(self, ev: Evaluacion):
        """El nivel más alto cuyo algún criterio se cumpla. Los criterios de acumulado exigen
        que esté lloviendo ahora (no se alerta por lluvia que ya pasó)."""
        area_min = float(self.cfg["niveles"].get("area_minima_acumulado_km2", 10))
        nivel, motivo = 0, ""
        for i, nombre in enumerate(NIVELES, start=1):
            c = self.cfg["niveles"].get(nombre, {})
            pruebas = [("area_moderada_km2", ev.area[30], "lluvia moderada en {v:,.0f} km²"),
                       ("area_fuerte_km2", ev.area[40], "lluvia fuerte en {v:,.0f} km²"),
                       ("area_muy_fuerte_km2", ev.area[50], "lluvia muy fuerte en {v:,.0f} km²")]
            for clave, valor, texto in pruebas:
                umbral = c.get(clave)
                if umbral is not None and valor >= umbral:
                    nivel, motivo = i, f"{nombre}: " + texto.format(v=valor) + f" (umbral {umbral})"
                    break
            if nivel == i or not ev.activo:
                continue
            for clave, ventana in (("acumulado_1h_mm", "1h"), ("acumulado_2h_mm", "2h")):
                umbral = c.get(clave)
                if umbral is not None and ev.area_acum.get((ventana, float(umbral)), 0) >= area_min:
                    nivel = i
                    motivo = (f"{nombre}: ≥{umbral} mm en {ventana[0]} h sobre "
                              f"{ev.area_acum[(ventana, float(umbral))]:,.0f} km²")
                    break
        return nivel, motivo

    # ------------------------------------------------------------------ movimiento
    def _movimiento(self, D, tiempos, ventana):
        """Velocidad de la lluvia comparando el cuadro actual con los de hace 10, 20 y 30 min
        (mediana de las estimaciones confiables)."""
        cfg_m = self.cfg.get("movimiento", {})
        cal_min = float(cfg_m.get("calidad_minima", 10.0))
        t0 = tiempos[-1]
        y0, y1, x0, x1 = ventana
        b = np.clip(D[-1][y0:y1, x0:x1].astype(np.float32) - 15, 0, None)
        if (b > 5).sum() < 30:
            return None
        estimaciones = []
        for rezago in (10, 20, 30):
            cands = [i for i, tt in enumerate(tiempos[:-1]) if abs(t0 - tt - rezago * 60) <= 180]
            if not cands:
                continue
            i_ref = cands[-1]
            a = np.clip(D[i_ref][y0:y1, x0:x1].astype(np.float32) - 15, 0, None)
            if (a > 5).sum() < 30:
                continue
            dy, dx, calidad = correlacion_fase(a, b)
            if calidad >= cal_min:
                dt = (t0 - tiempos[i_ref]) / 60
                estimaciones.append((dy / dt, dx / dt, calidad))
        if not estimaciones:
            return {"fiable": False}
        vy = float(np.median([e[0] for e in estimaciones]))
        vx = float(np.median([e[1] for e in estimaciones]))
        km = float(self.t.km_px_filas[(y0 + y1) // 2])
        vel = math.hypot(vx, vy) * km * 60
        dispersion = max(math.hypot(e[0] - vy, e[1] - vx) * km * 60 for e in estimaciones)
        fiable = (len(estimaciones) >= 2 and vel <= cfg_m.get("velocidad_maxima_kmh", 90)
                  and dispersion <= cfg_m.get("dispersion_maxima_kmh", 25))
        grados = math.degrees(math.atan2(vx, -vy)) % 360
        return {"fiable": bool(fiable), "vy": vy, "vx": vx, "vel_kmh": vel, "grados": grados,
                "hacia": rumbo(grados), "n": len(estimaciones), "dispersion_kmh": dispersion}

    def _proxima_hora(self, D0, mov, d, ventana):
        """Municipios del departamento sin lluvia ahora a los que llegaría si sigue su rumbo."""
        if not mov or not mov.get("fiable") or mov["vel_kmh"] < self.cfg.get("movimiento", {}).get("velocidad_minima_kmh", 5):
            return []
        t = self.t
        y0, y1, x0, x1 = ventana
        lluvia = D0[y0:y1, x0:x1] >= 30
        etq, area = t.etiquetas[y0:y1, x0:x1], t.area_px[y0:y1, x0:x1]
        n = t.n_mun + 1
        cubierto = lambda m: np.bincount(etq[m], weights=area[m], minlength=n)
        ahora = cubierto(lluvia)
        frac_min = 0.2
        llegada = {}
        for minutos in (10, 20, 30, 40, 50, 60):
            futuro = cubierto(desplazar(lluvia, int(round(mov["vy"] * minutos)), int(round(mov["vx"] * minutos))))
            alcanzado = (futuro >= np.minimum(25.0, frac_min * t.mun_area)) & (ahora < 5.0)
            for i in np.nonzero(alcanzado)[0]:
                if i > 0 and t.mun_dep[i] == d and i not in llegada:
                    llegada[i] = minutos
        return [(t.mun_nombre[i], m) for i, m in sorted(llegada.items(), key=lambda kv: kv[1])]

    def _sector(self, d, cx, cy, pct):
        if pct >= 0.5:
            return "gran parte del departamento"
        y0, y1, x0, x1 = self.t.dep_caja[d]
        dcx, dcy = self.t.dep_centro[d]
        nx = (cx - dcx) / max((x1 - x0) / 2, 1)
        ny = (dcy - cy) / max((y1 - y0) / 2, 1)
        if math.hypot(nx, ny) < 0.35:
            return "centro del departamento"
        return "sector " + rumbo(math.degrees(math.atan2(nx, ny)))

    def _umbrales_acumulado(self):
        u = {"1h": set(), "2h": set()}
        for nombre in NIVELES:
            c = self.cfg["niveles"].get(nombre, {})
            if c.get("acumulado_1h_mm") is not None:
                u["1h"].add(float(c["acumulado_1h_mm"]))
            if c.get("acumulado_2h_mm") is not None:
                u["2h"].add(float(c["acumulado_2h_mm"]))
        return u

    # ------------------------------------------------------------------ principal
    def analizar(self, cuadros: list[Cuadro], codigos: list[str]) -> dict[str, Evaluacion]:
        t, cfg = self.t, self.cfg
        cuadros = sorted(cuadros, key=lambda c: c.tiempo)
        tiempos = [c.tiempo for c in cuadros]
        D = [quitar_pixeles_aislados(c.dbz) for c in cuadros]
        t0, D0 = tiempos[-1], D[-1]
        est = [self._estadisticas(d) for d in D]
        nd1 = t.n_dep + 1
        serie = {u: np.array([np.bincount(t.mun_dep, weights=e[0][u], minlength=nd1) for e in est])
                 for u in UMBRALES_DBZ}
        areas0, lluvia0 = est[-1]

        # duración de cada cuadro (h): lo que lo separa del anterior, máximo 15 min
        dur_h = [(min(tiempos[i] - tiempos[i - 1], 900) if i > 0 else 600) / 3600 for i in range(len(tiempos))]
        en_hora = [i for i, tt in enumerate(tiempos) if tt > t0 - 3600]
        acum_mun = np.zeros(t.n_mun + 1)
        acum1 = np.zeros(D0.shape, dtype=np.float32)
        acum2 = np.zeros(D0.shape, dtype=np.float32)
        for i in range(len(D)):
            tasa_i = self.tasa[D[i]]
            acum2 += tasa_i * dur_h[i]
            if i in en_hora:
                dh = dur_h[i] if i != en_hora[0] else 600 / 3600   # la hora empieza en este cuadro
                acum1 += tasa_i * dh
                acum_mun += est[i][1] * dh
        acum_mun = np.divide(acum_mun, t.mun_area, out=np.zeros_like(acum_mun), where=t.mun_area > 0)
        area_acum = {}
        for ventana, umbrales in self._umbrales_acumulado().items():
            campo = acum1 if ventana == "1h" else acum2
            for u in umbrales:
                m = campo >= u
                area_acum[(ventana, u)] = np.bincount(t.dep_raster[m], weights=t.area_px[m], minlength=nd1)

        u_ll = int(cfg.get("intensidad", {}).get("lluvia_minima_dbz", 25))
        m_ll = D0 >= u_ll
        if cfg.get("intensidad", {}).get("limpiar_bordes", True):
            m_ll = limpiar_mascara(m_ll)
        area_lluvia = np.bincount(t.dep_raster[m_ll], weights=t.area_px[m_ll], minlength=nd1)

        # histograma de intensidades por departamento (para la intensidad máxima "robusta")
        m10 = D0 >= 10
        hist = np.bincount(t.dep_raster[m10].astype(np.int64) * 17 + D0[m10] // 5,
                           minlength=nd1 * 17).reshape(nd1, 17)
        # centro de la lluvia (≥30 dBZ; si no hay, ≥20 dBZ)
        centros = {}
        for u in (30, 20):
            yy, xx = np.nonzero(D0 >= u)
            dd, aa = t.dep_raster[yy, xx], t.area_px[yy, xx]
            sa = np.bincount(dd, weights=aa, minlength=nd1)
            sx = np.bincount(dd, weights=xx * aa, minlength=nd1)
            sy = np.bincount(dd, weights=yy * aa, minlength=nd1)
            for d in range(1, nd1):
                if d not in centros and sa[d] > 0:
                    centros[d] = (sx[d] / sa[d], sy[d] / sa[d])

        c_act = cfg["niveles"]["activo"]
        n_fin = int(cfg["notificacion"].get("cuadros_para_fin", 3))
        margen = cfg.get("movimiento", {}).get("margen_km", 60)
        min_px = int(cfg.get("intensidad", {}).get("pixeles_minimos_nucleo", 3))

        resultados = {}
        for codigo in codigos:
            d = t.dep_indice.get(codigo)
            if d is None or d not in t.dep_caja:
                continue
            ev = Evaluacion(codigo=codigo, indice=d, t0=t0)
            ev.area_dep_km2 = float(t.dep_area[d])
            ev.area = {u: float(serie[u][-1, d]) for u in UMBRALES_DBZ}
            ev.area_lluvia_km2 = float(area_lluvia[d])
            ev.pct_lluvia = ev.area_lluvia_km2 / ev.area_dep_km2 if ev.area_dep_km2 else 0.0
            ev.area_acum = {k: float(v[d]) for k, v in area_acum.items()}
            a30, a40 = serie[30][:, d], serie[40][:, d]
            activo_s = (a30 >= c_act["area_moderada_km2"]) | (a40 >= c_act["area_fuerte_km2"])
            ev.activo = bool(activo_s[-1])
            ev.terminado = len(activo_s) >= n_fin and not activo_s[-n_fin:].any()
            activos = np.nonzero(activo_s)[0]
            ev.ultimo_activo_ts = tiempos[activos[-1]] if len(activos) else None
            j_inicio = None
            if ev.activo:
                j_inicio = len(activo_s) - 1
                while j_inicio > 0 and activo_s[j_inicio - 1]:
                    j_inicio -= 1
                ev.inicio_ts = tiempos[j_inicio]
                ev.viene_de_antes = j_inicio == 0
                ev.duracion_min = int((t0 - tiempos[j_inicio]) / 60) + 10

            # intensidad máxima robusta: la clase más alta con al menos `min_px` píxeles
            acumulado = 0
            for b in range(16, 1, -1):
                acumulado += hist[d, b]
                if acumulado >= min_px:
                    ev.dbz_max = b * 5
                    break
            ev.tasa_max = float(self.tasa[ev.dbz_max]) if ev.dbz_max >= 10 else 0.0
            ev.granizo = ev.area[55] >= cfg.get("intensidad", {}).get("area_granizo_km2", 3)

            muns = t.mun_por_dep[d]
            if len(muns):
                j = muns[np.argmax(acum_mun[muns])]
                ev.acum_mun_max, ev.acum_mun_nombre = float(acum_mun[j]), t.mun_nombre[j]

            if ev.activo or ev.area[20] > 0:
                y0, y1, x0, x1 = t.dep_caja[d]
                dentro = t.dep_raster[y0:y1, x0:x1] == d
                recorte = np.where(dentro, acum1[y0:y1, x0:x1], 0)
                k = int(np.argmax(recorte))
                ev.acum_1h_max = float(recorte.flat[k])
                if ev.acum_1h_max > 0:
                    ev.acum_1h_municipio = t.mun_nombre[t.etiquetas[y0:y1, x0:x1].flat[k]]
                if j_inicio is not None:   # acumulado desde el inicio del evento
                    ev_acum = sum(self.tasa[D[i][y0:y1, x0:x1]] * (dur_h[i] if i > j_inicio else 600 / 3600)
                                  for i in range(j_inicio, len(D)))
                    ev.acum_evento_max = float(np.where(dentro, ev_acum, 0).max())

            ev.nivel, ev.motivo = self._nivel(ev)

            # municipios con lluvia (moderada o más; si no hay, ligera), de mayor a menor intensidad
            for u, minimo in ((30, 10.0), (20, 20.0)):
                a_u = areas0[u][muns]
                frac = np.divide(a_u, t.mun_area[muns], out=np.zeros_like(a_u), where=t.mun_area[muns] > 0)
                sel = muns[(a_u >= minimo) | ((frac >= 0.10) & (a_u > 0))]
                if len(sel):
                    tasa_media = lluvia0[sel] / np.maximum(t.mun_area[sel], 1e-6)
                    ev.municipios = [t.mun_nombre[i] for i in sel[np.argsort(-tasa_media)]]
                    break

            if d in centros:
                cx, cy = centros[d]
                lon, lat = t.malla.a_lonlat(cx, cy)
                ev.centro_lluvia = (float(lon), float(lat))
                ev.sector = self._sector(d, cx, cy, ev.pct_lluvia)

            if ev.activo or ev.nivel > 0:
                ventana = t.ventana(d, margen)
                ev.movimiento = self._movimiento(D, tiempos, ventana)
                ev.proxima_hora = self._proxima_hora(D0, ev.movimiento, d, ventana)
            resultados[codigo] = ev
        return resultados
