# -*- coding: utf-8 -*-
"""
Pronostico de lluvia de FENALCE para una ventana de 6 horas: mapa en milimetros y texto por departamento.

    python pronostico_lluvia/pronostico_lluvia.py tarde            hoy 12:00-18:00 (se emite a las 11:30 a. m.)
    python pronostico_lluvia/pronostico_lluvia.py manana           manana 06:00-12:00 (se emite a las 7:30 p. m.)
    python pronostico_lluvia/pronostico_lluvia.py tarde --enviar   ademas lo publica en ntfy (el puente lo lleva a WhatsApp)

Mientras PRUEBA=1 (valor por defecto), el titulo empieza con 🧪 y el puente lo manda solo al grupo «Pruebas».

Metodo (detalle y medicion en el repositorio de trabajo «pronostico-lluvia-fenalce»):
  - pronostico vigente de ECMWF IFS 0,25 (Open-Meteo) en una rejilla de 0,25 grados;
  - milimetros: promedio de ECMWF en 0,5 grados alrededor, corregido por mapeo de
    cuantiles contra la lluvia satelital IMERG (agosto-septiembre de 2026);
  - probabilidad de 1 mm o mas: regresion logistica con ECMWF y la climatologia
    IMERG 2015-2025 de ese mes y hora;
  - medido en octubre de 2026 sin usarlo para ajustar (ver la nota del mapa).
Elaboraron: Juan Fernando Gomez Bolanos y Jhon Jairo Valencia Monroy (FENALCE).
"""
import base64
import datetime as dt
import json
import os
import pathlib
import sys
import textwrap
import time
import urllib.request
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import BoundaryNorm, ListedColormap
from matplotlib.patches import Patch, PathPatch
from matplotlib.path import Path as Ruta
from scipy.ndimage import gaussian_filter, zoom

sys.stdout.reconfigure(encoding="utf-8")
AQUI = pathlib.Path(__file__).resolve().parent
DATOS = AQUI / "datos"
SALIDA = AQUI / "salida"
ZONA = ZoneInfo("America/Bogota")
TEMA = os.environ.get("NTFY_TEMA_PRONOSTICO") or "fenalce-lluvia-2ifz77fnqg"
PRUEBA = os.environ.get("PRUEBA", "1") != "0"
PAGINA = "https://agroclima-fenalce-portal.vercel.app/#lluvia"     # la seccion del mapa en la pagina principal del portal
PUBLICADO = AQUI / "publicado"            # lo que lee la pagina interactiva (se sube al repositorio)
VENTANAS = {"tarde": (13, 19, "Tarde", "12:00 p. m. a 6:00 p. m."), "manana": (7, 13, "Mañana", "6:00 a. m. a 12:00 p. m.")}
CIUDADES = {"Bogotá": (4.61, -74.08), "Medellín": (6.25, -75.56), "Cali": (3.45, -76.53), "Barranquilla": (10.96, -74.80),
            "Montería": (8.75, -75.88), "Villavicencio": (4.14, -73.63), "Neiva": (2.93, -75.28), "Ibagué": (4.44, -75.23),
            "Bucaramanga": (7.12, -73.12), "Cúcuta": (7.89, -72.50), "Valledupar": (10.46, -73.25), "Yopal": (5.35, -72.40),
            "Pasto": (1.21, -77.28), "Popayán": (2.44, -76.61), "Sincelejo": (9.30, -75.40), "Arauca": (7.08, -70.76),
            "Puerto Carreño": (6.19, -67.49), "Florencia": (1.61, -75.61), "Tunja": (5.53, -73.36), "Quibdó": (5.69, -76.66)}
# Una sola clase desde 20 mm: en la evaluacion de octubre, de 20 mm o mas solo se encontro 6 de cada 100 y 92 de cada 100 anuncios fallaron.
CORTES = [1, 2, 5, 10, 20]
COLORES = ["#D6ECF5", "#9DCDE6", "#5AA6D4", "#2368AE", "#103050"]   # solo azules (el rojo se lee como tiempo seco); el mas intenso es el azul marino del logo
ETIQUETAS = ["1 - 2 mm", "2 - 5 mm", "5 - 10 mm", "10 - 20 mm", "20 mm o más (fuerte posible)"]
NOMBRE_CLASE = {5: "Lluvias muy abundantes (20 mm o más, posibles eventos fuertes)", 4: "Lluvias abundantes (10 a 20 mm)",
                3: "Lluvias moderadas (5 a 10 mm)", 2: "Lluvias ligeras (2 a 5 mm)", 1: "Lluvias muy ligeras (1 a 2 mm)"}
