"""
clip_radar.py — Clip animado (GIF) de la lluvia de la última hora sobre un departamento,
para acompañar las alertas en WhatsApp (como los clips que comparte el IDEAM).

Capas: mapa base (OpenStreetMap / CARTO), lluvia del radar Rain-Alarm (12 cuadros, cada 5 min),
límites de municipios, municipios donde llueve resaltados y rayos de los últimos 15 min.

   python clip_radar.py 73 "Ibagué,Chaparral"   → salida/clips/clip_73.gif
"""
from __future__ import annotations

import io
import json
import logging
import math
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

log = logging.getLogger("clip")
BASE = Path(__file__).resolve().parent
H = {"User-Agent": "Mozilla/5.0 (FENALCE agroclimatologia)", "Referer": "https://agroclima-fenalce-portal.vercel.app/"}
MAPA = "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}"
LLUVIA = "https://images.rain-alarm.com/rain/g2/z{z}/{bx}_{by}/{x}_{y}{suf}.png"
ZONA = timezone(timedelta(hours=-5))
MAX_LADO = 620
_GEO = None


def _fuente(t):
    for f in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(f, t)
        except OSError:
            pass
    return ImageFont.load_default()


def _px(lon, lat, z):
    n = 256 * 2 ** z
    s = math.sin(math.radians(max(-85, min(85, lat))))
    return (lon + 180) / 360 * n, (0.5 - math.log((1 + s) / (1 - s)) / (4 * math.pi)) * n


def _baja(url):
    try:
        b = urllib.request.urlopen(urllib.request.Request(url, headers=H), timeout=30).read()
        return Image.open(io.BytesIO(b)).convert("RGBA")
    except Exception:
        return None


def _municipios(codigo):
    global _GEO
    if _GEO is None:
        _GEO = json.loads((BASE / "datos" / "municipios_mgn2018.geojson").read_text(encoding="utf-8"))["features"]
    out = []
    for f in _GEO:
        p = f["properties"]
        if str(p.get("DPTO_CCDGO")).zfill(2) != str(codigo).zfill(2):
            continue
        g = f["geometry"]
        polys = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
        out.append((p.get("MPIO_CNMBR", ""), [ring for poly in polys for ring in poly[:1]]))
    return out


