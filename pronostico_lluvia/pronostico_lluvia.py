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
PAGINA = "https://agroclima-fenalce-portal.vercel.app/pronostico-lluvia/"
PUBLICADO = AQUI / "publicado"            # lo que lee la pagina interactiva (se sube al repositorio)
VENTANAS = {"tarde": (13, 19, "Tarde", "12:00 p. m. a 6:00 p. m."), "manana": (7, 13, "Mañana", "6:00 a. m. a 12:00 p. m.")}
CIUDADES = {"Bogotá": (4.61, -74.08), "Medellín": (6.25, -75.56), "Cali": (3.45, -76.53), "Barranquilla": (10.96, -74.80),
            "Montería": (8.75, -75.88), "Villavicencio": (4.14, -73.63), "Neiva": (2.93, -75.28), "Ibagué": (4.44, -75.23),
            "Bucaramanga": (7.12, -73.12), "Cúcuta": (7.89, -72.50), "Valledupar": (10.46, -73.25), "Yopal": (5.35, -72.40),
            "Pasto": (1.21, -77.28), "Popayán": (2.44, -76.61), "Sincelejo": (9.30, -75.40), "Arauca": (7.08, -70.76),
            "Puerto Carreño": (6.19, -67.49), "Florencia": (1.61, -75.61), "Tunja": (5.53, -73.36), "Quibdó": (5.69, -76.66)}
CORTES = [1, 2, 5, 10, 20, 40]
COLORES = ["#D6ECF5", "#9DCDE6", "#5AA6D4", "#2F78B7", "#1D4E8C", "#103050"]   # solo azules (el rojo se lee como tiempo seco); el mas intenso es el azul marino del logo
ETIQUETAS = ["1 - 2 mm", "2 - 5 mm", "5 - 10 mm", "10 - 20 mm", "20 - 40 mm", "40 mm o más"]
NOMBRE_CLASE = {6: "Lluvias extremas (40 mm o más)", 5: "Lluvias muy fuertes (20 a 40 mm)", 4: "Lluvias fuertes (10 a 20 mm)",
                3: "Lluvias moderadas (5 a 10 mm)", 2: "Lluvias ligeras (2 a 5 mm)", 1: "Lloviznas (1 a 2 mm)"}
ICONO = {6: "⛈️", 5: "🌧️", 4: "🔵", 3: "🔷", 2: "💧"}
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
    rel = ax.contourf(x, y, S, levels=niveles, cmap=ListedColormap(COLORES), norm=BoundaryNorm(niveles, 6), antialiased=True)
    recorte = PathPatch(pais, transform=ax.transData, facecolor="none", edgecolor="none")
    ax.add_patch(recorte); rel.set_clip_path(recorte)
    ax.set_xlim(x[0], x[-1]); ax.set_ylim(y[0], y[-1])
    fig.savefig(ruta, transparent=True)
    plt.close(fig)
    return [[round(float(y[0]), 4), round(float(x[0]), 4)], [round(float(y[-1]), 4), round(float(x[-1]), 4)]]