ICONO = {5: "⛈️", 4: "🌧️", 3: "🔷", 2: "💧"}
NOMBRES_PAGINA = ["Sin lluvias o lloviznas", "Lluvias muy ligeras", "Lluvias ligeras", "Lluvias moderadas", "Lluvias abundantes", "Lluvias muy abundantes"]
# Leyenda (diseno aprobado por el usuario): rango, nombre y gotas de cada nivel
LEYENDA = [("Menos de\n1 mm", "Sin lluvias\no lloviznas", 0), ("1 – 2\nmm", "Lluvias\nmuy ligeras", 2), ("2 – 5\nmm", "Lluvias\nligeras", 2),
           ("5 – 10\nmm", "Lluvias\nmoderadas", 3), ("10 – 20\nmm", "Lluvias\nabundantes", 4), ("20 mm\no más", "Lluvias muy abundantes\n(posibles eventos fuertes)", 6)]
FONDO_FILA = ["#EEF1F4", "#E2F2FA", "#CDE6F5", "#A7D2EE", "#2368AE", "#103050"]
RESPONSABILIDAD = "Pronóstico de FENALCE: complementa y no reemplaza los avisos oficiales del IDEAM y de la gestión del riesgo."
CREDITOS_DATOS = "Datos: ECMWF (CC BY 4.0) vía Open-Meteo · lluvia satelital NASA GPM IMERG"
NAVY, GOLD, PIZARRA_CLARO, PIZARRA_INK, CREMA_PANEL, CREMA_BORDE = "#133353", "#F3C26B", "#A8BFCD", "#546D80", "#F7F5EE", "#E6DECA"
AUTORES = "Juan Fernando Gómez Bolaños y Jhon Jairo Valencia Monroy"
AREA = "FENALCE · Área de Agroclimatología y Modelación"
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]


def leer(nombre):
    return json.loads((DATOS / nombre).read_text(encoding="utf-8"))


def pedir(url):
    for intento in range(6):
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                return json.loads(r.read())
        except Exception as error:          # 429: limite de Open-Meteo
            print(f"  reintento {intento + 1}: {str(error)[:70]}", flush=True)
            time.sleep(30 * (intento + 1))
    raise RuntimeError("Open-Meteo no respondio")


def pronostico_ecmwf(puntos, fecha, ventana):
    h0, h1 = VENTANAS[ventana][:2]
    cache = SALIDA / f"cache_ecmwf_{fecha}_{ventana}_{dt.datetime.now(ZONA):%Y%m%d%H}.json"   # redibujar no vuelve a consultar
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    if "--sin-descargar" in sys.argv:                   # pruebas de diseno: el ultimo pronostico ya bajado, sin gastar consultas
        previos = sorted(SALIDA.glob(f"cache_ecmwf_{fecha}_{ventana}_*.json"))
        if previos:
            return json.loads(previos[-1].read_text(encoding="utf-8"))
    salida = [None] * len(puntos)
    for i in range(0, len(puntos), 50):
        lote = puntos[i:i + 50]
        url = ("https://api.open-meteo.com/v1/forecast?latitude=" + ",".join(str(p["lat"]) for p in lote)
               + "&longitude=" + ",".join(str(p["lon"]) for p in lote)
               + "&hourly=precipitation&models=ecmwf_ifs025&timezone=America/Bogota&forecast_days=3")
        r = pedir(url)
        time.sleep(1.5)
        for k, res in enumerate(r if isinstance(r, list) else [r]):
            sel = [v for t, v in zip(res["hourly"]["time"], res["hourly"]["precipitation"]) if t[:10] == fecha and h0 <= int(t[11:13]) < h1]
            salida[i + k] = None if not sel or any(v is None for v in sel) else round(sum(sel), 2)
    SALIDA.mkdir(exist_ok=True)
    cache.write_text(json.dumps(salida), encoding="utf-8")
    return salida


ISOYETA_CADA_GRADOS = 3.0     # una cifra cada ~330 km de linea
LAT_MIN, LAT_MAX = -4.35, 12.6
ALTO_MAPA_IN = 8.25                  # pulgadas de alto del mapa (ver dibujar)
GRADOS_POR_PUNTO = (LAT_MAX - LAT_MIN) / (ALTO_MAPA_IN * 72)   # escala del mapa (aspecto 1:1)


def caja_texto(x, y, texto, puntos, ha="center", va="center", margen=0.12):
    """Caja aproximada (en grados) que ocupa un texto en el mapa, con un margen."""
    ancho = len(texto) * 0.68 * puntos * GRADOS_POR_PUNTO
    alto = 1.15 * puntos * GRADOS_POR_PUNTO
    x0 = x - ancho / 2 if ha == "center" else x if ha == "left" else x - ancho
    y0 = y - alto / 2 if va == "center" else y if va == "bottom" else y - alto
    return (x0 - margen, y0 - margen, x0 + ancho + margen, y0 + alto + margen)


def choca(caja, ocupado):
    return any(caja[0] < b[2] and caja[2] > b[0] and caja[1] < b[3] and caja[3] > b[1] for b in ocupado)