def _mosaico(tpl, z, x0, x1, y0, y1, suf=""):
    img = Image.new("RGBA", ((x1 - x0 + 1) * 256, (y1 - y0 + 1) * 256), (0, 0, 0, 0))
    pares = [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]
    with ThreadPoolExecutor(8) as ex:
        res = ex.map(lambda p: (p, _baja(tpl.format(z=z, x=p[0], y=p[1], bx=p[0] // 8, by=p[1] // 8, suf=suf))), pares)
    ok = 0
    for (x, y), t in res:
        if t is not None:
            img.paste(t, ((x - x0) * 256, (y - y0) * 256)); ok += 1
    return img if ok else None


def _rayos(z, ox, oy):
    try:
        g = json.loads((BASE / "salida" / "rayos.geojson").read_text(encoding="utf-8"))
        lim = datetime.now(timezone.utc).timestamp() - 15 * 60
        pts = set()
        for f in g.get("features", []):
            if (f["properties"].get("t") or 0) < lim:
                continue
            lon, lat = f["geometry"]["coordinates"][:2]
            x, y = _px(lon, lat, z)
            pts.add((int(x - ox) // 6 * 6, int(y - oy) // 6 * 6))
        return sorted(pts)
    except Exception:
        return []


def generar(codigo, departamento="", resaltar=(), salida=None) -> Path | None:
    muns = _municipios(codigo)
    if not muns:
        return None
    lons = [c[0] for _, rings in muns for r in rings for c in r]
    lats = [c[1] for _, rings in muns for r in rings for c in r]
    lo0, lo1, la0, la1 = min(lons), max(lons), min(lats), max(lats)
    z = 6
    for zz in range(9, 5, -1):
        ax, ay = _px(lo0, la1, zz); bx_, by_ = _px(lo1, la0, zz)
        if max(bx_ - ax, by_ - ay) * 1.15 <= MAX_LADO:
            z = zz; break
    ax, ay = _px(lo0, la1, z); bx_, by_ = _px(lo1, la0, z)
    cx, cy = (ax + bx_) / 2, (ay + by_) / 2
    lado = max(bx_ - ax, by_ - ay) * 1.15
    w = int(max(lado, 420)); h = int(max(lado, 420))
    ox, oy = int(cx - w / 2), int(cy - h / 2)
    tx0, ty0, tx1, ty1 = ox // 256, oy // 256, (ox + w) // 256, (oy + h) // 256
    corte = (ox - tx0 * 256, oy - ty0 * 256, ox - tx0 * 256 + w, oy - ty0 * 256 + h)

    base = _mosaico(MAPA.replace("{x}", "{x}").replace("{y}", "{y}"), z, tx0, tx1, ty0, ty1)
    base = base.crop(corte) if base else Image.new("RGBA", (w, h), (235, 238, 240, 255))

    # límites de municipios
    lineas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(lineas)
    mascara = Image.new("L", (w, h), 0)
    dm = ImageDraw.Draw(mascara)
    res = {m.lower() for m in resaltar}
    centros = {}
    for nom, rings in muns:
        for r in rings:
            pts = [(_px(lo, la, z)[0] - ox, _px(lo, la, z)[1] - oy) for lo, la in r[:: max(1, len(r) // 300)]]
            if len(pts) > 2:
                d.line(pts + [pts[0]], fill=(60, 70, 90, 120), width=1)
                dm.polygon(pts, fill=255)
                if nom.lower() in res and nom not in centros:
                    centros[nom] = (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))

    # cuadros de lluvia: última hora cada 5 min
    ahora = datetime.now(timezone.utc)
    t0 = ahora.replace(second=0, microsecond=0) - timedelta(minutes=ahora.minute % 5)
    tiempos = [t0 - timedelta(minutes=5 * i) for i in range(11, 0, -1)]
    with ThreadPoolExecutor(4) as ex:
        capas = list(ex.map(lambda t: (t, _mosaico(LLUVIA, z, tx0, tx1, ty0, ty1, f"_{t:%H%M}")), tiempos))
    capas.append((t0, _mosaico(LLUVIA, z, tx0, tx1, ty0, ty1)))
    capas = [(t, c.crop(corte)) for t, c in capas if c is not None]
    if not capas:
        log.warning("Clip %s: sin cuadros de radar", codigo)
        return None

    # fuera del departamento se aclara el mapa; el borde del departamento va grueso
    fuera = Image.new("RGBA", (w, h), (255, 255, 255, 120))
    fuera.putalpha(mascara.point(lambda v: 0 if v else 120))
    borde = mascara.filter(ImageFilter.FIND_EDGES).filter(ImageFilter.MaxFilter(3))
    capa_borde = Image.new("RGBA", (w, h), (15, 60, 110, 255))
    capa_borde.putalpha(borde.point(lambda v: 255 if v else 0))
    rayos = _rayos(z, ox, oy)
    f_tit, f_txt, f_peq = _fuente(17), _fuente(13), _fuente(10)
    cuadros = []
    for i, (t, lluvia) in enumerate(capas):
        # en Rain-Alarm el gris es nubosidad: se suaviza para que resalte la lluvia
        px = lluvia.load()
        for yy in range(0, h):
            for xx in range(0, w):
                r_, g_, b_, a_ = px[xx, yy]
                if a_ and abs(r_ - g_) < 12 and abs(g_ - b_) < 12:
                    px[xx, yy] = (r_, g_, b_, 70)
        f = Image.alpha_composite(base, lluvia)
        f = Image.alpha_composite(f, lineas)
        f = Image.alpha_composite(f, fuera)
        f = Image.alpha_composite(f, capa_borde)
        dd = ImageDraw.Draw(f)
        if i >= len(capas) - 3:
            for x, y in rayos:
                dd.polygon([(x + 1, y - 5), (x - 3, y + 1), (x, y + 1), (x - 1, y + 6), (x + 4, y - 1), (x + 1, y - 1)],
                           fill=(255, 200, 0, 255), outline=(90, 50, 0, 255))
        for nom, (x, y) in centros.items():
            dd.ellipse((x - 3, y - 3, x + 3, y + 3), fill=(220, 30, 30, 255))
            dd.text((x + 5, y - 7), nom, font=f_txt, fill=(20, 20, 20, 255), stroke_width=2, stroke_fill=(255, 255, 255, 255))
        dd.rectangle((0, 0, w, 44), fill=(15, 60, 110, 230))
        dd.text((8, 4), f"Lluvia última hora · {departamento}", font=f_tit, fill="white")
        loc = t.astimezone(ZONA)
        dd.text((8, 25), f"{loc:%d/%m}  {loc.hour % 12 or 12}:{loc:%M} {'a. m.' if loc.hour < 12 else 'p. m.'}"
                + ("   · rayos: últimos 15 min (amarillo)" if rayos and i >= len(capas) - 3 else ""), font=f_txt, fill=(220, 235, 255))
        dd.rectangle((0, h - 16, w, h), fill=(255, 255, 255, 200))
        dd.text((6, h - 14), "FENALCE Agroclimatología · Radar: Rain-Alarm · Rayos: GOES-19 · Mapa: Esri",
                font=f_peq, fill=(60, 60, 60))
        cuadros.append(f.convert("RGB").quantize(colors=128, method=Image.Quantize.MEDIANCUT))
    salida = Path(salida or BASE / "salida" / "clips" / f"clip_{str(codigo).zfill(2)}.gif")
    salida.parent.mkdir(parents=True, exist_ok=True)
    dur = [450] * (len(cuadros) - 1) + [1800]
    cuadros[0].save(salida, save_all=True, append_images=cuadros[1:], duration=dur, loop=0, optimize=True)
    log.info("Clip %s: %d cuadros, %.0f KB", salida.name, len(cuadros), salida.stat().st_size / 1024)
    return salida


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    cod = sys.argv[1]
    mun = sys.argv[2].split(",") if len(sys.argv) > 2 else []
    print(generar(cod, sys.argv[3] if len(sys.argv) > 3 else cod, mun))
