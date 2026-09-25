"""
indice_nubes.py — Probabilidad de lluvia fuerte (próximas 1–2 h) por departamento, a partir de
las nubes y el agua en la atmósfera, no de un "radar" que en Colombia no tenemos en vivo.

Señales (todas con datos abiertos):
  • GOES-19 ABI banda 13 (infrarrojo 10,3 µm, cada 10 min): temperatura de la cima de las nubes.
      – área con cima < −40 °C (nube alta y densa, capaz de aguacero)
      – área con cima < −60 °C (núcleos de tormenta profunda)
      – crecimiento: cambio del área fría respecto a hace 30 min
  • GOES-19 ABI grosor óptico de nube (COD, solo de día): nube gruesa vs. velo delgado.
  • GOES-19 GLM: rayos en los últimos 15 min.
  • Open-Meteo (modelo): probabilidad de lluvia, agua precipitable (TCWV) y CAPE, próximas 3 h.

Uso:  python indice_nubes.py            (imprime la tabla y guarda salida/indice_nubes.json)
"""
from __future__ import annotations

import io
import json
import logging
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

import analisis as an
import motor_alertas as ma

log = logging.getLogger("nubes")
BUCKET = "https://noaa-goes19.s3.amazonaws.com"
BASE = Path(__file__).resolve().parent
_IDX = {}


# --------------------------------------------------------------------------- GOES
def _listar(prefijo):
    xml = urllib.request.urlopen(f"{BUCKET}/?list-type=2&prefix={prefijo}&max-keys=1000", timeout=30).read().decode()
    return re.findall(r"<Key>([^<]+)</Key>", xml)


def _inicio(k):
    y, j, h, mi, s = map(int, re.search(r"_s(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})", k).groups())
    return datetime(y, 1, 1, h, mi, s, tzinfo=timezone.utc) + timedelta(days=j - 1)


def claves(producto, filtro="", horas=2):
    ahora = datetime.now(timezone.utc)
    ks = []
    for dh in range(horas, -1, -1):
        h = ahora - timedelta(hours=dh)
        ks += _listar(f"{producto}/{h:%Y}/{h.timetuple().tm_yday:03d}/{h:%H}/")
    return sorted((k for k in ks if filtro in k), key=_inicio)


def _escalar(ds, sl=None):
    v = ds[sl] if sl is not None else ds[()]
    a = ds.attrs
    fill = a.get("_FillValue")
    if a.get("_Unsigned") in (b"true", "true") and v.dtype.kind == "i":
        v = v.view(v.dtype.str.replace("i", "u"))
        if fill is not None:
            fill = np.array(fill).astype(v.dtype)
    malo = (v == np.ravel(fill)[0]) if fill is not None else np.zeros(v.shape, bool)
    v = v.astype("float32")
    if "scale_factor" in a:
        v = v * float(np.ravel(a["scale_factor"])[0])
    if "add_offset" in a:
        v = v + float(np.ravel(a["add_offset"])[0])
    v[malo] = np.nan
    return v


def _indice(malla, f):
    x = _escalar(f["x"]).astype("float64"); y = _escalar(f["y"]).astype("float64")
    k = (malla.clave, len(x))
    if k in _IDX:
        return _IDX[k]
    p = f["goes_imager_projection"].attrs
    req = float(np.ravel(p["semi_major_axis"])[0]); rpol = float(np.ravel(p["semi_minor_axis"])[0])
    H = float(np.ravel(p["perspective_point_height"])[0]) + req
    lon0 = np.radians(float(np.ravel(p["longitude_of_projection_origin"])[0]))
    py, px = np.mgrid[0:malla.alto, 0:malla.ancho]
    lon, lat = malla.a_lonlat(px + 0.5, py + 0.5)
    lon, lat = np.radians(lon), np.radians(lat)
    e2 = (req ** 2 - rpol ** 2) / req ** 2
    latc = np.arctan((rpol ** 2 / req ** 2) * np.tan(lat))
    rc = rpol / np.sqrt(1 - e2 * np.cos(latc) ** 2)
    sx = H - rc * np.cos(latc) * np.cos(lon - lon0)
    sy = -rc * np.cos(latc) * np.sin(lon - lon0)
    sz = rc * np.sin(latc)
    ax = np.arcsin(-sy / np.sqrt(sx ** 2 + sy ** 2 + sz ** 2)); ay = np.arctan(sz / sx)
    ix = np.clip(np.rint((ax - x[0]) / (x[1] - x[0])).astype(np.int32), 0, len(x) - 1)
    iy = np.clip(np.rint((ay - y[0]) / (y[1] - y[0])).astype(np.int32), 0, len(y) - 1)
    _IDX[k] = (iy, ix)
    return _IDX[k]