MAXIMOS = 7                   # cuantos nucleos de lluvia se marcan con su valor


def geometria_isoyetas(x, y, S, pais):
    """Lineas de igual lluvia (en los cortes de la escala) solo dentro de Colombia, puntos para su valor y maximos de lluvia."""
    from scipy.ndimage import maximum_filter
    fig = plt.figure(); cs = plt.contour(x, y, S, levels=CORTES); plt.close(fig)
    lineas, etiquetas = [], []
    for nivel, segmentos in zip(cs.levels, cs.allsegs):
        for seg in segmentos:
            if len(seg) < 4:
                continue
            dentro = pais.contains_points(seg)
            # tramos continuos dentro de Colombia
            tramo = []
            for punto, ok in zip(seg, dentro):
                if ok:
                    tramo.append(punto)
                elif len(tramo) > 1:
                    lineas.append((float(nivel), np.array(tramo))); tramo = []
                else:
                    tramo = []
            if len(tramo) > 1:
                lineas.append((float(nivel), np.array(tramo)))
    for nivel, seg in lineas:
        if len(seg) < 4:
            continue
        d = np.r_[0, np.cumsum(np.hypot(*np.diff(seg, axis=0).T))]
        if d[-1] < 0.8:                                       # lineas muy cortas: sin cifra
            continue
        for objetivo in np.arange(0.4, d[-1], ISOYETA_CADA_GRADOS):
            k = min(max(int(np.searchsorted(d, objetivo)), 1), len(seg) - 2)
            dx, dy = seg[k + 1] - seg[k - 1]
            ang = np.degrees(np.arctan2(dy, dx))
            ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
            etiquetas.append((float(seg[k][0]), float(seg[k][1]), nivel, float(ang)))
    paso = (x[1] - x[0]) if len(x) > 1 else 0.025
    vent = max(3, int(round(1.5 / paso)))
    XX, YY = np.meshgrid(x, y)
    en_pais = pais.contains_points(np.column_stack([XX.ravel(), YY.ravel()])).reshape(S.shape)
    Sd = np.where(en_pais, S, -1.0)                           # el maximo se busca solo dentro de Colombia
    picos = (Sd == maximum_filter(Sd, size=vent)) & (Sd >= 5)
    maximos = sorted(((float(Sd[a, b]), float(x[b]), float(y[a])) for a, b in zip(*np.nonzero(picos))), reverse=True)[:MAXIMOS]
    return lineas, etiquetas, maximos


def isoyetas(ax, x, y, S, recorte, pais, ocupado):
    """Dibuja las isoyetas y los maximos; ningun numero se pone encima de un nombre de ciudad, de un maximo o de otro numero."""
    import matplotlib.patheffects as pe
    halo = [pe.withStroke(linewidth=2.6, foreground="white")]
    lineas, etiquetas, maximos = geometria_isoyetas(x, y, S, pais)
    for nivel, seg in lineas:
        ax.plot(seg[:, 0], seg[:, 1], color=NAVY, lw=0.8, alpha=0.85, ls=(0, (4, 2)), zorder=3.5)
    # 1. los maximos primero (lo mas importante): se busca el lado libre
    for valor, px, py in maximos:
        texto = f"{valor:.0f}"
        for dx, dy, ha, va in ((0.14, -0.04, "left", "top"), (-0.14, -0.04, "right", "top"), (0, 0.16, "center", "bottom"), (0, -0.16, "center", "top")):
            caja = caja_texto(px + dx, py + dy, texto, 8.2, ha, va, 0.08)
            if not choca(caja, ocupado):
                break
        ax.plot(px, py, marker="o", ms=4.2, color="white", mec=NAVY, mew=1.2, zorder=7)
        ax.text(px + dx, py + dy, texto, fontsize=8.2, color=NAVY, weight="bold", ha=ha, va=va, zorder=7, path_effects=halo)
        ocupado.append(caja); ocupado.append((px - 0.12, py - 0.12, px + 0.12, py + 0.12))
    # 2. el valor de las isoyetas, solo donde queda libre
    for px, py, nivel, ang in etiquetas:
        caja = caja_texto(px, py, f"{nivel:g}", 6.6, margen=0.16)
        if choca(caja, ocupado):
            continue
        ax.text(px, py, f"{nivel:g}", fontsize=6.6, color=NAVY, weight="bold", rotation=ang, rotation_mode="anchor",
                ha="center", va="center", zorder=6, path_effects=halo)
        ocupado.append(caja)


