"""
clip_radar.py — Clip animado (GIF) de la lluvia y los rayos de la última hora sobre un departamento,
para acompañar las alertas en WhatsApp (como los clips que comparte el IDEAM).

  • El PRIMER cuadro es la imagen MÁS RECIENTE del radar (es la que se ve en la vista previa del
    celular y de WhatsApp), con los rayos de los últimos 15 min.
  • Después viene la animación de la última hora (cada 5 min), con los rayos de cada momento.
  • La hora de cada imagen es la que publica Rain-Alarm (se busca la última disponible, ~5 min de
    retraso), no la del reloj del computador.
  • Capas: mapa base (Esri), lluvia del radar Rain-Alarm, municipios, departamento resaltado,
    municipios donde llueve, la capital, rayos GOES-19 (GLM) y hacia dónde se mueven las nubes.

   python clip_radar.py 73 "Ibagué,Chaparral" Tolima ALTA   → salida/clips/clip_73.gif
"""
from __future__ import annotations

import io
import json
import logging
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("clip")
BASE = Path(__file__).resolve().parent
CACHE = BASE / ".cache_clip"
CAB = {"User-Agent": "Mozilla/5.0 (FENALCE agroclimatologia)", "Referer": "https://agroclima-fenalce-portal.vercel.app/"}
MAPA = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
LLUVIA = "https://images.rain-alarm.com/rain/g2/z{z}/{bx}_{by}/{x}_{y}_{hhmm}.png"
ZONA = timezone(timedelta(hours=-5))
ANCHO, ALTO_CAB, ALTO_MAPA, ALTO_PIE = 640, 66, 500, 58
ALTO = ALTO_CAB + ALTO_MAPA + ALTO_PIE
N_CUADROS = 12                       # última hora, cada 5 min
AZUL = (21, 52, 84)                  # azul FENALCE
NIVELES = {"ALTA": ((246, 165, 113), "PROBABILIDAD ALTA"), "MEDIA": ((244, 196, 108), "PROBABILIDAD MEDIA")}
PALETA = [(4, 233, 231), (3, 0, 244), (2, 253, 2), (0, 142, 0), (253, 248, 2), (255, 148, 0), (230, 20, 20)]
MARCA = [(191, 114, 117), (244, 196, 108), (246, 165, 113), (110, 140, 161), (106, 160, 130),
         (233, 226, 208), (103, 178, 183), (176, 199, 144)]      # colores del logo FENALCE
TEAL, CREMA = (103, 178, 183), (247, 244, 236)
IDEAM_URL = "https://bart.ideam.gov.co/ospa/radarcol/transparent/{r}/z/{d}/"
_LISTAS = {}
CAPITAL = {"25": "11001", "CO": "11001"}            # Cundinamarca: se marca Bogotá
DIAS = "lunes martes miércoles jueves viernes sábado domingo".split()
MESES = "enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre".split()
_GEO = None
_RAYOS = {"t": 0.0, "datos": None}


# --------------------------------------------------------------------------- utilidades
def _fuente(t, negrita=True):
    nombres = (["DejaVuSans-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if negrita
               else ["DejaVuSans.ttf", "arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for f in nombres:
        try:
            return ImageFont.truetype(f, t)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=t)
    except TypeError:
        return ImageFont.load_default()


def _h12(dt):
    dt = dt.astimezone(ZONA)
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'a. m.' if dt.hour < 12 else 'p. m.'}"


def _px(lon, lat, z):
    n = 256 * 2 ** z
    s = math.sin(math.radians(max(-85.0, min(85.0, lat))))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def _px_np(lon, lat, z):
    n = 256 * 2 ** z
    s = np.sin(np.radians(np.clip(lat, -85, 85)))
    return (lon + 180) / 360 * n, (0.5 - np.log((1 + s) / (1 - s)) / (4 * np.pi)) * n


def _bajar(url, cache: Path | None = None):
    if cache is not None and cache.exists():
        return cache.read_bytes(), None
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=CAB), timeout=30)
        b = r.read()
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(b)
        return b, r.headers.get("Date")
    except Exception:
        return None, None


def _imagen(b):
    try:
        return Image.open(io.BytesIO(b)).convert("RGBA") if b else None
    except Exception:
        return None