def leer_campo(clave, variable, malla):
    import h5py
    datos = urllib.request.urlopen(f"{BUCKET}/{clave}", timeout=180).read()
    with h5py.File(io.BytesIO(datos), "r") as f:
        iy, ix = _indice(malla, f)
        y0, y1, x0, x1 = int(iy.min()), int(iy.max()) + 1, int(ix.min()), int(ix.max()) + 1
        v = _escalar(f[variable], (slice(y0, y1), slice(x0, x1)))
    return v[iy - y0, ix - x0]


# --------------------------------------------------------------------------- modelo
def open_meteo(puntos):
    lat = ",".join(f"{la:.3f}" for _, la in puntos); lon = ",".join(f"{lo:.3f}" for lo, _ in puntos)
    url = ("https://api.open-meteo.com/v1/forecast?latitude=" + lat + "&longitude=" + lon +
           "&hourly=precipitation_probability,total_column_integrated_water_vapour,cape,"
           "wind_speed_10m,wind_direction_10m,wind_speed_500hPa,wind_direction_500hPa"
           "&forecast_hours=3&timezone=UTC")
    r = json.load(urllib.request.urlopen(url, timeout=60))
    r = r if isinstance(r, list) else [r]
    out = []
    for x in r:
        h = x["hourly"]
        mx = lambda k: max([v for v in h[k] if v is not None] or [0])
        p0 = lambda k: (h.get(k) or [None])[0]
        out.append({"prob": mx("precipitation_probability"), "tcwv": mx("total_column_integrated_water_vapour"),
                    "cape": mx("cape"), "viento_kmh": p0("wind_speed_10m"), "viento_dir": p0("wind_direction_10m"),
                    "alto_kmh": p0("wind_speed_500hPa"), "alto_dir": p0("wind_direction_500hPa")})
    return out