def calcular(puntos, e, clima, cal, calmm, ventana):
    vecinos = [[k for k, q in enumerate(puntos) if abs(q["lat"] - p["lat"]) <= 0.51 and abs(q["lon"] - p["lon"]) <= 0.51] for p in puntos]
    c = cal[f"{ventana}|1mm"]["solo_ecmwf"]
    coef, b = np.array(c["coeficientes"]), c["intercepto"]
    tabla = calmm["tabla"]
    mm, p1 = [], []
    for k in range(len(puntos)):
        vec = [e[j] for j in vecinos[k] if e[j] is not None]
        if e[k] is None or not vec or clima[k] is None:
            mm.append(None); p1.append(None); continue
        mm.append(round(float(np.interp(np.mean(vec), tabla["modelo"], tabla["real"])), 2))
        f = clima[k]["frec1"]
        x = {"ecmwf": np.log1p(e[k]), "ecmwf_vec_media": np.log1p(np.mean(vec)), "ecmwf_vec_max": np.log1p(np.max(vec)),
             "clima": np.log(max(f, 0.01) / max(1 - f, 0.01))}
        z = b + float(np.dot(coef, [x[n] for n in c["columnas"]]))
        p1.append(round(1 / (1 + np.exp(-z)), 3))
    return mm, p1


def superficie(puntos, mm):
    lats = sorted({p["lat"] for p in puntos}); lons = sorted({p["lon"] for p in puntos})
    fi = {v: i for i, v in enumerate(lats)}; co = {v: j for j, v in enumerate(lons)}
    Z = np.full((len(lats), len(lons)), np.nan)
    for p, v in zip(puntos, mm):
        if v is not None:
            Z[fi[p["lat"]], co[p["lon"]]] = v
    m = ~np.isnan(Z)
    Zf = gaussian_filter(np.where(m, Z, 0), 1.0); W = gaussian_filter(m.astype(float), 1.0)
    S = np.clip(zoom(np.divide(Zf, W, out=np.zeros_like(Zf), where=W > 0.05), 10, order=3), 0, None)
    x = np.linspace(lons[0], lons[-1], S.shape[1]); y = np.linspace(lats[0], lats[-1], S.shape[0])
    return x, y, S


def anillos_pais():
    anillos = []
    for f in leer("departamentos.json")["features"]:
        g = f["geometry"]
        for pol in ([g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]):
            anillos.append(np.array(pol[0]))
    verts, codigos = [], []
    for a in anillos:
        verts += a.tolist(); codigos += [Ruta.MOVETO] + [Ruta.LINETO] * (len(a) - 1)
    return anillos, Ruta(verts, codigos)


def capa_transparente(puntos, mm, ruta):
    """Solo la lluvia (sin fondo, recortada a Colombia), para ponerla sobre el mapa de la pagina. Devuelve sus limites."""
    x, y, S = superficie(puntos, mm)
    _, pais = anillos_pais()
    ancho, alto = (x[-1] - x[0]), (y[-1] - y[0])
    fig = plt.figure(figsize=(ancho / 2.0, alto / 2.0), dpi=150)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_axis_off(); fig.patch.set_alpha(0)
    niveles = CORTES + [500]
    rel = ax.contourf(x, y, S, levels=niveles, cmap=ListedColormap(COLORES), norm=BoundaryNorm(niveles, len(COLORES)), antialiased=True)
    recorte = PathPatch(pais, transform=ax.transData, facecolor="none", edgecolor="none")
    ax.add_patch(recorte); rel.set_clip_path(recorte)
    ax.set_xlim(x[0], x[-1]); ax.set_ylim(y[0], y[-1])
    fig.savefig(ruta, transparent=True)
    plt.close(fig)
    return [[round(float(y[0]), 4), round(float(x[0]), 4)], [round(float(y[-1]), 4), round(float(x[-1]), 4)]]


