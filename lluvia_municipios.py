"""
lluvia_municipios.py — Municipios donde está lloviendo ahora (radar Rain-Alarm + IDEAM), priorizando
los departamentos donde FENALCE tiene mesas técnicas y sus vecinos.

   python lluvia_municipios.py            → imprime el mensaje
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml

import analisis as an
import motor_alertas as ma

BASE = Path(__file__).resolve().parent
ZONA = timezone(timedelta(hours=-5))
# departamentos con mesa técnica y sus vecinos (códigos DANE)
ZONAS = {"73": "Tolima", "68": "Santander", "23": "Córdoba", "13": "Bolívar", "86": "Putumayo", "25": "Cundinamarca"}
VECINOS = ["41", "17", "15", "54", "05", "70", "19", "52", "18", "50", "20", "47", "11", "63", "66", "76"]
EMO = {1: "🔵", 2: "🟢", 3: "🟠"}
NOMBRE_INT = {1: "débil", 2: "moderada", 3: "fuerte"}
_CACHE = {}


def calcular(min_km2=8.0):
    cfg = yaml.safe_load(open(BASE / "config.yaml", encoding="utf-8"))
    act = ma.departamentos_activos(cfg); cod = sorted(act)
    if "t" not in _CACHE:
        malla = ma.construir_malla(cfg, cod)
        t = an.Territorio(BASE / cfg["territorio"]["geojson"], malla, cfg["territorio"]["campos"])
        dep_de_mun = np.zeros(t.n_mun + 1, int)
        for m in range(1, t.n_mun + 1):
            d = t.dep_raster[t.etiquetas == m]
            dep_de_mun[m] = np.bincount(d).argmax() if len(d) else 0
        _CACHE.update(t=t, malla=malla, dep_de_mun=dep_de_mun)
    t, malla, dep_de_mun = _CACHE["t"], _CACHE["malla"], _CACHE["dep_de_mun"]
    import fuente_rainalarm
    ra = fuente_rainalarm.lluvia_actual(malla)
    if ra is None:
        return None
    area = np.bincount(t.etiquetas[ra >= 1], weights=t.area_px[ra >= 1], minlength=t.n_mun + 1)
    inten = np.zeros(t.n_mun + 1, int)
    np.maximum.at(inten, t.etiquetas[ra >= 1], ra[ra >= 1])
    cod_de_indice = {v: k for k, v in t.dep_indice.items()}
    por_dep = {}
    for m in np.nonzero(area >= min_km2)[0]:
        if m == 0:
            continue
        c = cod_de_indice.get(dep_de_mun[m])
        if c is None:
            continue
        por_dep.setdefault(c, []).append((t.mun_nombre[m], int(inten[m]), float(area[m])))
    for c in por_dep:
        por_dep[c].sort(key=lambda x: (-x[1], -x[2]))
    return {c: (act[c]["nombre"], v) for c, v in por_dep.items() if c in act}


def texto(por_dep, prueba=False):
    ahora = datetime.now(ZONA)
    h = f"{ahora.hour % 12 or 12}:{ahora.minute:02d} {'a. m.' if ahora.hour < 12 else 'p. m.'}"
    L = (["🧪 *MENSAJE DE PRUEBA*"] if prueba else []) + ["☔ *DÓNDE ESTÁ LLOVIENDO AHORA · FENALCE*",
                                                         f"🕘 Radar de las {h} · 🔵 débil · 🟢 moderada · 🟠 fuerte", ""]

    def linea(c, n=12):
        nombre, muns = por_dep[c]
        txt = ", ".join(f"{EMO[i]} {m}" for m, i, _ in muns[:n])
        return f"*{nombre}*: {txt}" + (f" y {len(muns) - n} más" if len(muns) > n else "")
    nuestras = [c for c in ZONAS if c in por_dep]
    L.append("📍 *En nuestras zonas*")
    L += [linea(c) for c in nuestras] or ["Sin lluvia en este momento en Tolima, Santander, Córdoba, Bolívar, Putumayo y Cundinamarca."]
    secas = [ZONAS[c] for c in ZONAS if c not in por_dep]
    if nuestras and secas:
        L.append(f"Sin lluvia: {', '.join(secas)}.")
    vec = [c for c in VECINOS if c in por_dep]
    if vec:
        L += ["", "🧭 *Alrededores*"] + [linea(c, 6) for c in vec]
    resto = [c for c in por_dep if c not in ZONAS and c not in VECINOS]
    if resto:
        L += ["", "🗺️ *Resto del país*: " + "; ".join(f"{por_dep[c][0]} ({len(por_dep[c][1])} mun.)" for c in resto)]
    L += ["", "_Lluvia vista por el radar en este momento; donde el radar no alcanza (Pacífico, Orinoquía, Amazonía) puede estar lloviendo sin aparecer aquí._",
          "*FENALCE · Equipo de Agroclimatología*", "Juan Gómez · Jhon Valencia"]
    return "\n".join(L)


def departamento_foco(por_dep):
    """Departamento para el clip: el de nuestras zonas con más lluvia; si no hay, el de más lluvia del país."""
    sumar = lambda c: sum(a * i for _, i, a in por_dep[c][1])
    nuestras = [c for c in ZONAS if c in por_dep]
    pool = nuestras or list(por_dep)
    return max(pool, key=sumar) if pool else None


def texto_condiciones(c, por_dep, r, prueba=False):
    """Condiciones actuales de un departamento: dónde llueve, probabilidad, nubes, viento y rayos."""
    import reporte_nubes as rn
    ahora = datetime.now(ZONA)
    h = f"{ahora.hour % 12 or 12}:{ahora.minute:02d} {'a. m.' if ahora.hour < 12 else 'p. m.'}"
    nombre, muns = por_dep.get(c, (r.get("departamento", ""), []))
    prob = {"ALTA": "⛈️ Probabilidad de lluvia fuerte en las próximas 2 horas: *alta*",
            "MEDIA": "🌦️ Probabilidad de lluvia fuerte en las próximas 2 horas: *media*"}.get(
        r.get("probabilidad"), "🌤️ Probabilidad de lluvia fuerte en las próximas 2 horas: *baja*")
    L = (["🧪 *MENSAJE DE PRUEBA*"] if prueba else []) + [f"📍 *{nombre.upper()} · CONDICIONES AHORA*",
                                                         f"🕘 Radar de las {h}", ""]
    L.append("☔ *Lloviendo en:* " + (", ".join(f"{EMO[i]} {m}" for m, i, _ in muns[:15])
                                      + (f" y {len(muns) - 15} municipios más" if len(muns) > 15 else "")
                                      if muns else "el radar no muestra lluvia en este momento."))
    L.append(prob)
    if r.get("frio40") is not None:
        cuerpo = rn.texto_departamento(r, datetime.now(ZONA).strftime("%Y-%m-%d %H:%M")).split("\n")
        L += [x for x in cuerpo if x[:2] in ("☁️", "🧭", "💨", "⚡", "🛑") or x.startswith(("☁", "🧭", "💨", "⚡", "🛑"))]
    L += ["", rn.FIRMA]
    return "\n".join(L)


def enviar(prueba=False, tema=None):
    """Mensaje de municipios con lluvia + clip del departamento con más lluvia en nuestras zonas."""
    import reporte_nubes as rn
    por_dep = calcular()
    if por_dep is None:
        return
    import json
    rn._ntfy(("🧪 " if prueba else "") + "☔ Dónde está lloviendo ahora", texto(por_dep, prueba), 3, tema=tema)
    # 1) clip de todo el país
    rn._enviar_clip({"codigo": "CO", "departamento": "Colombia", "llueve_en": [], "probabilidad": None}, prueba, [tema])
    # 2) un mensaje y un clip por cada departamento de nuestras zonas donde está lloviendo
    try:
        indice = {x["codigo"]: x for x in json.loads((BASE / "salida" / "indice_nubes.json").read_text(encoding="utf-8"))["departamentos"]}
    except Exception:
        indice = {}
    for c in [c for c in ZONAS if c in por_dep]:
        nombre, muns = por_dep[c]
        r = dict(indice.get(c, {}), codigo=c, departamento=nombre, llueve_en=[m for m, _, _ in muns[:4]])
        rn._ntfy(("🧪 " if prueba else "") + f"📍 {nombre}: condiciones ahora", texto_condiciones(c, por_dep, r, prueba), 3, tema=tema)
        rn._enviar_clip(dict(r, probabilidad=r.get("probabilidad") if r.get("probabilidad") != "BAJA" else None), prueba, [tema])


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    print(texto(calcular()))