def dibujar(puntos, mm, ventana, fecha, ruta, nota):
    x, y, S = superficie(puntos, mm)

    fig = plt.figure(figsize=(9, 11), dpi=200)
    ax = fig.add_axes([0.0, 0.075, 1.0, 0.78])
    ax.set_facecolor("#E3EEF3")
    anillos, pais = anillos_pais()
    ax.add_patch(PathPatch(pais, facecolor=CREMA_PANEL, edgecolor="none", zorder=1))
    niveles = CORTES + [500]
    rel = ax.contourf(x, y, S, levels=niveles, cmap=ListedColormap(COLORES), norm=BoundaryNorm(niveles, 6), zorder=2, antialiased=True)
    recorte = PathPatch(pais, transform=ax.transData, facecolor="none", edgecolor="none")
    ax.add_patch(recorte); rel.set_clip_path(recorte)
    for a in anillos:
        ax.plot(a[:, 0], a[:, 1], color=PIZARRA_INK, lw=0.45, alpha=0.8, zorder=3)
    ax.add_patch(PathPatch(pais, facecolor="none", edgecolor=NAVY, lw=1.0, zorder=3))
    for nombre, (la, lo) in CIUDADES.items():
        ax.plot(lo, la, "o", ms=2.6, color=NAVY, zorder=5)
        ax.text(lo + 0.13, la + 0.07, nombre, fontsize=6.8, color=NAVY, zorder=5, bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.75))
    ax.set_xlim(-79.4, -66.7); ax.set_ylim(-4.4, 12.7); ax.set_aspect("equal"); ax.axis("off")

    def banda(rect, color):
        b = fig.add_axes(rect); b.set_facecolor(color); b.set_xticks([]); b.set_yticks([])
        for s in b.spines.values():
            s.set_visible(False)
    banda([0, 0.875, 1, 0.125], NAVY); banda([0, 0.869, 1, 0.006], GOLD); banda([0, 0, 1, 0.03], NAVY)
    dia = dt.date.fromisoformat(fecha)
    fig.text(0.04, 0.955, f"Pronóstico de lluvia · {VENTANAS[ventana][2]}", fontsize=20, weight="bold", color="white")
    fig.text(0.04, 0.922, f"Válido de {VENTANAS[ventana][3]} · {DIAS[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]} de {dia.year}", fontsize=11.5, color="white")
    fig.text(0.04, 0.893, "Lluvia acumulada en la ventana de 6 horas (milímetros)", fontsize=9.5, color=PIZARRA_CLARO)
    logo = DATOS / "logo-horizontal-blanca.png"
    if logo.exists():
        lax = fig.add_axes([0.70, 0.895, 0.27, 0.085]); lax.imshow(plt.imread(str(logo))); lax.axis("off")
    ley = ax.legend(handles=[Patch(facecolor=CREMA_PANEL, edgecolor=PIZARRA_CLARO, label="Menos de 1 mm")]
                    + [Patch(facecolor=c, edgecolor="none", label=e) for c, e in zip(COLORES, ETIQUETAS)],
                    loc="lower left", bbox_to_anchor=(0.02, 0.03), fontsize=8.5, title="Lluvia en 6 horas", title_fontsize=9.5,
                    frameon=True, framealpha=0.96, edgecolor=CREMA_BORDE)
    ley.get_title().set_weight("bold"); ley.get_title().set_color(NAVY)
    fig.text(0.04, 0.072, chr(10).join(textwrap.wrap(nota, 140)), fontsize=7.2, color=PIZARRA_INK, va="top")
    fig.text(0.04, 0.038, f"Elaboró: {AUTORES} · {AREA}", fontsize=7.6, color=NAVY, weight="bold")
    fig.text(0.04, 0.010, "Pronóstico automático: ECMWF corregido con lluvia satelital (IMERG) · agroclima-fenalce-portal.vercel.app",
             fontsize=7.2, color="white")
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
    for c in (6, 5, 4, 3, 2):
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
        lineas += ["⚪ *Lloviznas (1 a 2 mm):* " + ", ".join(llovizna), ""]
    if not any(n >= 1 for n in nivel.values()):
        lineas += ["No se espera lluvia importante en las zonas de FENALCE.", ""]
    else:
        lineas += ["En el resto de las zonas de FENALCE no se espera lluvia importante."]
    lineas += [f"📍 Consulte su municipio: {PAGINA}", "",
               "Milímetros aproximados por zonas de unos 28 km: no es el pronóstico de una finca. Entre tanto, siguen las alertas en tiempo real.",
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
    xy = np.array([[p["lat"], p["lon"]] for p in puntos])
    municipios = []
    for nombre, depto, la, lo in leer("municipios_centros.json"):
        k = int(np.argmin((xy[:, 0] - la) ** 2 + (xy[:, 1] - lo) ** 2))
        municipios.append([nombre, depto, la, lo, mm[k], p1[k]])
    dia = dt.date.fromisoformat(fecha)
    datos = {"fecha": fecha, "ventana": ventana, "nombre": VENTANAS[ventana][2], "rango": VENTANAS[ventana][3],
             "fecha_texto": f"{DIAS[dia.weekday()]} {dia.day} de {MESES[dia.month - 1]} de {dia.year}",
             "emitido": dt.datetime.now(ZONA).isoformat(timespec="minutes"), "prueba": PRUEBA,
             "cortes": CORTES, "colores": COLORES, "etiquetas": ETIQUETAS, "limites_capa": limites,
             "acierto": {"clase": v["acierto_de_clase"], "clase_o_vecina": v["acierto_clase_o_vecina"],
                         "climatologia": calmm_todo["climatologia"]["acierto_de_clase"]},
             "zonas": [[p["lat"], p["lon"], a, b] for p, a, b in zip(puntos, mm, p1) if a is not None],
             "municipios": municipios}
    (PUBLICADO / f"ultimo_{ventana}.json").write_text(json.dumps(datos, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    (PUBLICADO / "ultimo.json").write_text(json.dumps({"ventana": ventana, "fecha": fecha, "emitido": datos["emitido"]}), encoding="utf-8")


def main():
    ventana = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in VENTANAS else "tarde"
    hoy = dt.datetime.now(ZONA).date()
    fechas = [a for a in sys.argv[2:] if a[:2] == "20"]
    fecha = fechas[0] if fechas else (hoy if ventana == "tarde" else hoy + dt.timedelta(days=1)).isoformat()
    puntos = leer("rejilla_025.json")
    clima = leer("clima_025.json")[f"{int(fecha[5:7])}|{ventana}"]
    cal = leer("calibracion.json")
    calmm_todo = leer("calibracion_mm.json")[ventana]
    e = pronostico_ecmwf(puntos, fecha, ventana)
    mm, p1 = calcular(puntos, e, clima, cal, calmm_todo["vecindad"], ventana)
    v = calmm_todo["vecindad"]
    nota = (f"Medido en octubre de 2026, sin usarlo para ajustar: acertó la clase de milímetros en {round(v['acierto_de_clase'] * 100)} % de las zonas "
            f"y la clase o la vecina en {round(v['acierto_clase_o_vecina'] * 100)} % (lo que suele llover a esa hora: "
            f"{round(calmm_todo['climatologia']['acierto_de_clase'] * 100)} %). Milímetros aproximados por zonas de unos 28 km; no es el pronóstico de una finca.")
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