# --------------------------------------------------------------------------- desplazamiento
def movimiento(bt, bt0, t, d, minutos=30):
    """Hacia dónde se mueven las nubes densas del departamento (correlación entre la imagen
    de hace `minutos` y la actual). Devuelve None si no hay nubes o el cálculo no es confiable."""
    y0, y1, x0, x1 = t.ventana(d, 80)
    a = np.clip(-40 - np.nan_to_num(bt0[y0:y1, x0:x1], nan=0), 0, None)
    b = np.clip(-40 - np.nan_to_num(bt[y0:y1, x0:x1], nan=0), 0, None)
    if (a > 0).sum() < 50 or (b > 0).sum() < 50:
        return None
    dy, dx, calidad = an.correlacion_fase(a, b)
    km = float(t.km_px_filas[(y0 + y1) // 2])
    vel = (dy * dy + dx * dx) ** 0.5 * km * 60 / minutos
    if calidad < 8 or vel > 90:
        return None
    grados = float(np.degrees(np.arctan2(dx, -dy)) % 360)
    return {"hacia": an.rumbo(grados), "vel_kmh": vel, "grados": grados}


# --------------------------------------------------------------------------- índice
# En la Orinoquía, Amazonía y el Pacífico las nubes frías extensas son lo normal: se exige más área.
REGION = {**{c: ("andina", 1.0) for c in "05 11 15 17 25 41 54 63 66 68 73 19 76".split()},
          **{c: ("caribe", 1.5) for c in "08 13 20 23 44 47 70 88".split()},
          **{c: ("pacífico", 1.5) for c in "27 52".split()},
          **{c: ("oriente", 2.0) for c in "50 81 85 99 18 86 91 94 95 97".split()}}


def puntaje(s, codigo=""):
    p, razones = 0, []
    _, f = REGION.get(codigo, ("", 1.0))
    s = dict(s, frio40=s["frio40"] / f, frio60=s["frio60"] / f,
             cod_gruesa=(s["cod_gruesa"] / f if s["cod_gruesa"] is not None else None))
    if s["frio40"] >= 2000: p += 3; razones.append(f"nubes densas (cima < −40 °C) en ~{s['frio40']:,.0f} km²")
    elif s["frio40"] >= 500: p += 2; razones.append(f"nubes densas en ~{s['frio40']:,.0f} km²")
    elif s["frio40"] >= 100: p += 1; razones.append(f"nubes densas en ~{s['frio40']:,.0f} km²")
    if s["frio60"] >= 200: p += 2; razones.append(f"núcleos de tormenta (cima < −60 °C) en ~{s['frio60']:,.0f} km²")
    elif s["frio60"] >= 30: p += 1; razones.append(f"núcleos de tormenta en ~{s['frio60']:,.0f} km²")
    if s["frio40"] >= 100 and s["crec"] is not None:
        if s["crec"] >= 1.3: p += 1; razones.append("nubes creciendo (+{:.0f} % en 30 min)".format((s["crec"] - 1) * 100))
        elif s["crec"] <= 0.7: p -= 1; razones.append("nubes disipándose")
    if s["rayos"] >= 50: p += 2; razones.append(f"{s['rayos']} rayos en 15 min")
    elif s["rayos"] >= 5: p += 1; razones.append(f"{s['rayos']} rayos en 15 min")
    if s["cod_gruesa"] is not None and s["cod_gruesa"] >= 500: p += 1; razones.append(f"nubes gruesas (COD ≥ 30) en ~{s['cod_gruesa']:,.0f} km²")
    m = s["modelo"]
    if m:
        if m["prob"] >= 70 and m["tcwv"] >= 45: p += 1
        if m["cape"] >= 1500: p += 1
        razones.append(f"modelo: prob. lluvia {m['prob']:.0f} %, agua precipitable {m['tcwv']:.0f} mm, CAPE {m['cape']:.0f} J/kg")
    nivel = "ALTA" if p >= 6 else "MEDIA" if p >= 3 else "BAJA"
    return p, nivel, razones


def calcular(cfg=None):
    cfg = cfg or yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
    act = ma.departamentos_activos(cfg); cod = sorted(act)
    malla = ma.construir_malla(cfg, cod)
    t = an.Territorio(BASE / cfg["territorio"]["geojson"], malla, cfg["territorio"]["campos"])
    nd = t.n_dep + 1

    k13 = claves("ABI-L2-CMIPF", "M6C13", 1)
    ahora_k = k13[-1]
    antes_k = min(k13, key=lambda k: abs((_inicio(ahora_k) - _inicio(k)).total_seconds() - 1800))
    log.info("IR ahora %s, antes %s", _inicio(ahora_k), _inicio(antes_k))
    bt = leer_campo(ahora_k, "CMI", malla) - 273.15
    bt0 = leer_campo(antes_k, "CMI", malla) - 273.15

    def area(mask):
        return np.bincount(t.dep_raster[mask], weights=t.area_px[mask], minlength=nd)

    f40, f60, f40_0 = area(bt < -40), area(bt < -60), area(bt0 < -40)
    btmin = np.full(nd, np.nan)
    for d in range(1, nd):
        m = t.dep_raster == d
        if m.any():
            btmin[d] = np.nanmin(bt[m])

    cod_g = None
    try:
        kc = claves("ABI-L2-CODF", "", 1)[-1]
        codv = leer_campo(kc, "COD", malla)
        if np.isfinite(codv).mean() > 0.05:
            cod_g = area(np.nan_to_num(codv) >= 30)
    except Exception as e:
        log.warning("COD no disponible: %s", e)

    rayos = {}
    try:
        import rayos_glm
        r = rayos_glm.descargar_rayos(15)
        rayos = rayos_glm.conteo_por_departamento(r, t, 15)
    except Exception as e:
        log.warning("Rayos: %s", e)

    ds = [t.dep_indice[c] for c in cod if c in t.dep_indice]
    puntos = []
    frio = bt < -40
    for d in ds:
        yy, xx = np.nonzero(frio & (t.dep_raster == d))
        if len(yy) >= 20:   # centro de las nubes densas del departamento
            cx, cy = xx.mean(), yy.mean()
        else:
            cx, cy = t.dep_centro[d]
        puntos.append(tuple(float(v) for v in malla.a_lonlat(cx, cy)))
    try:
        modelo = dict(zip(ds, open_meteo(puntos)))
    except Exception as e:
        log.warning("Open-Meteo: %s", e); modelo = {}

    res = []
    for c in cod:
        d = t.dep_indice.get(c)
        if d is None:
            continue
        s = {"frio40": float(f40[d]), "frio60": float(f60[d]), "btmin": float(btmin[d]),
             "crec": (float(f40[d]) / float(f40_0[d])) if f40_0[d] >= 50 else (2.0 if f40[d] >= 100 else None),
             "rayos": int(rayos.get(d, 0)), "cod_gruesa": float(cod_g[d]) if cod_g is not None else None,
             "modelo": modelo.get(d)}
        p, nivel, razones = puntaje(s, c)
        # municipios con más nube densa
        m = (bt < -40) & (t.dep_raster == d)
        mun = np.bincount(t.etiquetas[m], weights=t.area_px[m], minlength=t.n_mun + 1)
        top = [t.mun_nombre[i] for i in np.argsort(-mun)[:4] if mun[i] >= 20]
        res.append({"movimiento": movimiento(bt, bt0, t, d) if s["frio40"] >= 100 else None,
                    "viento": {k: v for k, v in (s["modelo"] or {}).items() if "dir" in k or "kmh" in k},
                    "codigo": c, "region": REGION.get(c, ("", 1))[0], "departamento": act[c]["nombre"], "puntaje": p, "probabilidad": nivel,
                    "municipios": top, "razones": razones, **{k: v for k, v in s.items() if k != "modelo"}})
    res.sort(key=lambda r: -r["puntaje"])
    hora = _inicio(ahora_k).astimezone(timezone(timedelta(hours=-5))).strftime("%Y-%m-%d %H:%M")
    (BASE / "salida").mkdir(exist_ok=True)
    (BASE / "salida" / "indice_nubes.json").write_text(json.dumps({"hora_satelite": hora, "departamentos": res},
                                                               ensure_ascii=False, indent=1), encoding="utf-8")
    return hora, res


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    hora, res = calcular()
    print(f"\nÍNDICE DE PROBABILIDAD DE LLUVIA FUERTE (próximas 1–2 h) · satélite {hora} hora Colombia")
    print(f"{'Departamento':18} {'Prob.':6} {'pts':>3} {'<-40°C':>8} {'<-60°C':>7} {'cima':>6} {'crec.':>6} {'rayos':>5}  municipios")
    for r in res:
        cr = f"{(r['crec'] - 1) * 100:+.0f}%" if r["crec"] is not None else "  —"
        print(f"{r['departamento'][:18]:18} {r['probabilidad']:6} {r['puntaje']:3d} {r['frio40']:8.0f} {r['frio60']:7.0f} "
              f"{r['btmin']:5.0f}° {cr:>6} {r['rayos']:5d}  {', '.join(r['municipios'])}")
    print()
    for r in res[:6]:
        print(f"• {r['departamento']} ({r['probabilidad']}): " + "; ".join(r["razones"]))


if __name__ == "__main__":
    sys.exit(main())
