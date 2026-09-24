"""
qpe_ideam.py — Lluvia estimada con los radares de doble polarización del IDEAM (datos abiertos AWS).

Para un día local (00-24 h Colombia) y un radar:
  1. descarga el barrido más bajo (tarea de 1,3°, cada 5 min) desde s3://s3-radaresideam,
  2. filtra ecos no meteorológicos (RHOHV) y calcula la tasa de lluvia:
       - Marshall-Palmer  R(Z) = (Z/200)^(1/1.6)            (lo que usa hoy el sistema de alertas)
       - Híbrido doble pol.: R(KDP) = 21·KDP^0.72 en lluvia fuerte (KDP>0,3 y Z≥38 dBZ), si no R(Z)
  3. acumula la lluvia del día en cada estación y en una malla de 0,01° (mapa para el portal).

Los datos del IDEAM en AWS se publican al día siguiente: sirven para calibrar y para el
histórico, no para alertar en vivo.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import csv
import json
import math
import re
import tempfile
import urllib.request
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
BUCKET = "https://s3-radaresideam.s3.amazonaws.com"
RADARES = {"Barrancabermeja": "BAR", "Munchique": "CEM", "Carimagua": "CAR", "Guaviare": "GUA"}
COL = timezone(timedelta(hours=-5))
RANGO_MAX_KM = 200          # más lejos el haz va muy alto (>3-4 km) para estimar lluvia en superficie
PASO_GRILLA = 0.01          # grados (~1,1 km)


def listar(prefijo: str) -> list[str]:
    claves, token = [], None
    while True:
        url = f"{BUCKET}/?list-type=2&prefix={prefijo}&max-keys=1000" + (f"&continuation-token={urllib.request.quote(token)}" if token else "")
        xml = urllib.request.urlopen(url, timeout=60).read().decode()
        claves += re.findall(r"<Key>([^<]+)</Key>", xml)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", xml)
        if not m:
            return claves
        token = m.group(1)


def archivos_del_dia(radar: str, dia: date) -> list[tuple[datetime, str]]:
    """Archivos del barrido bajo (tarea que empieza en minuto múltiplo de 5) del día local."""
    ini = datetime(dia.year, dia.month, dia.day, tzinfo=COL)
    fin = ini + timedelta(days=1)
    salida = []
    for d in {ini.astimezone(timezone.utc).date(), (fin - timedelta(seconds=1)).astimezone(timezone.utc).date()}:
        for k in listar(f"l2_data/{d:%Y/%m/%d}/{radar}/"):
            m = re.search(r"([A-Z]{3})(\d{12})\.RAW", k)
            if not m:
                continue
            t = datetime.strptime(m.group(2), "%y%m%d%H%M%S").replace(tzinfo=timezone.utc)
            if t.minute % 5 == 0 and t.second < 45 and ini <= t < fin:
                salida.append((t, k))
    return sorted(salida)


def tasa_lluvia(dbz: np.ndarray, kdp: np.ndarray, rho: np.ndarray):
    """Devuelve (R_MP, R_hibrida) en mm/h."""
    dbz = np.where(np.isfinite(dbz), dbz, -99.0)
    meteo = (dbz >= 10) & (np.nan_to_num(rho, nan=0) >= 0.80)
    z = 10 ** (np.minimum(dbz, 53.0) / 10)
    r_mp = np.where(meteo, (z / 200.0) ** (1 / 1.6), 0.0)
    k = np.nan_to_num(kdp, nan=0.0)
    usa_kdp = meteo & (k > 0.3) & (dbz >= 38)
    r_hib = np.where(usa_kdp, 21.0 * np.maximum(k, 0) ** 0.72, r_mp)
    return r_mp, np.minimum(r_hib, 200.0)


def leer_barrido(ruta: str):
    import xradar as xd
    dt = xd.io.open_iris_datatree(ruta)
    sw = sorted(k for k in dt.children if k.startswith("sweep"))[0]
    ds = dt[sw].ds
    lat0 = float(dt["latitude"].values) if "latitude" in dt else float(ds["latitude"].values)
    lon0 = float(dt["longitude"].values) if "longitude" in dt else float(ds["longitude"].values)
    az = ds["azimuth"].values
    rg = ds["range"].values
    orden = np.argsort(az)
    get = lambda v: ds[v].values[orden] if v in ds else np.full((len(az), len(rg)), np.nan)
    datos = (lat0, lon0, az[orden], rg, get("DBZH"), get("KDP"), get("RHOHV"), get("DB_HCLASS"))
    try:
        dt.close()
    except Exception:
        pass
    return datos


def descargar(clave: str, carpeta: Path) -> str:
    destino = carpeta / clave.split("/")[-1]
    if not destino.exists():
        destino.write_bytes(urllib.request.urlopen(f"{BUCKET}/{clave}", timeout=120).read())
    return str(destino)


def geo_a_polar(lat0, lon0, lat, lon):
    """Distancia (m) y azimut (°) desde el radar (aprox. esférica)."""
    R = 6371000.0
    p1, p2 = math.radians(lat0), math.radians(lat)
    dl = math.radians(lon - lon0)
    d = 2 * R * math.asin(math.sqrt(math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2))
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return d, (math.degrees(math.atan2(y, x)) + 360) % 360


def procesar_dia(radar: str, dia: date, estaciones: list[dict], carpeta_salida: Path, max_archivos=None):
    archivos = archivos_del_dia(radar, dia)
    if max_archivos:
        archivos = archivos[:max_archivos]
    print(f"{radar} {dia}: {len(archivos)} barridos de 5 min")
    if not archivos:
        return None
    tmp = Path(tempfile.mkdtemp())
    acum_mp = acum_hib = None
    granizo = None
    n = 0
    with cf.ThreadPoolExecutor(6) as ex:
        rutas = list(ex.map(lambda a: descargar(a[1], tmp), archivos))
    for (t, _), ruta in zip(archivos, rutas):
        try:
            lat0, lon0, az, rg, dbz, kdp, rho, hcl = leer_barrido(ruta)
        except Exception as e:
            print("  omitido", ruta, e)
            continue
        if acum_mp is None:
            forma, az0, rg0 = dbz.shape, az, rg
            acum_mp = np.zeros(forma); acum_hib = np.zeros(forma); granizo = np.zeros(forma)
        if dbz.shape != forma:
            continue
        r_mp, r_hib = tasa_lluvia(dbz, kdp, rho)
        acum_mp += r_mp * (5 / 60)
        acum_hib += r_hib * (5 / 60)
        granizo = np.maximum(granizo, np.nan_to_num(dbz, nan=0) >= 55)
        n += 1
        try:
            Path(ruta).unlink(missing_ok=True)
        except OSError:   # Windows puede mantener el archivo abierto; se borra al final
            pass
    if n == 0:
        return None
    # --- estaciones
    filas = []
    for e in estaciones:
        d, a = geo_a_polar(lat0, lon0, e["lat"], e["lon"])
        if d > RANGO_MAX_KM * 1000:
            continue
        ia = int(np.argmin(np.abs(((az0 - a + 180) % 360) - 180)))
        ir = int(np.argmin(np.abs(rg0 - d)))
        sa = [(ia + k) % len(az0) for k in (-1, 0, 1)]
        sr = slice(max(ir - 1, 0), ir + 2)
        filas.append({"fecha": dia.isoformat(), "radar": radar, "estacion": e["id"], "nombre": e["name"],
                      "fuente": e["source"], "dist_km": round(d / 1000, 1),
                      "mm_radar_mp": round(float(acum_mp[sa, sr].mean()), 2),
                      "mm_radar_hibrido": round(float(acum_hib[sa, sr].mean()), 2),
                      "barridos": n, "mm_estacion": e["lluvia"].get(dia.isoformat())})
    # --- malla lat/lon para el mapa
    R = 6371000.0
    A, G = np.meshgrid(np.radians(az0), rg0, indexing="ij")
    lat = lat0 + np.degrees(G * np.cos(A) / R)
    lon = lon0 + np.degrees(G * np.sin(A) / (R * np.cos(np.radians(lat0))))
    dentro = G <= RANGO_MAX_KM * 1000
    return {"filas": filas, "lat": lat[dentro], "lon": lon[dentro], "mm": acum_hib[dentro],
            "granizo": granizo[dentro], "radar_latlon": (lat0, lon0), "barridos": n}


def cargar_estaciones(ruta_portal: Path) -> list[dict]:
    """Pluviómetros reales del portal: IDEAM (datos físicos) y estaciones FENALCE."""
    def leer(archivo):
        s = archivo.read_text(encoding="utf-8")
        return json.loads(s[s.index("{"): s.rstrip().rstrip(";").rindex("}") + 1])
    est = []
    for k, v in leer(ruta_portal / "ideam_hibridas.js").items():
        p = v.get("physical_data") or {}
        lluvia = {d: r for d, r in zip(p.get("dates", []), p.get("rain", [])) if r is not None}
        est.append({"id": k, "name": v["name"], "lat": v["lat"], "lon": v["lon"], "source": "ideam", "lluvia": lluvia})
    for k, v in leer(ruta_portal / "data.js").items():
        if v.get("source_type") != "fenalce":
            continue
        d = v["data"]
        lluvia = {f: r for f, r in zip(d["dates"], d["rain"]) if r is not None}
        est.append({"id": k, "name": v["name"], "lat": v["lat"], "lon": v["lon"], "source": "fenalce", "lluvia": lluvia})
    return est


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--portal", required=True, help="carpeta site/estaciones del portal (o URL raw)")
    p.add_argument("--dias", type=int, default=1, help="cuántos días hacia atrás procesar (desde ayer)")
    p.add_argument("--fecha", default=None, help="AAAA-MM-DD (en vez de --dias)")
    p.add_argument("--radares", default="Barrancabermeja,Munchique,Carimagua,Guaviare")
    p.add_argument("--salida", default="calibracion")
    p.add_argument("--max-archivos", type=int, default=None, help="solo para pruebas")
    a = p.parse_args()
    salida = Path(a.salida); salida.mkdir(parents=True, exist_ok=True)
    estaciones = cargar_estaciones(Path(a.portal))
    print(f"{len(estaciones)} estaciones con pluviómetro")
    hoy = datetime.now(COL).date()
    dias = [date.fromisoformat(a.fecha)] if a.fecha else [hoy - timedelta(days=i) for i in range(a.dias, 0, -1)]
    csv_ruta = salida / "radar_ideam_estaciones.csv"
    previas = []
    if csv_ruta.exists():
        with csv_ruta.open(encoding="utf-8") as f:
            previas = [r for r in csv.DictReader(f)]
    hechos = {(r["fecha"], r["radar"]) for r in previas}
    nuevas = []
    for dia in dias:
        for radar in a.radares.split(","):
            if (dia.isoformat(), radar) in hechos and not a.max_archivos:
                continue
            res = procesar_dia(radar, dia, estaciones, salida, a.max_archivos)
            if not res:
                continue
            nuevas += res["filas"]
            guardar_mapa(res, radar, dia, salida)
    todas = previas + nuevas
    if todas:
        with csv_ruta.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(todas[0].keys()))
            w.writeheader(); w.writerows(todas)
    print(f"{len(nuevas)} filas nuevas; total {len(todas)}")
    resumen(todas, salida / "calibracion.json")


def resumen(filas: list[dict], ruta: Path, umbral_mm: float = 1.0):
    """Radar vs pluviómetro: sesgo, correlación y detección de días de lluvia, por radar y método."""
    out = {"generado": datetime.now(COL).isoformat(timespec="minutes"), "umbral_dia_lluvia_mm": umbral_mm,
           "nota": "Días locales 00-24 h. Estaciones a menos de 200 km del radar. Barridos de 1,3° cada 5 min.",
           "radares": {}}
    por_radar: dict[str, list] = {}
    for r in filas:
        if r.get("mm_estacion") in (None, "", "None"):
            continue
        if int(float(r.get("barridos", 0) or 0)) < 200:      # día con menos del ~70 % de barridos
            continue
        por_radar.setdefault(r["radar"], []).append(r)
    for radar, rs in por_radar.items():
        g = np.array([float(r["mm_estacion"]) for r in rs])
        info = {"pares": len(rs), "dias": len({r["fecha"] for r in rs}), "estaciones": len({r["estacion"] for r in rs})}
        for metodo, col in (("marshall_palmer", "mm_radar_mp"), ("doble_polarizacion", "mm_radar_hibrido")):
            x = np.array([float(r[col]) for r in rs])
            lluvia = (g >= umbral_mm) | (x >= umbral_mm)
            sesgo = float(x[lluvia].sum() / g[lluvia].sum()) if g[lluvia].sum() > 0 else None
            corr = float(np.corrcoef(x[lluvia], g[lluvia])[0, 1]) if lluvia.sum() >= 5 else None
            aciertos = int(((g >= umbral_mm) & (x >= umbral_mm)).sum())
            pod = aciertos / max(int((g >= umbral_mm).sum()), 1)
            far = int(((g < umbral_mm) & (x >= umbral_mm)).sum()) / max(int((x >= umbral_mm).sum()), 1)
            info[metodo] = {"sesgo_radar_sobre_estacion": None if sesgo is None else round(sesgo, 2),
                            "factor_sugerido": None if not sesgo else round(1 / sesgo, 2),
                            "correlacion": None if corr is None or np.isnan(corr) else round(corr, 2),
                            "deteccion_dias_lluvia_pod": round(pod, 2), "falsas_alarmas_far": round(far, 2)}
        out["radares"][radar] = info
    ruta.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


def guardar_mapa(res, radar, dia, salida: Path):
    """Acumulado diario en malla 0,01° → PNG transparente + límites, para el portal."""
    from PIL import Image
    lat, lon, mm = res["lat"], res["lon"], res["mm"]
    la0, la1, lo0, lo1 = lat.min(), lat.max(), lon.min(), lon.max()
    ny, nx = int((la1 - la0) / PASO_GRILLA) + 1, int((lo1 - lo0) / PASO_GRILLA) + 1
    iy = ((la1 - lat) / PASO_GRILLA).astype(int); ix = ((lon - lo0) / PASO_GRILLA).astype(int)
    suma = np.zeros((ny, nx)); cuenta = np.zeros((ny, nx))
    np.add.at(suma, (iy, ix), mm); np.add.at(cuenta, (iy, ix), 1)
    g = np.divide(suma, cuenta, out=np.full((ny, nx), np.nan), where=cuenta > 0)
    # escala de acumulado diario (mm): 1, 5, 10, 20, 30, 50, 75, 100
    cortes = [1, 5, 10, 20, 30, 50, 75, 100]
    colores = [(190, 230, 255), (120, 190, 250), (60, 140, 230), (30, 90, 200), (60, 180, 80),
               (250, 220, 0), (250, 130, 0), (220, 30, 30)]
    img = np.zeros((ny, nx, 4), np.uint8)
    for c, col in zip(cortes, colores):
        img[g >= c] = (*col, 200)
    Image.fromarray(img).save(salida / f"lluvia_{radar}_{dia.isoformat()}.png", optimize=True)
    meta_ruta = salida / "mapas_radar_ideam.json"
    meta = json.loads(meta_ruta.read_text(encoding="utf-8")) if meta_ruta.exists() else {}
    meta[f"{radar}_{dia.isoformat()}"] = {"radar": radar, "fecha": dia.isoformat(), "archivo": f"lluvia_{radar}_{dia.isoformat()}.png",
                                         "bounds": [[float(la0), float(lo0)], [float(la1), float(lo1)]],
                                         "radar_latlon": list(res["radar_latlon"]), "barridos": res["barridos"],
                                         "max_mm": round(float(np.nanmax(g)), 1) if np.isfinite(g).any() else 0}
    meta_ruta.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