def nube(ax, cx, cy, s, gotas, color_gota, borde):
    """Icono: nube blanca con borde y gotas debajo (s = escala en pulgadas)."""
    from matplotlib.patches import Circle, FancyBboxPatch, Polygon
    piezas = [Circle((cx - 0.13 * s, cy + 0.02 * s), 0.11 * s), Circle((cx + 0.01 * s, cy + 0.09 * s), 0.15 * s),
              Circle((cx + 0.15 * s, cy + 0.01 * s), 0.10 * s)]
    base = lambda: FancyBboxPatch((cx - 0.24 * s, cy - 0.09 * s), 0.49 * s, 0.13 * s, boxstyle=f"round,pad=0,rounding_size={0.06 * s}")
    for pz in piezas + [base()]:                      # primero con borde...
        pz.set(facecolor="white", edgecolor=borde, linewidth=1.3, zorder=3); ax.add_patch(pz)
    for c in piezas:                                  # ...y encima sin borde: solo queda el contorno exterior
        ax.add_patch(Circle(c.center, c.radius - 0.012 * s, facecolor="white", edgecolor="none", zorder=4))
    b = base(); b.set(facecolor="white", edgecolor="none", zorder=4); b.set_bounds(cx - 0.235 * s, cy - 0.085 * s, 0.48 * s, 0.12 * s); ax.add_patch(b)
    if not gotas:
        return
    tt = np.linspace(0, 2 * np.pi, 40)
    gota = np.column_stack([0.035 * s * np.sin(tt) * np.sin(tt / 2), 0.055 * s * np.cos(tt)])
    filas = [gotas] if gotas <= 3 else [gotas - gotas // 2, gotas // 2]
    for f, n in enumerate(filas):
        xs = cx + (np.arange(n) - (n - 1) / 2) * 0.11 * s + (0.05 * s if f else 0)
        for gx in xs:
            ax.add_patch(Polygon(gota + [gx, cy - 0.19 * s - f * 0.14 * s], closed=True, facecolor=color_gota, edgecolor="none", zorder=4))


def dibujar(puntos, mm, ventana, fecha, ruta, nota):
    from matplotlib.lines import Line2D
    from matplotlib.patches import FancyBboxPatch
    x, y, S = superficie(puntos, mm)
    ANCHO, ALTO = 9.0, 14.6                          # pulgadas
    F = lambda x0, y0, w, h: [x0 / ANCHO, y0 / ALTO, w / ANCHO, h / ALTO]
    fig = plt.figure(figsize=(ANCHO, ALTO), dpi=200)
    caja_mapa = F(0, 4.9, ANCHO, ALTO_MAPA_IN)

    # --- fondo del mapa (mar) y marca de agua grande detras ---
    fondo_mar = fig.add_axes(caja_mapa, zorder=0); fondo_mar.set_facecolor("#E3EEF3"); fondo_mar.set_xticks([]); fondo_mar.set_yticks([])
    for s_ in fondo_mar.spines.values():
        s_.set_visible(False)
    marca = DATOS / "logo-vertical-azul.png"
    if marca.exists():
        img = plt.imread(str(marca))
        h_in = ALTO_MAPA_IN * 0.88; w_in = h_in * img.shape[1] / img.shape[0]
        mx = fig.add_axes(F((ANCHO - w_in) / 2, 4.9 + (ALTO_MAPA_IN - h_in) / 2, w_in, h_in), zorder=1)
        mx.imshow(img, alpha=0.10, interpolation="antialiased"); mx.axis("off")

    # --- mapa ---
    ax = fig.add_axes(caja_mapa, zorder=2)
    ax.patch.set_alpha(0)
    anillos, pais = anillos_pais()
    ax.add_patch(PathPatch(pais, facecolor=CREMA_PANEL, edgecolor="none", alpha=0.82, zorder=1))
    niveles = CORTES + [500]
    rel = ax.contourf(x, y, S, levels=niveles, cmap=ListedColormap(COLORES), norm=BoundaryNorm(niveles, len(COLORES)), zorder=2, antialiased=True)
    recorte = PathPatch(pais, transform=ax.transData, facecolor="none", edgecolor="none")
    ax.add_patch(recorte); rel.set_clip_path(recorte)
    for a in anillos:
        ax.plot(a[:, 0], a[:, 1], color=PIZARRA_INK, lw=0.35, alpha=0.45, zorder=3)
    XX, YY = np.meshgrid(x, y)
    en_pais = pais.contains_points(np.column_stack([XX.ravel(), YY.ravel()])).reshape(XX.shape).astype(float)
    ax.contour(x, y, gaussian_filter(en_pais, 0.6), levels=[0.5], colors=NAVY, linewidths=0.9, zorder=3.3)
    maximos = geometria_isoyetas(x, y, S, pais)[2]
    puntos_max = [(px - 0.15, py - 0.15, px + 0.15, py + 0.15) for _, px, py in maximos]
    lado_ciudad, ocupado = {}, []
    for nombre, (la, lo) in CIUDADES.items():
        derecha = caja_texto(lo + 0.13, la + 0.07, nombre, 6.4, "left", "bottom", 0.08)
        izquierda = caja_texto(lo - 0.13, la + 0.07, nombre, 6.4, "right", "bottom", 0.08)
        lado_ciudad[nombre] = "right" if choca(derecha, puntos_max) and not choca(izquierda, puntos_max) else "left"
        ocupado.append(derecha if lado_ciudad[nombre] == "left" else izquierda)
        ocupado.append((lo - 0.1, la - 0.1, lo + 0.1, la + 0.1))
    isoyetas(ax, x, y, S, recorte, pais, ocupado)
    for nombre, (la, lo) in CIUDADES.items():
        ax.plot(lo, la, "o", ms=2.4, color=NAVY, zorder=5)
        ha = lado_ciudad.get(nombre, "left")
        ax.text(lo + (0.13 if ha == "left" else -0.13), la + 0.07, nombre, fontsize=6.4, color=NAVY, ha=ha, zorder=5,
                bbox=dict(boxstyle="round,pad=0.1", fc="white", ec="none", alpha=0.7))
    ax.set_ylim(LAT_MIN, LAT_MAX); ax.set_xlim(-73.05 - 6.5, -73.05 + 6.5)
    ax.set_aspect("equal", adjustable="datalim"); ax.axis("off")

    # --- encabezado y pie ---
    def banda(rect, color):
        b = fig.add_axes(rect); b.set_facecolor(color); b.set_xticks([]); b.set_yticks([])
        for s_ in b.spines.values():
            s_.set_visible(False)
    banda(F(0, 13.22, ANCHO, ALTO - 13.22), NAVY); banda(F(0, 13.15, ANCHO, 0.07), GOLD); banda(F(0, 0, ANCHO, 0.38), NAVY)
    dia = dt.date.fromisoformat(fecha)
    fig.text(0.36 / ANCHO, 14.05 / ALTO, f"Pronóstico de lluvia · {VENTANAS[ventana][2]}", fontsize=20, weight="bold", color="white")
    fig.text(0.36 / ANCHO, 13.7 / ALTO, f"Válido de {VENTANAS[ventana][3]} · {DIAS[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]} de {dia.year}", fontsize=11.5, color="white")
    fig.text(0.36 / ANCHO, 13.38 / ALTO, "Lluvia acumulada en la ventana de 6 horas (milímetros)", fontsize=9.5, color=PIZARRA_CLARO)
    logo = DATOS / "logo-horizontal-blanca.png"
    if logo.exists():
        lax = fig.add_axes(F(6.3, 13.4, 2.45, 1.0)); lax.imshow(plt.imread(str(logo))); lax.axis("off")

    # --- leyenda (franja propia, debajo del mapa) ---
    lg = fig.add_axes(F(0.3, 1.2, 5.4, 3.6)); lg.set_xlim(0, 5.4); lg.set_ylim(0, 3.6); lg.set_aspect("equal"); lg.axis("off")
    lg.add_patch(FancyBboxPatch((0, 3.12), 5.4, 0.46, boxstyle="round,pad=0,rounding_size=0.1", facecolor=NAVY, edgecolor="none"))
    lg.text(2.7, 3.35, "Lluvia en 6 horas (mm)", ha="center", va="center", fontsize=14, weight="bold", color="white")
    muestras = [CREMA_PANEL] + COLORES
    for k, (rango, nombre_k, gotas) in enumerate(LEYENDA):
        y0 = 3.12 - 0.06 - (k + 1) * 0.505 + 0.02
        oscuro = k >= 4
        texto = "white" if oscuro else NAVY
        lg.add_patch(FancyBboxPatch((0, y0), 5.4, 0.46, boxstyle="round,pad=0,rounding_size=0.08", facecolor=FONDO_FILA[k], edgecolor="none"))
        lg.add_patch(FancyBboxPatch((0.12, y0 + 0.08), 0.6, 0.3, boxstyle="round,pad=0,rounding_size=0.05", facecolor=muestras[k],
                                    edgecolor="#9AA9B4" if k == 0 else ("white" if oscuro else "none"), linewidth=1.0))
        nube(lg, 1.12, y0 + 0.28, 0.62, gotas, "white" if oscuro else "#2F8FD8", NAVY)
        lg.text(1.6, y0 + 0.23, rango, va="center", fontsize=9.6, weight="bold", color=texto, linespacing=1.0)
        lg.plot([2.95, 2.95], [y0 + 0.08, y0 + 0.38], color=texto, lw=0.8, alpha=0.5)
        lg.text(3.08, y0 + 0.23, nombre_k, va="center", fontsize=8.6 if k < 5 else 8.0, color=texto, linespacing=1.05)

    # --- isoyetas, maximo y QR (columna derecha) ---
    der = fig.add_axes(F(5.85, 1.2, 2.85, 3.6)); der.set_xlim(0, 2.85); der.set_ylim(0, 3.6); der.set_aspect("equal"); der.axis("off")
    for yy, titulo, texto in ((3.06, "Isoyetas", "Líneas de igual lluvia (mm)"), (2.48, "Máximo esperado", "Zona con más lluvia (mm)")):
        der.add_patch(FancyBboxPatch((0, yy), 2.85, 0.52, boxstyle="round,pad=0,rounding_size=0.08", facecolor="#F1F4F7", edgecolor="none"))
        if titulo == "Isoyetas":
            der.add_line(Line2D([0.12, 0.62], [yy + 0.26, yy + 0.26], color=NAVY, lw=2.2, ls=(0, (3, 1.6))))
        else:
            der.plot(0.37, yy + 0.26, marker="o", ms=11, color="white", mec=NAVY, mew=2.2)
        der.text(0.78, yy + 0.33, titulo, va="center", fontsize=9.6, weight="bold", color=NAVY)
        der.text(0.78, yy + 0.15, texto, va="center", fontsize=7.6, color=PIZARRA_INK)
    qr = DATOS / "qr_pagina.png"
    if qr.exists():
        der.text(1.425, 2.28, "Consulte su municipio", ha="center", va="center", fontsize=9.4, weight="bold", color=NAVY)
        qax = fig.add_axes(F(5.85 + 0.52, 1.2 + 0.36, 1.8, 1.8)); qax.imshow(plt.imread(str(qr))); qax.axis("off")
        der.text(1.425, 0.2, "Escanee el código", ha="center", va="center", fontsize=7.6, color=PIZARRA_INK)

    # --- nota, creditos ---
    fig.text(0.36 / ANCHO, 1.1 / ALTO, chr(10).join(textwrap.wrap(nota, 165)), fontsize=7.0, color=PIZARRA_INK, va="top")
    fig.text(0.36 / ANCHO, 0.5 / ALTO, f"Elaboró: {AUTORES} · {AREA}", fontsize=7.6, color=NAVY, weight="bold")
    fig.text(0.36 / ANCHO, 0.14 / ALTO, f"{CREDITOS_DATOS} · agroclima-fenalce-portal.vercel.app", fontsize=7.2, color="white")
    SALIDA.mkdir(exist_ok=True)
    fig.savefig(ruta, facecolor="white")
    plt.close(fig)


def resumen(puntos, mm, p1, ventana, fecha):
    presencia = set(leer("presencia.json"))
    xy = np.array([[p["lat"], p["lon"]] for p in puntos])
    filas = []
    for nombre, depto, la, lo in leer("municipios_centros.json"):
        if depto not in presencia:
            continue
        k = int(np.argmin((xy[:, 0] - la) ** 2 + (xy[:, 1] - lo) ** 2))
        if mm[k] is None:
            continue
        filas.append((int(np.digitize(mm[k], CORTES)), mm[k], p1[k], nombre, depto))
    nivel = {}
    for c, _, _, _, d in filas:
        nivel[d] = max(nivel.get(d, 0), c)
    dia = dt.date.fromisoformat(fecha)
    lineas = [f"🌧️ *Pronóstico de lluvia FENALCE · {VENTANAS[ventana][2]}*",
              f"{DIAS[dia.weekday()].capitalize()} {dia.day} de {MESES[dia.month - 1]} · {VENTANAS[ventana][3]}", ""]
    for c in (5, 4, 3, 2):
        deptos = sorted(d for d, n in nivel.items() if n == c)
        if not deptos:
            continue
        lineas.append(f"{ICONO[c]} *{NOMBRE_CLASE[c]}*")
        for d in deptos:
            ms = sorted((x for x in filas if x[4] == d and x[0] == c), key=lambda x: -x[1])
            prob = max(x[2] or 0 for x in ms)
            nombres = ", ".join(x[3] for x in ms[:5]) + (f" y {len(ms) - 5} más" if len(ms) > 5 else "")
            lineas.append(f"• {d}: {nombres} (probabilidad de lluvia hasta {round(prob * 100)} %)")
        lineas.append("")
    llovizna = sorted(d for d, n in nivel.items() if n == 1)
    if llovizna:
        lineas += ["⚪ *Lluvias muy ligeras (1 a 2 mm):* " + ", ".join(llovizna), ""]
    if not any(n >= 1 for n in nivel.values()):
        lineas += ["No se espera lluvia importante en las zonas de FENALCE.", ""]
    else:
        lineas += ["En el resto de las zonas de FENALCE no se espera lluvia importante."]
    lineas += [f"📍 Consulte su municipio: {PAGINA}", "",
               "Milímetros aproximados por zonas de unos 28 km: no es el pronóstico de una finca. Entre tanto, siguen las alertas en tiempo real.",
               f"_{RESPONSABILIDAD}_",
               f"_Elaboró: {AUTORES}_", f"_{AREA}_"]
    return chr(10).join(lineas)


def enviar(png, texto, ventana, fecha):
    titulo = ("🧪 " if PRUEBA else "") + f"🌧️ Pronóstico de lluvia FENALCE · {VENTANAS[ventana][2]} ({fecha})"
    cod = lambda s: "=?UTF-8?B?" + base64.b64encode(s.encode("utf-8")).decode() + "?="
    cabeceras = {"Filename": png.name, "Title": cod(titulo), "Message": cod(texto), "Tags": "cloud_with_rain"}
    if os.environ.get("NTFY_TOKEN"):
        cabeceras["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
    pedido = urllib.request.Request(f"https://ntfy.sh/{TEMA}", data=png.read_bytes(), method="PUT", headers=cabeceras)
    with urllib.request.urlopen(pedido, timeout=60) as r:
        print("Enviado a ntfy:", r.status, titulo)


def publicar(puntos, mm, p1, ventana, fecha, v, calmm_todo):
    """Lo que lee la pagina interactiva: zonas, municipios (con su zona mas cercana) y la capa del mapa."""
    PUBLICADO.mkdir(exist_ok=True)
    limites = capa_transparente(puntos, mm, PUBLICADO / f"capa_{ventana}.png")
    x, y, S = superficie(puntos, mm)
    lineas, etiquetas, maximos = geometria_isoyetas(x, y, S, anillos_pais()[1])
    isoyetas_web = [{"nivel": n, "linea": [[round(float(b), 3), round(float(a), 3)] for a, b in seg[::2]]} for n, seg in lineas]   # [lat, lon]
    xy = np.array([[p["lat"], p["lon"]] for p in puntos])
    municipios = []
    for nombre, depto, la, lo in leer("municipios_centros.json"):
        k = int(np.argmin((xy[:, 0] - la) ** 2 + (xy[:, 1] - lo) ** 2))
        municipios.append([nombre, depto, la, lo, mm[k], p1[k]])
    dia = dt.date.fromisoformat(fecha)
    datos = {"fecha": fecha, "ventana": ventana, "nombre": VENTANAS[ventana][2], "rango": VENTANAS[ventana][3],
             "fecha_texto": f"{DIAS[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]} de {dia.year}",
             "emitido": dt.datetime.now(ZONA).isoformat(timespec="minutes"), "prueba": PRUEBA,
             "cortes": CORTES, "colores": COLORES, "etiquetas": ETIQUETAS, "nombres_clase": NOMBRES_PAGINA, "limites_capa": limites,
             "responsabilidad": RESPONSABILIDAD, "creditos": CREDITOS_DATOS,
             "acierto": {"clase": v["acierto_de_clase"], "clase_o_vecina": v["acierto_clase_o_vecina"],
                         "climatologia": calmm_todo["climatologia"]["acierto_de_clase"]},
             "zonas": [[p["lat"], p["lon"], a, b] for p, a, b in zip(puntos, mm, p1) if a is not None],
             "municipios": municipios, "isoyetas": isoyetas_web,
             "etiquetas_isoyetas": [[round(py, 3), round(px, 3), n, round(a)] for px, py, n, a in etiquetas],
             "maximos": [[round(py, 3), round(px, 3), round(v)] for v, px, py in maximos]}
    (PUBLICADO / f"ultimo_{ventana}.json").write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (PUBLICADO / "ultimo.json").write_text(json.dumps({"ventana": ventana, "fecha": fecha, "emitido": datos["emitido"]}), encoding="utf-8")


def main():
    ventana = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in VENTANAS else "tarde"
    hoy = dt.datetime.now(ZONA).date()
    fechas = [a for a in sys.argv[2:] if a[:2] == "20"]
    fecha = fechas[0] if fechas else (hoy if ventana == "tarde" else hoy + dt.timedelta(days=1)).isoformat()
    publicado = PUBLICADO / f"ultimo_{ventana}.json"
    if "--si-falta" in sys.argv and publicado.exists() and json.loads(publicado.read_text(encoding="utf-8")).get("fecha") == fecha:
        print(f"La emisión de {ventana} para {fecha} ya salió: el respaldo no hace nada.")
        return
    puntos = leer("rejilla_025.json")
    clima = leer("clima_025.json")[f"{int(fecha[5:7])}|{ventana}"]
    cal = leer("calibracion.json")
    calmm_todo = leer("calibracion_mm.json")[ventana]
    e = pronostico_ecmwf(puntos, fecha, ventana)
    mm, p1 = calcular(puntos, e, clima, cal, calmm_todo["vecindad"], ventana)
    v = calmm_todo["vecindad"]
    nota = (f"Medido en octubre de 2026, sin usarlo para ajustar: acertó la clase de milímetros en {round(v['acierto_de_clase'] * 100)} % de las zonas "
            f"y la clase o la vecina en {round(v['acierto_clase_o_vecina'] * 100)} % (lo que suele llover a esa hora: "
            f"{round(calmm_todo['climatologia']['acierto_de_clase'] * 100)} %). Milímetros aproximados por zonas de unos 28 km; no es el pronóstico de una finca. " + RESPONSABILIDAD)
    base = f"lluvia_mm_{ventana}_{fecha}"
    png = SALIDA / f"{base}.png"
    dibujar(puntos, mm, ventana, fecha, png, nota)
    texto = resumen(puntos, mm, p1, ventana, fecha)
    (SALIDA / f"{base}.txt").write_text(texto, encoding="utf-8")
    (SALIDA / f"{base}.json").write_text(json.dumps({"fecha": fecha, "ventana": ventana, "emitido": dt.datetime.now(ZONA).isoformat(timespec="minutes"),
                                                     "puntos": [{**p, "mm": a, "p1": b, "ecmwf_mm": c} for p, a, b, c in zip(puntos, mm, p1, e)]}),
                                         encoding="utf-8")
    publicar(puntos, mm, p1, ventana, fecha, v, calmm_todo)
    print(texto)
    if "--enviar" in sys.argv:
        enviar(png, texto, ventana, fecha)


if __name__ == "__main__":
    main()
