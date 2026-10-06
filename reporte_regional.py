"""
reporte_regional.py — Reporte nacional explicado por regiones naturales (Andina, Caribe, Pacífica,
Orinoquía y Amazonía): dónde llueve (radar y satélite), cómo están las nubes, rayos, hacia dónde se
mueven y qué departamentos tienen probabilidad de lluvia fuerte.

   python reporte_regional.py      → imprime el mensaje
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

import analisis as an
import motor_alertas as ma

log = logging.getLogger("regional")
BASE = Path(__file__).resolve().parent
ZONA = timezone(timedelta(hours=-5))
REGIONES = [("andina", "Andina", "🏔️"), ("caribe", "Caribe", "🏖️"), ("pacifica", "Pacífica", "🌊"),
            ("orinoquia", "Orinoquía", "🌾"), ("amazonia", "Amazonía", "🌳")]
POR_DEPTO = {**{c: "caribe" for c in "08 13 20 23 44 47 70".split()},
             "27": "pacifica",
             **{c: "orinoquia" for c in "50 81 85 99".split()},
             **{c: "amazonia" for c in "18 86 91 94 95 97".split()}}
# municipios del litoral Pacífico (Valle, Cauca, Nariño) y de Urabá antioqueño (Caribe)
POR_MUNICIPIO = {**{m: "pacifica" for m in "76109 19318 19418 19809 52079 52250 52390 52427 52473 52490 52520 52621 52696 52835".split()},
                 **{m: "caribe" for m in "05045 05051 05147 05172 05490 05659 05665 05837".split()}}
RUMBOS = ["norte", "nororiente", "oriente", "suroriente", "sur", "suroccidente", "occidente", "noroccidente"]
_CACHE = {}


def _region_base(cod_mun: str) -> str:
    cod_mun = str(cod_mun).zfill(5)
    return POR_MUNICIPIO.get(cod_mun) or POR_DEPTO.get(cod_mun[:2], "andina")


def region_de(cod_mun: str) -> str:
    """Región natural del municipio. Usa datos/regiones_municipios.json (ajustado por relieve, ver
    calibracion/regiones_relieve.md); si no está, la asignación por departamento."""
    if "por_relieve" not in _CACHE:
        try:
            _CACHE["por_relieve"] = json.loads((BASE / "datos" / "regiones_municipios.json").read_text(encoding="utf-8"))
        except Exception:
            _CACHE["por_relieve"] = {}
    cod_mun = str(cod_mun).zfill(5)
    return _CACHE["por_relieve"].get(cod_mun) or _region_base(cod_mun)


def _territorio():
    if "t" not in _CACHE:
        cfg = yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
        act = ma.departamentos_activos(cfg)
        malla = ma.construir_malla(cfg, sorted(act))
        t = an.Territorio(BASE / cfg["territorio"]["geojson"], malla, cfg["territorio"]["campos"])
        ids = {k: i + 1 for i, (k, _, _) in enumerate(REGIONES)}
        reg_mun = np.array([0] + [ids[region_de(c)] for c in t.mun_codigo[1:]], np.int8)
        nom_dep = {c: act[c]["nombre"] for c in act}
        _CACHE.update(t=t, malla=malla, reg=reg_mun[t.etiquetas], reg_mun=reg_mun, ids=ids, nom_dep=nom_dep)
    return _CACHE


def _campos(malla):
    """Radar (clases 0-3), lluvia satélite (mm/h), temperatura de nubes (°C) y rayos (lon, lat)."""
    import fuente_rainalarm
    import indice_nubes as inu
    out = {"radar": None, "sat": None, "bt": None, "rayos": np.zeros((0, 4))}
    try:
        out["radar"] = fuente_rainalarm.lluvia_actual(malla)
    except Exception as e:
        log.warning("radar: %s", e)
    try:
        out["sat"] = np.nan_to_num(inu.leer_campo(inu.claves("ABI-L2-RRQPEF", "", 1)[-1], "RRQPE", malla), nan=0)
    except Exception as e:
        log.warning("satélite lluvia: %s", e)
    try:
        k13 = inu.claves("ABI-L2-CMIPF", "M6C13", 1)
        out["bt"] = inu.leer_campo(k13[-1], "CMI", malla) - 273.15
        # imagen de hace ~30 min: para saber si las nubes de lluvia crecen o se disipan
        antes = min(k13, key=lambda k: abs((inu._inicio(k13[-1]) - inu._inicio(k)).total_seconds() - 1800))
        if antes != k13[-1]:
            out["bt0"] = inu.leer_campo(antes, "CMI", malla) - 273.15
    except Exception as e:
        log.warning("satélite nubes: %s", e)
    try:
        import rayos_glm
        out["rayos"] = rayos_glm.descargar_rayos(15)
    except Exception as e:
        log.warning("rayos: %s", e)
    return out


def calcular(res: list[dict]):
    """Estadísticas por región. `res` = departamentos del índice de nubes (indice_nubes.calcular)."""
    c = _territorio()
    t, malla, reg, ids = c["t"], c["malla"], c["reg"], c["ids"]
    f = _campos(malla)
    km2 = t.area_px
    est = {}
    for k, nombre, emo in REGIONES:
        m = reg == ids[k]
        e = {"nombre": nombre, "emo": emo, "radar_km2": 0, "radar_fuerte_km2": 0, "muns": [], "sat_km2": 0,
             "sat_fuerte_km2": 0, "frio40": 0, "frio60": 0, "rayos": 0}
        if f["radar"] is not None:
            ll = m & (f["radar"] >= 1)
            e["radar_km2"] = float(km2[ll].sum())
            e["radar_fuerte_km2"] = float(km2[m & (f["radar"] >= 3)].sum())
            a = np.bincount(t.etiquetas[ll], weights=km2[ll] * f["radar"][ll], minlength=t.n_mun + 1)
            # sin San Andrés y Providencia (zona insular)
            e["muns"] = [(t.mun_nombre[i], c["nom_dep"].get(str(t.mun_codigo[i]).zfill(5)[:2], ""))
                         for i in np.argsort(-a) if a[i] >= 8 and i > 0
                         and str(t.mun_codigo[i]).zfill(5)[:2] != "88"]
        if f["sat"] is not None:
            e["sat_km2"] = float(km2[m & (f["sat"] >= 1)].sum())
            e["sat_fuerte_km2"] = float(km2[m & (f["sat"] >= 10)].sum())
        if f["bt"] is not None:
            e["frio40"] = float(km2[m & (f["bt"] < -40)].sum())
            e["frio60"] = float(km2[m & (f["bt"] < -60)].sum())
        if f.get("bt0") is not None:
            e["frio40_0"] = float(km2[m & (f["bt0"] < -40)].sum())
        est[k] = e
    if len(f["rayos"]):
        px, py = malla.a_pixel(f["rayos"][:, 0], f["rayos"][:, 1])
        ok = (px >= 0) & (px < malla.ancho) & (py >= 0) & (py < malla.alto)
        r = reg[py[ok].astype(int), px[ok].astype(int)]
        lab = t.etiquetas[py[ok].astype(int), px[ok].astype(int)]
        for k, _, _ in REGIONES:
            est[k]["rayos"] = int((r == ids[k]).sum())
            cnt = Counter()
            for i in lab[r == ids[k]]:
                cod = str(t.mun_codigo[i]).zfill(5)[:2] if i > 0 else ""
                if cod and cod != "88":
                    cnt[c["nom_dep"].get(cod, "")] += 1
            est[k]["rayos_dep"] = [d for d, nn in cnt.most_common() if d and nn >= 3]
    # departamentos del índice: probabilidad y movimiento
    for d in res:
        k = POR_DEPTO.get(d["codigo"], "andina")
        if d["codigo"] in ("76", "19", "52") and (d.get("municipios") or []):
            pass
        e = est[k]
        e.setdefault("alta", []); e.setdefault("media", []); e.setdefault("mov", []); e.setdefault("viento", [])
        if d["probabilidad"] == "ALTA":
            e["alta"].append(d["departamento"])
        elif d["probabilidad"] == "MEDIA":
            e["media"].append(d["departamento"])
        if d.get("movimiento"):
            e["mov"].append(d["movimiento"]["hacia"])
            if d["movimiento"].get("grados") is not None and d["movimiento"].get("vel_kmh", 0) >= 5:
                e.setdefault("mov_grados", []).append(d["movimiento"]["grados"])
        v = d.get("viento") or {}
        if v.get("alto_dir") is not None and (v.get("alto_kmh") or 0) >= 3:
            e["viento"].append((v["alto_dir"] + 180) % 360)
        if v.get("viento_dir") is not None and v.get("viento_kmh") is not None:
            e.setdefault("sup", []).append((v["viento_dir"], v["viento_kmh"]))
    _CACHE["ultimo_est"] = est
    return est


def tendencia(e):
    """'crece', 'disipa', 'igual' o None según el área de nubes frías (< -40 °C) ahora y hace 30 min."""
    a, a0 = e.get("frio40", 0), e.get("frio40_0")
    if a0 is None or max(a, a0) < 1500:
        return None
    if a0 < 500:
        return "crece"
    r = a / a0
    return "crece" if r >= 1.2 else "disipa" if r <= 0.8 else "igual"


def viento_superficie(e):
    """(rumbo de donde viene, km/h) promediando vectores de los departamentos de la región."""
    s = e.get("sup") or []
    if not s:
        return None
    u = sum(k * np.sin(np.radians(d)) for d, k in s) / len(s)
    v = sum(k * np.cos(np.radians(d)) for d, k in s) / len(s)
    vel = float(np.mean([k for _, k in s]))
    g = np.degrees(np.arctan2(u, v)) % 360
    return RUMBOS[int((g + 22.5) // 45) % 8], vel


def flechas():
    """Hacia dónde van las nubes en cada región (grados), del último cálculo; para el mapa nacional."""
    out = {}
    for k, e in (_CACHE.get("ultimo_est") or {}).items():
        if e.get("frio40", 0) < 1000 and not e.get("muns"):
            continue      # sin nubes ni lluvia: no se dibuja flecha
        if e.get("mov_grados"):
            out[k] = _grados_medios(e["mov_grados"])
        elif e.get("viento"):
            out[k] = _grados_medios(e["viento"])
    return out


def _grados_medios(gs):
    s = sum(np.sin(np.radians(g)) for g in gs); c = sum(np.cos(np.radians(g)) for g in gs)
    return float(np.degrees(np.arctan2(s, c)) % 360)


def _rumbo_medio(grados):
    s = sum(np.sin(np.radians(g)) for g in grados); c = sum(np.cos(np.radians(g)) for g in grados)
    g = np.degrees(np.arctan2(s, c)) % 360
    return RUMBOS[int((g + 22.5) // 45) % 8]


def _lista(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1] if xs else ""


def nivel_lluvia(e):
    n = len(e["muns"])
    if n >= 10 or e["sat_km2"] >= 3000:
        return "mucha"
    if n or e["sat_km2"] >= 300:
        return "poca"
    return "nada"


def resumen(est) -> str:
    mucha = [est[k]["nombre"] for k, _, _ in REGIONES if nivel_lluvia(est[k]) == "mucha"]
    poca = [est[k]["nombre"] for k, _, _ in REGIONES if nivel_lluvia(est[k]) == "poca"]
    seca = [est[k]["nombre"] for k, _, _ in REGIONES if nivel_lluvia(est[k]) == "nada"]
    L = []
    if mucha:
        L.append(f"llueve sobre todo en la región {_lista(mucha)}" if len(mucha) == 1 else f"llueve sobre todo en las regiones {_lista(mucha)}")
    if poca:
        L.append(f"hay lluvias aisladas en {_lista(poca)}")
    if seca:
        L.append(f"en {_lista(seca)} no está lloviendo")
    if not L:
        return ""
    t = "; ".join(L) + "."
    return "📌 *En resumen:* " + t[0].upper() + t[1:]


def parrafo(e) -> str:
    """Párrafo en lenguaje sencillo, para productores."""
    partes = []
    muns = e["muns"]
    n = len(muns)
    if n:
        deps = list(dict.fromkeys(d for _, d in muns if d))
        if n <= 4:
            # pocos: solo los nombres
            nombres = [f"{m} ({d})" if m.lower() in ("colombia",) or m == d else m for m, d in muns]
            txt = f"Está lloviendo en {_lista(nombres)}"
        else:
            # muchos: solo la cantidad, por departamento
            txt = (f"Está lloviendo en {n} municipios"
                   + (f" de {_lista(deps[:4])}" + (" y otros departamentos" if len(deps) > 4 else "") if deps else ""))
        if e["radar_fuerte_km2"] >= 20:
            txt += ". En algunos puntos llueve fuerte"
        partes.append(txt)
    elif e["sat_km2"] >= 3000:
        partes.append("Hay lluvias en buena parte de la región" + (" y en algunos puntos llueve fuerte" if e["sat_fuerte_km2"] >= 100 else ""))
    elif e["sat_km2"] >= 300:
        partes.append("Hay lluvias en algunos sectores" + (" y en algunos puntos llueve fuerte" if e["sat_fuerte_km2"] >= 100 else ""))
    else:
        partes.append("Por ahora no está lloviendo")
    if e["frio60"] >= 1500:
        cielo = "Hay nubes de tormenta"
    elif e["frio40"] >= 5000:
        cielo = "El cielo está muy nublado"
    elif e["frio40"] >= 1000:
        cielo = "Está nublado en algunas partes"
    else:
        cielo = "El cielo está casi despejado"
    if e.get("mov_grados"):
        cielo += f"; las nubes van hacia el {_rumbo_medio(e['mov_grados'])}"
    elif e.get("mov"):
        cielo += f"; las nubes van hacia el {Counter(e['mov']).most_common(1)[0][0]}"
    elif e.get("viento") and (e["frio40"] >= 1000 or n):
        cielo += f"; las nubes van hacia el {_rumbo_medio(e['viento'])}"
    partes.append(cielo)
    txt = ". ".join(partes) + "."
    tnd = tendencia(e)
    if tnd == "crece":
        txt += "\n📈 *La lluvia se está formando:* las nubes de tormenta están creciendo."
    elif tnd == "disipa":
        txt += "\n📉 *La lluvia se está disipando:* las nubes de tormenta se están reduciendo."
    elif tnd == "igual":
        txt += "\n➡️ Las nubes de lluvia se mantienen: ni crecen ni se disipan."
    vs = viento_superficie(e)
    if vs:
        txt += "\n💨 Viento cerca del suelo: calmado." if vs[1] < 3 else f"\n💨 Viento cerca del suelo: viene del {vs[0]}, unos {vs[1]:.0f} km/h."
    if e["rayos"] >= 5 and e.get("rayos_dep"):
        txt += f"\n⚡ Presencia de rayos en {_lista(e['rayos_dep'][:4])}."
    prob = []
    if e.get("alta"):
        prob.append(f"⛈️ *Puede llover fuerte* en las próximas 2 horas en: {_lista(e['alta'])}.")
    if e.get("media"):
        prob.append(f"🌦️ *Podría llover fuerte* en: {_lista(e['media'])}.")
    return f"{e['emo']} *{e['nombre']}*\n{txt}" + ("\n" + "\n".join(prob) if prob else "")


def texto(est, hora_txt: str, prueba=False) -> str:
    import reporte_nubes as rn
    L = (["🧪 *MENSAJE DE PRUEBA*"] if prueba else [])
    L += ["🌎 *¿CÓMO ESTÁ LLOVIENDO EN EL PAÍS? · FENALCE*",
          f"🕘 Así está el tiempo a las {hora_txt}", ""]
    r_ = resumen(est)
    if r_:
        L += [r_, ""]
    for k, _, _ in REGIONES:
        L += [parrafo(est[k]), ""]
    L += ["🗺️ En el mapa: los colores muestran dónde llueve y los ⚡ dónde caen rayos.", "",
          f"🌐 Más información en el *Portal Agroclimático FENALCE*: {rn.PORTAL}",
          f"📢 Avisos oficiales del IDEAM: {rn.OSPA}", "", rn.FIRMA]
    return "\n".join(L)


def _hora_txt():
    a = datetime.now(ZONA)
    return f"{a.hour % 12 or 12}:{a.minute:02d} {'a. m.' if a.hour < 12 else 'p. m.'}"


def generar(res=None, prueba=False):
    if res is None:
        res = json.loads((BASE / "salida" / "indice_nubes.json").read_text(encoding="utf-8"))["departamentos"]
    return texto(calcular(res), _hora_txt(), prueba)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    print(generar())