def _teselas(url, z, tx, ty, cache_dir: Path | None = None, **kw):
    def una(p):
        x, y = p
        c = cache_dir / f"{z}_{x}_{y}.png" if cache_dir else None
        return p, _imagen(_bajar(url.format(z=z, x=x, y=y, bx=x // 8, by=y // 8, **kw), c)[0])
    with ThreadPoolExecutor(6) as ex:
        return dict(ex.map(una, [(x, y) for x in tx for y in ty]))


def _mosaico(teselas, tx, ty, corte):
    img = Image.new("RGBA", (len(tx) * 256, len(ty) * 256), (0, 0, 0, 0))
    for (x, y), t in teselas.items():
        if t is not None:
            img.paste(t, ((x - tx[0]) * 256, (y - ty[0]) * 256))
    return img.crop(corte)


def _municipios(codigo):
    global _GEO
    if _GEO is None:
        _GEO = json.loads((BASE / "datos" / "municipios_mgn2018.geojson").read_text(encoding="utf-8"))["features"]
    cod = str(codigo).zfill(2)
    cod_cap = CAPITAL.get(cod, cod + "001")
    muns, capital = [], None
    for f in _GEO:
        p, g = f["properties"], f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        m = (str(p.get("MPIO_CCNCT")), p.get("MPIO_CNMBR", ""), [[(c[0], c[1]) for c in poly[0]] for poly in polys])
        if cod == "CO" or str(p.get("DPTO_CCDGO")).zfill(2) == cod:
            muns.append(m)
        if m[0] == cod_cap:
            capital = m
    return muns, capital


def _titulo(s):
    """'SAN JOSÉ DEL GUAVIARE' -> 'San José del Guaviare'."""
    out = []
    for i, w in enumerate(str(s).lower().split()):
        out.append(w.upper() if "." in w else w if i and w in ("de", "del", "la", "las", "los", "el", "y", "e")
                   else w[:1].upper() + w[1:])
    return " ".join(out)


def _centro(anillos):
    """Centroide del polígono más grande del municipio."""
    mejor, area_max = None, 0.0
    for r in anillos:
        a = cx = cy = 0.0
        for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]):
            c = x0 * y1 - x1 * y0
            a += c; cx += (x0 + x1) * c; cy += (y0 + y1) * c
        if a and abs(a) > area_max:
            area_max, mejor = abs(a), (cx / (3 * a), cy / (3 * a))
    return mejor


def _ultimo_cuadro(z, x, y):
    """Hora (UTC) de la imagen de radar más reciente que ya publicó Rain-Alarm, y la hora del servidor."""
    _, fecha = _bajar(f"https://images.rain-alarm.com/rain/g2/z{z}/{x // 8}_{y // 8}/{x}_{y}.png")
    ahora = parsedate_to_datetime(fecha) if fecha else datetime.now(timezone.utc)
    t = ahora.replace(second=0, microsecond=0) - timedelta(minutes=ahora.minute % 5)
    for i in range(8):
        tt = t - timedelta(minutes=5 * i)
        if _bajar(LLUVIA.format(z=z, x=x, y=y, bx=x // 8, by=y // 8, hhmm=f"{tt:%H%M}"))[0]:
            return tt, ahora
    return None, ahora


def _rayos(minutos=65):
    """Rayos GOES-19 GLM [lon, lat, t, energía] de la última hora (se reutilizan 4 min entre clips)."""
    if _RAYOS["datos"] is not None and time.time() - _RAYOS["t"] < 240:
        return _RAYOS["datos"]
    datos = np.zeros((0, 4))
    try:
        import rayos_glm
        datos = rayos_glm.descargar_rayos(minutos)
    except Exception as e:
        log.warning("Rayos para el clip: %s", e)
        try:
            g = json.loads((BASE / "salida" / "rayos.geojson").read_text(encoding="utf-8"))
            datos = np.array([[*f["geometry"]["coordinates"][:2], f["properties"]["t"], 0] for f in g["features"]])
        except Exception:
            pass
    _RAYOS.update(t=time.time(), datos=datos)
    return datos


def _rayo(d, x, y, s=1.0):
    p = [(x + 1 * s, y - 6 * s), (x - 3 * s, y + 1 * s), (x, y + 1 * s), (x - 1 * s, y + 6 * s),
         (x + 4 * s, y - 1 * s), (x + 1 * s, y - 1 * s)]
    d.polygon(p, fill=(255, 205, 0, 255), outline=(95, 55, 0, 255))


def _capa_lluvia(arr, alfa_gris=50):
    """Radar: la nubosidad (gris) queda tenue y la lluvia se repinta con los colores de la leyenda según su tono
    (al acercar el mapa Rain-Alarm suaviza los bordes y salen tonos pálidos que no corresponden a la leyenda)."""
    rgb = arr[..., :3].astype(np.int16)
    a = arr[..., 3]
    mx, mn = rgb.max(-1), rgb.min(-1)
    gris = (mx - mn < 30) & (a > 0)
    lluvia = (a > 40) & ~gris
    r, g, b = (rgb[..., i].astype(np.float32) for i in range(3))
    d = np.maximum(mx - mn, 1).astype(np.float32)
    h = np.where(mx == r, ((g - b) / d) % 6, np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60
    clase = np.select([(h >= 160) & (h < 205), (h >= 205) & (h < 300), (h >= 75) & (h < 160) & (mx > 190),
                       (h >= 75) & (h < 160), (h >= 45) & (h < 75), (h >= 15) & (h < 45)], [0, 1, 2, 3, 4, 5], 6)
    out = np.zeros_like(arr)
    out[lluvia, :3] = np.array(PALETA, np.uint8)[clase[lluvia]]
    out[lluvia, 3] = 255
    out[gris, :3] = arr[gris, :3]
    out[gris, 3] = alfa_gris
    return Image.fromarray(out, "RGBA"), lluvia


def _lista_ideam(radar, dias):
    """[(hora UTC, url)] de las imágenes transparentes publicadas por el IDEAM (se guarda 3 min)."""
    import re
    k = (radar, tuple(dias))
    if k in _LISTAS and time.time() - _LISTAS[k][0] < 180:
        return _LISTAS[k][1]
    out = []
    for d in dias:
        url = IDEAM_URL.format(r=radar, d=d)
        b, _ = _bajar(url)
        for nom in sorted(set(re.findall(r"([A-Z]{3}\d{10}_z_Transp\.png)", (b or b"").decode("latin-1")))):
            t = datetime.strptime(nom[3:13], "%y%m%d%H%M").replace(tzinfo=timezone.utc) + timedelta(hours=5)
            out.append((t, url + nom))
    out.sort()
    _LISTAS[k] = (time.time(), out)
    return out


def _ideam(tiempos, lon, lat):
    """Reflectividad (dBZ, clases de 5) de los radares IDEAM en la ventana, para cada tiempo."""
    try:
        import fuente_ideam as fi
        cols, dbz = fi._paleta()
    except Exception as e:
        log.warning("IDEAM para el clip: %s", e)
        return {}
    capas = {}
    dias = sorted({(t - timedelta(hours=5)).strftime("%Y/%m/%d") for t in tiempos})
    for nombre, (la0, lo0, km) in fi.RADARES.items():
        hy = km / 111.32
        hx = hy / np.cos(np.radians(la0))
        c = ((lon - (lo0 - hx)) / (2 * hx) * 800).astype(int)
        r = (((la0 + hy) - lat) / (2 * hy) * 800).astype(int)
        dentro = ((c >= 0) & (c < 800) & (r >= 0) & (r < 800) &
                  (np.hypot((lat - la0) * 111.32, (lon - lo0) * 111.32 * np.cos(np.radians(la0))) <= km * 0.97))
        if not dentro.any():
            continue
        archivos = _lista_ideam(nombre, dias)
        r, c = np.clip(r, 0, 799), np.clip(c, 0, 799)
        for t in tiempos:
            cand = [a for a in archivos if t - timedelta(minutes=10) <= a[0] <= t + timedelta(minutes=2)]
            if not cand:
                continue
            u = cand[-1][1]
            im = _imagen(_bajar(u, CACHE / "ideam" / u.rsplit("/", 1)[-1])[0])
            if im is None:
                continue
            v = np.where(dentro, fi._a_dbz(np.array(im), cols, dbz)[r, c], 0).astype(np.uint8)
            capas[t] = v if t not in capas else np.maximum(capas[t], v)
    return capas


# temperatura de la cima de la nube (°C) -> color (más fría = nube de tormenta más alta)
RAMPA_T = [-75, -62, -50, -38, -25, -12, 0]
RAMPA_C = [(125, 45, 150, 225), (70, 60, 140, 205), (65, 90, 140, 180), (85, 110, 145, 150), (110, 128, 150, 115),
           (130, 142, 160, 75), (140, 150, 165, 0)]


def _capa_ir(tc):
    out = np.zeros(tc.shape + (4,), np.uint8)
    v = np.nan_to_num(tc, nan=99.0)
    for i in range(4):
        out[..., i] = np.interp(v, RAMPA_T, [c[i] for c in RAMPA_C]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def _goes_ir(tiempos, z, tx, ty, corte):
    """Imagen infrarroja GOES-19 (banda 13) más reciente anterior a cada tiempo, sobre la ventana del clip."""
    try:
        import h5py
        import indice_nubes as inu
        from fuente_radar import Malla
        ks = inu.claves("ABI-L2-CMIPF", "M6C13", 2)
    except Exception as e:
        log.warning("Satélite para el clip: %s", e)
        return {}
    malla = Malla(z, 256, tx[0], tx[-1], ty[0], ty[-1])
    eleg = {}
    for t in tiempos:
        c = [k for k in ks if inu._inicio(k) <= t]
        if c:
            eleg[t] = c[-1]
    dir_c = CACHE / "goes"
    dir_c.mkdir(parents=True, exist_ok=True)
    for f in dir_c.glob("*.nc"):
        if time.time() - f.stat().st_mtime > 3 * 3600:
            f.unlink(missing_ok=True)

    def leer(k):
        f = dir_c / k.rsplit("/", 1)[-1]
        try:
            if not f.exists():
                f.write_bytes(urllib.request.urlopen(f"{inu.BUCKET}/{k}", timeout=180).read())
            with h5py.File(f, "r") as h:
                iy, ix = inu._indice(malla, h)
                y0, y1, x0, x1 = int(iy.min()), int(iy.max()) + 1, int(ix.min()), int(ix.max()) + 1
                v = inu._escalar(h["CMI"], (slice(y0, y1), slice(x0, x1)))[iy - y0, ix - x0] - 273.15
            return k, _capa_ir(v[corte[1]:corte[3], corte[0]:corte[2]])
        except Exception as e:
            log.warning("Satélite %s: %s", k, e)
            f.unlink(missing_ok=True)
            return k, None
    with ThreadPoolExecutor(3) as ex:
        imgs = dict(ex.map(leer, sorted(set(eleg.values()))))
    return {t: imgs[k] for t, k in eleg.items() if imgs.get(k) is not None}


SAT_MMH = [1, 2.5, 5, 10, 20, 40]     # mm/h -> clases 0..6 de la leyenda
ALFA_SAT = 165                         # más clara que la del radar para distinguirla


def _capa_sat(rr):
    out = np.zeros(rr.shape + (4,), np.uint8)
    v = np.nan_to_num(rr, nan=0.0)
    m = v >= SAT_MMH[0]
    clase = np.digitize(v, SAT_MMH[1:])
    out[m, :3] = np.array(PALETA, np.uint8)[clase[m]]
    out[m, 3] = ALFA_SAT
    return Image.fromarray(out, "RGBA"), m


def _goes_lluvia(tiempos, z, tx, ty, corte):
    """Lluvia estimada por el satélite GOES-19 (NOAA, ABI-L2-RRQPEF, cada 10 min) sobre la ventana del clip."""
    try:
        import h5py
        import indice_nubes as inu
        from fuente_radar import Malla
        ks = inu.claves("ABI-L2-RRQPEF", "", 2)
    except Exception as e:
        log.warning("Lluvia satélite para el clip: %s", e)
        return {}
    malla = Malla(z, 256, tx[0], tx[-1], ty[0], ty[-1])
    eleg = {}
    for t in tiempos:
        c = [k for k in ks if inu._inicio(k) <= t]
        if c:
            eleg[t] = c[-1]
    dir_c = CACHE / "goes"
    dir_c.mkdir(parents=True, exist_ok=True)
    for f in dir_c.glob("*.nc"):
        if time.time() - f.stat().st_mtime > 3 * 3600:
            f.unlink(missing_ok=True)

    def leer(k):
        f = dir_c / k.rsplit("/", 1)[-1]
        try:
            if not f.exists():
                f.write_bytes(urllib.request.urlopen(f"{inu.BUCKET}/{k}", timeout=180).read())
            with h5py.File(f, "r") as h:
                iy, ix = inu._indice(malla, h)
                y0, y1, x0, x1 = int(iy.min()), int(iy.max()) + 1, int(ix.min()), int(ix.max()) + 1
                rr = inu._escalar(h["RRQPE"], (slice(y0, y1), slice(x0, x1)))
                rr[h["DQF"][y0:y1, x0:x1] != 0] = np.nan
                rr = rr[iy - y0, ix - x0]
            return k, _capa_sat(rr[corte[1]:corte[3], corte[0]:corte[2]])
        except Exception as e:
            log.warning("Lluvia satélite %s: %s", k, e)
            f.unlink(missing_ok=True)
            return k, None
    with ThreadPoolExecutor(3) as ex:
        res = dict(ex.map(leer, sorted(set(eleg.values()))))
    return {t: res[k] for t, k in eleg.items() if res.get(k) is not None}


COLOR_REGION = {"andina": (106, 160, 130), "caribe": (232, 180, 80), "pacifica": (103, 178, 183),
                "orinoquia": (240, 150, 95), "amazonia": (150, 185, 110)}
NOMBRE_REGION = {"andina": "ANDINA", "caribe": "CARIBE", "pacifica": "PACÍFICA", "orinoquia": "ORINOQUÍA",
                 "amazonia": "AMAZONÍA"}


def icono_cultivo(tipo, d=28):
    """Circulito con el dibujo del cultivo (maíz, soya o fríjol), dibujado a 4x y reducido para que quede suave."""
    k = 4
    D = d * k
    im = Image.new("RGBA", (D, D), (0, 0, 0, 0))
    g = ImageDraw.Draw(im)
    g.ellipse((2 * k, 2 * k, D - 2 * k, D - 2 * k), fill=(255, 255, 255, 255), outline=(*AZUL, 255), width=2 * k)
    c = D / 2
    if tipo == "maiz":
        g.polygon([(c - 9 * k, c + 9 * k), (c - 3 * k, c - 2 * k), (c - 1 * k, c + 9 * k)], fill=(90, 150, 70, 255))
        g.polygon([(c + 9 * k, c + 9 * k), (c + 3 * k, c - 2 * k), (c + 1 * k, c + 9 * k)], fill=(90, 150, 70, 255))
        g.ellipse((c - 4 * k, c - 10 * k, c + 4 * k, c + 8 * k), fill=(240, 190, 40, 255), outline=(190, 135, 20, 255), width=k)
        for yy in range(-7, 7, 3):
            for xx in (-2, 1):
                g.ellipse((c + xx * k, c + yy * k, c + (xx + 1.6) * k, c + (yy + 1.6) * k), fill=(210, 150, 20, 255))
    elif tipo == "soya":
        g.rounded_rectangle((c - 10 * k, c - 4 * k, c + 10 * k, c + 4 * k), radius=4 * k, fill=(110, 160, 60, 255),
                            outline=(70, 115, 40, 255), width=k)
        for xx in (-6, 0, 6):
            g.ellipse((c + (xx - 2.6) * k, c - 2.6 * k, c + (xx + 2.6) * k, c + 2.6 * k), fill=(200, 215, 120, 255))
    else:   # fríjol
        for dx, dy in ((-4, -3), (4, 3)):
            g.ellipse((c + (dx - 5) * k, c + (dy - 3.5) * k, c + (dx + 5) * k, c + (dy + 3.5) * k),
                      fill=(150, 45, 45, 255), outline=(95, 25, 25, 255), width=k)
            g.ellipse((c + (dx - 2) * k, c + (dy - 2) * k, c + dx * k, c + (dy - 0.6) * k), fill=(215, 120, 110, 255))
    return im.resize((d, d), Image.LANCZOS)


def _ids_a_bordes(ids):
    """Píxeles donde cambia el identificador (límite entre regiones o departamentos)."""
    b = np.zeros(ids.shape, bool)
    b[1:, :] |= ids[1:, :] != ids[:-1, :]
    b[:, 1:] |= ids[:, 1:] != ids[:, :-1]
    return b


# --------------------------------------------------------------------------- clip
def generar(codigo, departamento="", resaltar=(), nivel=None, movimiento=None, salida=None, forzar=False,
            viento=None):
    """Genera el GIF. Devuelve {archivo, hora, hace_min, lluvia_km2, rayos} o None si no hay qué mostrar."""
    muns, capital = _municipios(codigo)
    if not muns:
        return None
    pts = [c for _, _, an in muns for r in an for c in r]
    lo0, lo1 = min(p[0] for p in pts), max(p[0] for p in pts)
    la0, la1 = min(p[1] for p in pts), max(p[1] for p in pts)
    for z in range(9, 4, -1):
        x0, y0 = _px(lo0, la1, z)
        x1, y1 = _px(lo1, la0, z)
        if (x1 - x0) * 1.06 <= ANCHO and (y1 - y0) * 1.06 <= ALTO_MAPA:
            break
    ox, oy = int((x0 + x1) / 2 - ANCHO / 2), int((y0 + y1) / 2 - ALTO_MAPA / 2)
    tx = range(ox // 256, (ox + ANCHO - 1) // 256 + 1)
    ty = range(oy // 256, (oy + ALTO_MAPA - 1) // 256 + 1)
    corte = (ox - tx[0] * 256, oy - ty[0] * 256, ox - tx[0] * 256 + ANCHO, oy - ty[0] * 256 + ALTO_MAPA)

    def a_px(lon, lat):
        x, y = _px(lon, lat, z)
        return x - ox, y - oy

    # ---- capas fijas: mapa base, municipios, departamento resaltado
    fondo = Image.new("RGBA", (ANCHO, ALTO_MAPA), (236, 238, 240, 255))
    fondo = Image.alpha_composite(fondo, _mosaico(_teselas(MAPA, z, tx, ty, CACHE / "mapa"), tx, ty, corte))
    lineas = Image.new("RGBA", (ANCHO, ALTO_MAPA), (0, 0, 0, 0))
    dl = ImageDraw.Draw(lineas)
    mascara = Image.new("L", (ANCHO, ALTO_MAPA), 0)
    dm = ImageDraw.Draw(mascara)
    centros = {}
    for _, nom, anillos in muns:
        for r in anillos:
            p = [a_px(*c) for c in r[:: max(1, len(r) // 400)]]
            if len(p) > 2:
                dm.polygon(p, fill=255)
                (codigo != 'CO') and dl.line(p + [p[0]], fill=(70, 80, 100, 110), width=1)
        c = _centro(anillos)
        if c:
            centros[nom.casefold()] = (nom, a_px(*c))
    m_dep = np.array(mascara) > 0
    fuera = np.zeros((ALTO_MAPA, ANCHO, 4), np.uint8)
    fuera[..., :3] = 255
    fuera[..., 3] = np.where(m_dep, 0, 125)
    bordes = mascara.filter(ImageFilter.FIND_EDGES)
    halo = np.array(bordes.filter(ImageFilter.MaxFilter(7))) > 0
    linea = np.array(bordes.filter(ImageFilter.MaxFilter(3))) > 0
    borde = np.zeros((ALTO_MAPA, ANCHO, 4), np.uint8)
    borde[halo] = (255, 255, 255, 170)
    borde[linea] = (*AZUL, 255)
    if codigo == "CO":
        try:
            from reporte_regional import region_de
            claves_r = list(COLOR_REGION)
            img_reg = Image.new("L", (ANCHO, ALTO_MAPA), 0)
            img_dep = Image.new("I", (ANCHO, ALTO_MAPA), 0)
            dr, dd = ImageDraw.Draw(img_reg), ImageDraw.Draw(img_dep)
            for cod_m, _, anillos in muns:
                rid = claves_r.index(region_de(cod_m)) + 1
                for r_ in anillos:
                    pp = [a_px(*c_) for c_ in r_[:: max(1, len(r_) // 200)]]
                    if len(pp) > 2:
                        dr.polygon(pp, fill=rid)
                        dd.polygon(pp, fill=int(str(cod_m).zfill(5)[:2]))
            reg = np.array(img_reg)
            tinte = np.zeros((ALTO_MAPA, ANCHO, 4), np.uint8)
            for i, k_ in enumerate(claves_r):
                tinte[reg == i + 1] = (*COLOR_REGION[k_], 60)
            fondo = Image.alpha_composite(fondo, Image.fromarray(tinte, "RGBA"))
            capa = np.zeros((ALTO_MAPA, ANCHO, 4), np.uint8)
            capa[_ids_a_bordes(np.array(img_dep)) & (np.array(img_dep) > 0)] = (110, 120, 135, 120)
            bor_r = Image.fromarray((_ids_a_bordes(reg) & (reg > 0)).astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))
            capa[np.array(bor_r) > 0] = (*AZUL, 200)
            lineas = Image.alpha_composite(lineas, Image.fromarray(capa, "RGBA"))
            regiones_lbl = []
            for i, k_ in enumerate(claves_r):
                # punto más "adentro" de la región: se encoge la máscara hasta que queda el núcleo
                m_ = Image.fromarray(((reg == i + 1) * 255).astype(np.uint8))
                ult = np.array(m_) > 0
                for _ in range(40):
                    m_ = m_.filter(ImageFilter.MinFilter(5))
                    a_ = np.array(m_) > 0
                    if not a_.any():
                        break
                    ult = a_
                ys_, xs_ = np.nonzero(ult)
                if len(xs_):
                    regiones_lbl.append((NOMBRE_REGION[k_], float(xs_.mean()), float(ys_.mean()), COLOR_REGION[k_]))
        except Exception as e:
            log.warning("Regiones en el mapa: %s", e)
            regiones_lbl = []
    capa_fuera = Image.fromarray(fuera, "RGBA")      # aclara el mapa y las nubes fuera del departamento (no la lluvia)
    encima = Image.alpha_composite(lineas, Image.fromarray(borde, "RGBA"))

    # ---- radar: última imagen publicada y la hora anterior, cada 5 min
    cx_t, cy_t = (ox + ANCHO // 2) // 256, (oy + ALTO_MAPA // 2) // 256
    t_ult, ahora = _ultimo_cuadro(z, cx_t, cy_t)
    if t_ult is None:
        log.warning("Clip %s: Rain-Alarm sin imágenes recientes", codigo)
        return None
    tiempos = [t_ult - timedelta(minutes=5 * i) for i in range(N_CUADROS - 1, -1, -1)]
    with ThreadPoolExecutor(2) as ex:
        crudos = list(ex.map(lambda t: _teselas(LLUVIA, z, tx, ty, hhmm=f"{t:%H%M}"), tiempos))
    cuadros, previo = [], {}
    for t, tes in zip(tiempos, crudos):
        tes = {k: (v if v is not None else previo.get(k)) for k, v in tes.items()}   # tesela faltante: la anterior
        if not any(v is not None for v in tes.values()):
            continue
        previo = tes
        arr = np.array(_mosaico(tes, tx, ty, corte))
        if cuadros and np.array_equal(arr, cuadros[-1][1]):
            if t == tiempos[-1]:
                cuadros[-1] = (t, arr)     # misma imagen: se deja con la hora más reciente
            continue
        cuadros.append((t, arr))
    if not cuadros:
        return None
    # radar IDEAM (Munchique, Barrancabermeja): donde tiene dato, manda sobre Rain-Alarm
    n_px = 256 * 2 ** z
    lon_v = (ox + np.arange(ANCHO) + 0.5) / n_px * 360 - 180
    lat_v = np.degrees(np.arctan(np.sinh(np.pi * (1 - 2 * (oy + np.arange(ALTO_MAPA) + 0.5) / n_px))))
    LON, LAT = np.meshgrid(lon_v, lat_v)
    ideam = _ideam([t for t, _ in cuadros], LON, LAT)
    usa_ideam = False
    for i, (t, arr) in enumerate(cuadros):
        v = ideam.get(t)
        if v is not None and (v >= 20).any():
            arr = arr.copy()
            m = v >= 20
            arr[m, :3] = np.array(PALETA, np.uint8)[np.clip((v[m].astype(int) - 20) // 5, 0, 6)]
            arr[m, 3] = 255
            cuadros[i] = (t, arr)
            usa_ideam = True
    sat = _goes_lluvia([t for t, _ in cuadros], z, tx, ty, corte)
    usa_sat = bool(sat)
    ir = {t: v[0] for t, v in sat.items()}
    capas = [(t, *_capa_lluvia(arr)) for t, arr in cuadros]
    lat_c = (la0 + la1) / 2
    km2_px = (40075.0 * math.cos(math.radians(lat_c)) / (256 * 2 ** z)) ** 2
    lluvia_km2 = [float((ll & m_dep).sum() * km2_px) for _, _, ll in capas]

    # ---- rayos
    ry = _rayos()
    if len(ry):
        rx, ryy = _px_np(ry[:, 0], ry[:, 1], z)
        rx, ryy = rx - ox, ryy - oy
        ok = (rx >= 0) & (rx < ANCHO) & (ryy >= 0) & (ryy < ALTO_MAPA)
        rx, ryy, rt = rx[ok], ryy[ok], ry[ok, 2]
    else:
        rx = ryy = rt = np.zeros(0)
    en_dep = m_dep[ryy.astype(int), rx.astype(int)] if len(rx) else np.zeros(0, bool)
    rayos_dep = int((en_dep & (rt >= ahora.timestamp() - 3600)).sum())
    if not forzar and max(lluvia_km2) < 20 and rayos_dep < 15:
        log.info("Clip %s: sin lluvia en el radar ni rayos que mostrar", codigo)
        return None

    def puntos(t0, t1):
        sel = (rt > t0) & (rt <= t1)
        return sorted({(int(x) // 8 * 8 + 4, int(y) // 8 * 8 + 4) for x, y in zip(rx[sel], ryy[sel])})

    # ---- fuentes y elementos fijos sobre el mapa
    color, titulo = NIVELES.get(nivel, (AZUL, "LLUVIA"))
    f_hora, f_chip, f_chip_r = _fuente(28), _fuente(12), _fuente(13, False)
    f_mun, f_ley, f_ley_r, f_pie = _fuente(15), _fuente(12), _fuente(11, False), _fuente(11, False)
    medir = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    w_chip = int(max(medir.textlength("ÚLTIMA IMAGEN DEL RADAR", font=f_chip),
                     medir.textlength("ANIMACIÓN · ÚLTIMA HORA", font=f_chip),
                     medir.textlength("12:55 p. m.", font=f_hora),
                     medir.textlength("hace 55 min  ·  rayos: 15 min", font=f_chip_r))) + 24
    caja_hora = (10, 10, 10 + w_chip, 10 + 78)
    ocupado = [caja_hora]
    etiquetas = Image.new("RGBA", (ANCHO, ALTO_MAPA), (0, 0, 0, 0))
    de = ImageDraw.Draw(etiquetas)

    # leyenda
    x_sw = 10 + int(medir.textlength("Satélite", font=f_ley)) + 10
    lh = 62 if usa_sat else 46
    lx, ly = 10, ALTO_MAPA - 10 - lh
    lw = x_sw + 7 * 17 + 22 + int(medir.textlength("rayo", font=f_ley)) + 12
    de.rounded_rectangle((lx, ly, lx + lw, ly + lh), radius=9, fill=(255, 255, 255, 255), outline=(205, 212, 220, 255))
    de.text((lx + 10, ly + 8), "Radar" if usa_sat else "Lluvia", font=f_ley, fill=(30, 35, 45, 255))
    for i, c in enumerate(PALETA):
        de.rectangle((lx + x_sw + i * 17, ly + 9, lx + x_sw + i * 17 + 16, ly + 21), fill=(*c, 255))
    de.text((lx + x_sw, ly + (44 if usa_sat else 25)), "débil", font=f_ley_r, fill=(70, 80, 90, 255))
    de.text((lx + x_sw + 7 * 17 - medir.textlength("fuerte", font=f_ley_r), ly + (44 if usa_sat else 25)), "fuerte", font=f_ley_r,
            fill=(70, 80, 90, 255))
    xr = lx + x_sw + 7 * 17 + 14
    _rayo(de, xr, ly + 16, 1.3)
    de.text((xr + 10, ly + 9), "rayo", font=f_ley, fill=(30, 35, 45, 255))
    if usa_sat:
        de.text((lx + 10, ly + 26), "Satélite", font=f_ley, fill=(30, 35, 45, 255))
        a = ALFA_SAT / 255
        for i, c in enumerate(PALETA):
            de.rectangle((lx + x_sw + i * 17, ly + 27, lx + x_sw + i * 17 + 16, ly + 39),
                         fill=tuple(int(c[j] * a + 255 * (1 - a)) for j in range(3)) + (255,))
        de.text((lx + x_sw + 7 * 17 + 6, ly + 26), "estimada", font=f_ley_r, fill=(70, 80, 90, 255))
    ocupado.append((lx, ly, lx + lw, ly + lh))

    # dirección: seguimiento de las nubes; si no hay, viento en altura (lleva las tormentas) o en superficie
    RUMBOS = ["norte", "nororiente", "oriente", "suroriente", "sur", "suroccidente", "occidente", "noroccidente"]
    rumbo = lambda g: RUMBOS[int(((g % 360) + 22.5) // 45) % 8]
    flecha = None
    v = viento or {}
    if movimiento and movimiento.get("vel_kmh", 0) >= 5 and movimiento.get("grados") is not None:
        flecha = (movimiento["grados"], "Las nubes se mueven")
    elif v.get("alto_dir") is not None and (v.get("alto_kmh") or 0) >= 3:
        flecha = ((v["alto_dir"] + 180) % 360, "Las nubes se mueven")
    elif v.get("viento_dir") is not None and (v.get("viento_kmh") or 0) >= 3:
        flecha = ((v["viento_dir"] + 180) % 360, "El viento sopla")

    def dibujar_flecha(d, cx, cy, grados, largo, ancho, color):
        ux, uy = math.sin(math.radians(grados)), -math.cos(math.radians(grados))
        px_, py_ = -uy, ux
        cola = (cx - ux * largo / 2, cy - uy * largo / 2)
        base = (cx + ux * (largo / 2 - ancho * 1.6), cy + uy * (largo / 2 - ancho * 1.6))
        punta = (cx + ux * largo / 2, cy + uy * largo / 2)
        d.line([cola, base], fill=color, width=int(ancho))
        d.polygon([punta, (base[0] + px_ * ancho * 1.3, base[1] + py_ * ancho * 1.3),
                   (base[0] - px_ * ancho * 1.3, base[1] - py_ * ancho * 1.3)], fill=color)

    if flecha:
        grados, frase = flecha
        # flecha grande y semitransparente sobre el departamento
        ys, xs = np.nonzero(m_dep)
        if len(xs):
            capa_f = Image.new("RGBA", (ANCHO, ALTO_MAPA), (0, 0, 0, 0))
            dibujar_flecha(ImageDraw.Draw(capa_f), float(xs.mean()), float(ys.mean()), grados, 110, 14, (*AZUL, 150))
            etiquetas.alpha_composite(capa_f)
        mw = int(max(medir.textlength(frase, font=f_ley_r), medir.textlength(f"hacia el {rumbo(grados)}", font=f_ley))) + 62
        mh = 46
        mx, my = ANCHO - 10 - mw, ALTO_MAPA - 10 - mh
        de.rounded_rectangle((mx, my, mx + mw, my + mh), radius=9, fill=(255, 255, 255, 255), outline=(205, 212, 220, 255))
        ccx, ccy, rr = mx + 25, my + 23, 17
        de.ellipse((ccx - rr, ccy - rr, ccx + rr, ccy + rr), fill=(*AZUL, 255))
        dibujar_flecha(de, ccx, ccy, grados, 24, 4, (255, 255, 255, 255))
        de.text((mx + 50, my + 7), frase, font=f_ley_r, fill=(70, 80, 90, 255))
        de.text((mx + 50, my + 23), f"hacia el {rumbo(grados)}", font=f_ley, fill=(20, 25, 35, 255))
        ocupado.append((mx, my, mx + mw, my + mh))

    if codigo == "CO":
        try:
            cult = {}   # íconos de cultivos desactivados por ahora (datos/cultivos.json)
        except Exception:
            cult = {}
        tipos_usados, iconos_cajas = [], []
        for cod_d, tipos in cult.items():
            if cod_d.startswith("_"):
                continue
            pts_d = [a_px(*c_) for cm, _, an in muns if str(cm).zfill(5)[:2] == cod_d for r_ in an for c_ in r_[::20]]
            if not pts_d:
                continue
            cx_ = float(np.median([p_[0] for p_ in pts_d])); cy_ = float(np.median([p_[1] for p_ in pts_d]))
            for j, tp in enumerate(tipos):
                ic = icono_cultivo(tp, 24)
                xi_, yi_ = int(cx_ - 12 + (j - (len(tipos) - 1) / 2) * 22), int(cy_ - 12)
                etiquetas.alpha_composite(ic, (xi_, yi_))
                iconos_cajas.append((xi_, yi_, xi_ + 24, yi_ + 24))
                if tp not in tipos_usados:
                    tipos_usados.append(tp)
        f_reg = _fuente(11)
        for nom_r, xr_, yr_, col_r in []:   # nombres de región desactivados (solo se ven los colores)
            bb_ = medir.textbbox((0, 0), nom_r, font=f_reg, stroke_width=3)
            tw_, th_ = bb_[2] - bb_[0], bb_[3] - bb_[1]
            for dy_ in (0,):   # centrado dentro de la región
                caja_ = (xr_ - tw_ / 2, yr_ - th_ / 2, xr_ + tw_ / 2, yr_ + th_ / 2)
                if True:
                    de.text((caja_[0], caja_[1]), nom_r, font=f_reg, fill=(*[int(v * 0.55) for v in col_r], 255),
                            stroke_width=3, stroke_fill=(255, 255, 255, 230))
                    ocupado.append(caja_)
                    break
        if tipos_usados:
            nombres_c = {"maiz": "Maíz", "soya": "Soya", "frijol": "Fríjol"}
            ancho_c = 14 + sum(26 + int(medir.textlength(nombres_c[tp], font=f_ley)) + 12 for tp in tipos_usados)
            cx0, cy0 = ANCHO - 10 - ancho_c, ALTO_MAPA - 10 - 40
            de.rounded_rectangle((cx0, cy0, cx0 + ancho_c, cy0 + 40), radius=9, fill=(255, 255, 255, 255), outline=(205, 212, 220, 255))
            de.text((cx0 + 10, cy0 + 3), "Cultivos FENALCE", font=f_ley_r, fill=(70, 80, 90, 255))
            xx_ = cx0 + 10
            for tp in tipos_usados:
                etiquetas.alpha_composite(icono_cultivo(tp, 20), (int(xx_), int(cy0 + 17)))
                de.text((xx_ + 23, cy0 + 19), nombres_c[tp], font=f_ley, fill=(20, 25, 35, 255))
                xx_ += 26 + medir.textlength(nombres_c[tp], font=f_ley) + 12
            ocupado.append((cx0, cy0, cx0 + ancho_c, cy0 + 40))

    # aviso cuando el radar no muestra lluvia en el departamento (fuera de su alcance o aún sin lluvia)
    if lluvia_km2[-1] < 20 and rayos_dep >= 15:
        txt = ["El radar no muestra lluvia en esta zona;",
               ("la lluvia y los rayos que se ven son del satélite." if usa_sat else
                "los rayos (satélite) indican la tormenta.")]
        nw = int(max(medir.textlength(t, font=f_chip_r) for t in txt)) + 20
        nx, ny = ANCHO - 10 - nw, 10
        de.rounded_rectangle((nx, ny, nx + nw, ny + 42), radius=9, fill=(255, 248, 225, 255), outline=(220, 190, 120, 255))
        for i, t in enumerate(txt):
            de.text((nx + 10, ny + 6 + i * 16), t, font=f_chip_r, fill=(90, 60, 0, 255))
        ocupado.append((nx, ny, nx + nw, ny + 42))

    # municipios donde llueve (punto rojo) y la capital (cuadro azul)
    marcas = []
    for nom in resaltar:
        c = centros.get(str(nom).casefold())
        if c:
            marcas.append((_titulo(nom) if str(nom).isupper() else str(nom), c[1], "llueve"))
    if codigo != "CO" and capital and capital[1].casefold() not in {m[0].casefold() for m in marcas}:
        c = _centro(capital[2])
        if c:
            marcas.append((_titulo(capital[1]), a_px(*c), "capital"))

    def libre(b):
        return (b[0] >= 4 and b[1] >= 4 and b[2] <= ANCHO - 4 and b[3] <= ALTO_MAPA - 4 and
                all(b[2] < o[0] or b[0] > o[2] or b[3] < o[1] or b[1] > o[3] for o in ocupado))

    for nom, (x, y), tipo in marcas:
        if not (0 <= x < ANCHO and 0 <= y < ALTO_MAPA):
            continue
        if tipo == "llueve":
            de.ellipse((x - 5, y - 5, x + 5, y + 5), fill=(215, 25, 30, 255), outline=(255, 255, 255, 255), width=2)
        else:
            de.rectangle((x - 4, y - 4, x + 4, y + 4), fill=(*AZUL, 255), outline=(255, 255, 255, 255), width=2)
        bb = medir.textbbox((0, 0), nom, font=f_mun, stroke_width=3)
        tw, th_ = bb[2] - bb[0], bb[3] - bb[1]
        for dx, dy in ((10, -th_ / 2 - 2), (-10 - tw, -th_ / 2 - 2), (-tw / 2, -th_ - 10), (-tw / 2, 8)):
            caja = (x + dx, y + dy, x + dx + tw, y + dy + th_)
            if libre(caja):
                de.text((x + dx, y + dy), nom, font=f_mun, fill=(20, 25, 35, 255), stroke_width=3,
                        stroke_fill=(255, 255, 255, 255))
                ocupado.append(caja)
                break

    # ---- cabecera
    cab = Image.new("RGB", (ANCHO, ALTO_CAB), AZUL)
    dc = ImageDraw.Draw(cab)
    logo = None
    try:
        logo = Image.open(BASE / "datos" / "logo_fenalce_blanco.png").convert("RGBA")
        logo = logo.resize((round(logo.width * 30 / logo.height), 30), Image.LANCZOS)
    except Exception:
        pass
    lw_ = logo.width if logo else 0
    xt = 14
    if nivel in NIVELES:
        f_b = _fuente(15)
        bw = dc.textlength(titulo, font=f_b) + 18
        dc.rounded_rectangle((xt, 9, xt + bw, 33), radius=7, fill=color)
        dc.text((xt + 9, 12), titulo, font=f_b, fill=AZUL)
        xt += bw + 10
    texto = departamento.upper() if nivel in NIVELES else f"LLUVIA · {departamento.upper()}"
    tam = 23
    while tam > 13 and dc.textlength(texto, font=_fuente(tam)) > ANCHO - lw_ - 30 - xt:
        tam -= 1
    dc.text((xt, 8 + (23 - tam) // 2), texto, font=_fuente(tam), fill=(255, 255, 255))
    loc = t_ult.astimezone(ZONA)
    dc.text((14, 40), f"Lluvia fuerte · {DIAS[loc.weekday()]} {loc.day} de {MESES[loc.month - 1]} · radar, satélite y rayos",
            font=_fuente(14, False), fill=(200, 214, 228))
    wf = ANCHO / len(MARCA)
    for i, cm in enumerate(MARCA):
        dc.rectangle((round(i * wf), ALTO_CAB - 5, round((i + 1) * wf), ALTO_CAB), fill=cm)
    if logo:
        cab.paste(logo, (ANCHO - lw_ - 14, (ALTO_CAB - logo.height) // 2), logo)

    tiempos_ok = [t for t, _, _ in capas]

    def pie(idx):
        im = Image.new("RGB", (ANCHO, ALTO_PIE), CREMA)
        d = ImageDraw.Draw(im)
        n, xa0, xa1, sep = len(tiempos_ok), 16, ANCHO - 16, 3
        w = (xa1 - xa0 - sep * (n - 1)) / n
        for k in range(n):
            xa = xa0 + k * (w + sep)
            col = AZUL if k == idx else (TEAL if k < idx else (226, 220, 204))
            d.rectangle((xa, 8, xa + w, 15), fill=col)
        d.text((xa0, 19), _h12(tiempos_ok[0]), font=f_ley, fill=AZUL)
        fin = _h12(tiempos_ok[-1])
        d.text((xa1 - d.textlength(fin, font=f_ley), 19), fin, font=f_ley, fill=AZUL)
        fuentes = ("FENALCE   |   Radar: Rain-Alarm" + (" e IDEAM" if usa_ideam else "")
                   + (" · Satélite y rayos: GOES-19 (NOAA)" if usa_sat else " · Rayos: GOES-19 (NOAA)") + " · Mapa: Esri")
        d.text(((ANCHO - d.textlength(fuentes, font=f_pie)) / 2, 39), fuentes, font=f_pie, fill=(95, 105, 115))
        return im

    # ---- cuadros: portada (imagen más reciente) + animación de la última hora
    secuencia = [(True, capas[-1], len(capas) - 1)] + [(False, c, i) for i, c in enumerate(capas)]
    finales = []
    for portada, (t, img_ll, _), idx in secuencia:
        m = Image.alpha_composite(fondo, ir[t]) if t in ir else fondo
        m = Image.alpha_composite(m, capa_fuera)
        m = Image.alpha_composite(m, img_ll)
        m = Image.alpha_composite(m, encima)
        d = ImageDraw.Draw(m)
        if portada:
            t0, t1 = ahora.timestamp() - 15 * 60, ahora.timestamp()
        else:
            t0, t1 = (t - timedelta(minutes=5)).timestamp(), t.timestamp()
        for x, y in puntos(t0, t1):
            _rayo(d, x, y)
        m = Image.alpha_composite(m, etiquetas)
        d = ImageDraw.Draw(m)
        x, y, x2, y2 = caja_hora
        d.rounded_rectangle(caja_hora, radius=10, fill=(255, 255, 255, 255), outline=(205, 212, 220, 255))
        d.text((x + 11, y + 8), "ÚLTIMA IMAGEN DEL RADAR" if portada else "ANIMACIÓN · ÚLTIMA HORA", font=f_chip,
               fill=(*AZUL, 255))
        d.text((x + 10, y + 22), _h12(t), font=f_hora, fill=(20, 25, 35, 255))
        hace = max(0, round((ahora - t).total_seconds() / 60))
        d.text((x + 11, y + 57), f"hace {hace} min" + ("  ·  rayos: 15 min" if portada else ""), font=f_chip_r,
               fill=(90, 100, 110, 255))
        lienzo = Image.new("RGB", (ANCHO, ALTO), (255, 255, 255))
        lienzo.paste(cab, (0, 0))
        lienzo.paste(m.convert("RGB"), (0, ALTO_CAB))
        lienzo.paste(pie(idx), (0, ALTO_CAB + ALTO_MAPA))
        finales.append(lienzo)

    # una sola paleta para todos los cuadros (sin parpadeo de colores)
    muestra = Image.new("RGB", (ANCHO, ALTO * 3 + 60), (255, 255, 255))
    for i, f in enumerate((finales[0], finales[len(finales) // 2], finales[1])):
        muestra.paste(f, (0, i * ALTO))
    dmu = ImageDraw.Draw(muestra)          # los colores de la leyenda siempre entran en la paleta
    claros = [tuple(int(c[j] * ALFA_SAT / 255 + 240 * (1 - ALFA_SAT / 255)) for j in range(3)) for c in PALETA]
    for i, c in enumerate(PALETA + MARCA + claros):
        dmu.rectangle((i * 28, ALTO * 3, i * 28 + 27, ALTO * 3 + 59), fill=c)
    pal = muestra.quantize(colors=200, method=Image.Quantize.MEDIANCUT)
    gif = [f.quantize(palette=pal, dither=Image.Dither.NONE) for f in finales]
    dur = [2600] + [420] * (len(gif) - 2) + [1600]
    salida = Path(salida or BASE / "salida" / "clips" / f"clip_{str(codigo).zfill(2)}.gif")
    salida.parent.mkdir(parents=True, exist_ok=True)
    gif[0].save(salida, save_all=True, append_images=gif[1:], duration=dur, loop=0, optimize=False, disposal=1)
    info = {"archivo": salida, "hora": _h12(t_ult), "hace_min": max(0, round((ahora - t_ult).total_seconds() / 60)),
            "lluvia_km2": round(lluvia_km2[-1]), "rayos": rayos_dep, "cuadros": len(capas)}
    log.info("Clip %s: %d cuadros, última imagen %s (hace %d min), %.0f KB", salida.name, len(capas), info["hora"],
             info["hace_min"], salida.stat().st_size / 1024)
    return info


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    a = sys.argv[1:]
    print(generar(a[0], a[2] if len(a) > 2 else a[0], [m for m in (a[1].split(",") if len(a) > 1 else []) if m],
                  nivel=a[3] if len(a) > 3 else None))
