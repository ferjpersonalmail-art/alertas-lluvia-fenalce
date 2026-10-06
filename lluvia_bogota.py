"""
lluvia_bogota.py - Condiciones de lluvia solo para Bogotá (zona urbana) y la Sabana.

   python lluvia_bogota.py            imprime el mensaje
   python lluvia_bogota.py enviar     envía mensaje + clip (tema de Bogotá)
"""
from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

BASE = Path(__file__).resolve().parent
ZONA = timezone(timedelta(hours=-5))
TEMA_BOGOTA = "fenalce-bogota-2ifz77"
CENTRO = (-74.09, 4.65)              # lon, lat
CAJA = (-74.32, 4.45, -73.95, 5.05)  # zona urbana y Sabana (sin Sumapaz)
SABANA = ["25754", "25175", "25214", "25286", "25473", "25430", "25269", "25377", "25126", "25899",
          "25740", "25817", "25758", "25799", "25785", "25295", "25099", "25769", "25260"]
NOMBRE_INT = {1: "débil", 2: "moderada", 3: "fuerte"}
log = logging.getLogger(__name__)


def _hora(d):
    return f"{d.hour % 12 or 12}:{d.minute:02d} {'a. m.' if d.hour < 12 else 'p. m.'}"


def calcular():
    import fuente_rainalarm
    import lluvia_municipios as lm
    lm.calcular()
    t, malla = lm._CACHE["t"], lm._CACHE["malla"]
    ra = fuente_rainalarm.lluvia_actual(malla)
    out = {"bogota": None, "sabana": [], "rayos": 0, "rayos_cerca": 0}
    if ra is not None:
        # solo la parte urbana / norte de Bogotá (filas dentro de la caja)
        xa, ya = malla.a_pixel(np.array([CAJA[0], CAJA[2]]), np.array([CAJA[3], CAJA[1]]))
        caja = np.zeros(ra.shape, bool)
        caja[max(0, int(ya[0])):int(ya[1]) + 1, max(0, int(xa[0])):int(xa[1]) + 1] = True
        cod = np.array(["" if i == 0 else str(t.mun_codigo[i]).zfill(5) for i in range(t.n_mun + 1)])
        lab_cod = cod[t.etiquetas]
        llueve = (ra >= 1) & caja
        bog = llueve & (lab_cod == "11001")
        km = float(t.area_px[bog].sum())
        if km >= 3:
            out["bogota"] = {"km2": km, "int": int(ra[bog].max())}
        for c in SABANA:
            s = llueve & (lab_cod == c)
            a = float(t.area_px[s].sum())
            if a >= 3:
                i = int(np.argmax(cod == c))
                out["sabana"].append((t.mun_nombre[i], int(ra[s].max()), a))
        out["sabana"].sort(key=lambda x: (-x[1], -x[2]))
    try:
        import rayos_glm
        r = rayos_glm.descargar_rayos(30)
        if len(r):
            dx = (r[:, 0] - CENTRO[0]) * 111 * np.cos(np.radians(CENTRO[1]))
            dy = (r[:, 1] - CENTRO[1]) * 111
            d = np.hypot(dx, dy)
            out["rayos_cerca"] = int((d <= 15).sum())
            out["rayos"] = int((d <= 40).sum())
    except Exception as e:
        log.warning("rayos: %s", e)
    try:
        dep = {x["codigo"]: x for x in json.loads((BASE / "salida" / "indice_nubes.json").read_text(encoding="utf-8"))["departamentos"]}
        out["indice"] = dep.get("25", {})
    except Exception:
        out["indice"] = {}
    return out


def texto(e, prueba=False):
    import reporte_nubes as rn
    L = (["🧪 *MENSAJE DE PRUEBA*"] if prueba else []) + ["🏙️ *BOGOTÁ Y LA SABANA · ¿ESTÁ LLOVIENDO?*",
                                                        f"🕘 Así está el tiempo a las {_hora(datetime.now(ZONA))}", ""]
    b = e["bogota"]
    if b:
        L.append(f"🌧️ *En Bogotá está lloviendo* ({NOMBRE_INT.get(b['int'], 'débil')}).")
    else:
        L.append("🌤️ *En Bogotá no está lloviendo* en este momento.")
    s = e["sabana"]
    if s:
        if len(s) <= 4:
            L.append(f"🌧️ En la Sabana llueve en {rn_lista([m for m, _, _ in s])}.")
        else:
            L.append(f"🌧️ En la Sabana llueve en {len(s)} municipios.")
    else:
        L.append("En la Sabana tampoco llueve por ahora." if not b else "En la Sabana no está lloviendo.")
    if e["rayos_cerca"]:
        L.append("⚡ *Presencia de rayos sobre Bogotá.*")
    elif e["rayos"] >= 3:
        L.append("⚡ Presencia de rayos cerca de Bogotá.")
    ind = e.get("indice") or {}
    mov, v = ind.get("movimiento"), ind.get("viento") or {}
    rumbos = ["norte", "nororiente", "oriente", "suroriente", "sur", "suroccidente", "occidente", "noroccidente"]
    if mov and mov.get("hacia"):
        L.append(f"☁️ Las nubes van hacia el {mov['hacia']}.")
    elif v.get("alto_dir") is not None and (v.get("alto_kmh") or 0) >= 3:
        L.append(f"☁️ Las nubes van hacia el {rumbos[int((((v['alto_dir'] + 180) % 360) + 22.5) // 45) % 8]}.")
    crec = ind.get("crec")
    if crec is not None and (ind.get("frio40") or 0) >= 100:
        if crec >= 1.3:
            L.append("📈 *La lluvia se está formando:* las nubes de tormenta están creciendo.")
        elif crec <= 0.7:
            L.append("📉 *La lluvia se está disipando:* las nubes de tormenta se están reduciendo.")
        else:
            L.append("➡️ Las nubes de lluvia se mantienen: ni crecen ni se disipan.")
    if v.get("viento_dir") is not None and v.get("viento_kmh") is not None:
        L.append("💨 Viento cerca del suelo: calmado." if v["viento_kmh"] < 3 else
                 f"💨 Viento cerca del suelo: viene del {rumbos[int(((v['viento_dir'] % 360) + 22.5) // 45) % 8]}, unos {v['viento_kmh']:.0f} km/h.")
    p = ind.get("probabilidad")
    if p == "ALTA":
        L.append("⛈️ *Puede llover fuerte* en las próximas 2 horas.")
    elif p == "MEDIA":
        L.append("🌦️ *Podría llover fuerte* en las próximas 2 horas.")
    L += ["", rn.FIRMA]
    return "\n".join(L)


def rn_lista(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1]


def enviar(prueba=False):
    import reporte_nubes as rn
    e = calcular()
    rn._ntfy(("🧪 " if prueba else "") + "🏙️ Bogotá: condiciones ahora", texto(e, prueba), 3, tema=TEMA_BOGOTA)
    ind = e.get("indice") or {}
    r = {"codigo": "BOG", "departamento": "Bogotá", "llueve_en": [], "movimiento": ind.get("movimiento"),
         "viento": ind.get("viento"), "probabilidad": ind.get("probabilidad") if ind.get("probabilidad") != "BAJA" else None}
    rn._enviar_clip(r, prueba, [TEMA_BOGOTA])


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if "enviar" in sys.argv:
        enviar("prueba" in sys.argv)
    else:
        print(texto(calcular()))
