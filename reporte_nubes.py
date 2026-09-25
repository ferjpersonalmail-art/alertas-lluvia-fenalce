"""
reporte_nubes.py — Reporte de probabilidad de lluvia fuerte por departamento, en lenguaje sencillo.

  • Reportes programados: 1, 5 y 7 a. m. y 1, 5 y 7 p. m. (hora Colombia).
  • Aviso inmediato, a cualquier hora, cuando un departamento sube a probabilidad ALTA (rojo).
  • Se envía al celular por ntfy; cada departamento trae el botón para reenviarlo por WhatsApp.

Lo llama el motor en cada ciclo (python reporte_nubes.py también funciona solo).
   python reporte_nubes.py --forzar     → manda el reporte ya, sin esperar la hora
   python reporte_nubes.py --prueba     → igual, marcado como PRUEBA
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("reporte")
BASE = Path(__file__).resolve().parent
ZONA = timezone(timedelta(hours=-5))
HORAS_REPORTE = (1, 5, 7, 13, 17, 19)
ESTADO = BASE / "estado" / "reporte_nubes.json"
OSPA = "https://www.ideam.gov.co/nuestra-entidad/servicio-de-pronosticos-y-alertas"   # pronosticosyalertas.gov.co tiene el certificado vencido
PORTAL = "https://agroclima-fenalce-portal.vercel.app/"
QR_URL = "https://raw.githubusercontent.com/ferjpersonalmail-art/alertas-lluvia-fenalce/main/datos/qr_portal.png"
COLOR = {"ALTA": ("🔴", "ROJA"), "MEDIA": ("🟡", "AMARILLA"), "BAJA": ("🟢", "VERDE")}
ORDEN = {"BAJA": 0, "MEDIA": 1, "ALTA": 2}
DIR_VIENTO = ["norte", "nororiente", "oriente", "suroriente", "sur", "suroccidente", "occidente", "noroccidente"]


def _rumbo(grados):
    return DIR_VIENTO[int(((grados % 360) + 22.5) // 45) % 8]


def _lista(xs):
    xs = [x for x in xs if x]
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1] if xs else ""


def _hora(dt):
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'a. m.' if dt.hour < 12 else 'p. m.'}"


# --------------------------------------------------------------------------- texto
def texto_departamento(r: dict, hora_sat: str, tipo: str = "reporte", prueba: bool = False) -> str:
    emo, nombre = COLOR[r["probabilidad"]]
    dt = datetime.strptime(hora_sat, "%Y-%m-%d %H:%M")
    L = []
    if prueba:
        L.append("🧪 *MENSAJE DE PRUEBA* (no reenviar)")
    if tipo == "sube":
        L.append(f"⬆️ *AVISO: SUBE A ALERTA ROJA · {r['departamento'].upper()}*")
    else:
        L.append(f"{emo} *ALERTA {nombre} POR LLUVIAS FUERTES · {r['departamento'].upper()}*")
    L.append(f"🕘 Imagen de satélite de las {_hora(dt)} · válido para las próximas 2 horas")
    L.append("")
    frase = {"ALTA": "Es *muy probable* que se presenten lluvias fuertes",
             "MEDIA": "*Podrían* presentarse lluvias fuertes", "BAJA": "Baja posibilidad de lluvias fuertes"}
    zona = _lista(r.get("municipios") or [])
    L.append(f"🌧️ {frase[r['probabilidad']]}" + (f" en *{zona}* y alrededores." if zona else " en el departamento."))

    # nubes, en palabras
    if r["frio60"] >= 30:
        nubes = "nubes de tormenta muy desarrolladas"
    elif r["frio40"] >= 100:
        nubes = "nubes cargadas de agua"
    else:
        nubes = "nubosidad dispersa"
    tend = ""
    if r.get("crec") is not None and r["frio40"] >= 100:
        tend = " que *están creciendo*" if r["crec"] >= 1.3 else " que *se están debilitando*" if r["crec"] <= 0.7 else " que se mantienen"
    L.append(f"☁️ Hay {nubes}{tend}.")

    mov = r.get("movimiento")
    v = r.get("viento") or {}
    if mov and mov["vel_kmh"] >= 5:
        L.append(f"🧭 Las nubes se mueven *hacia el {mov['hacia']}* (unos {mov['vel_kmh']:.0f} km/h).")
    elif mov:
        L.append("🧭 Las nubes están casi quietas sobre la zona.")
    elif v.get("alto_dir") is not None and (v.get("alto_kmh") or 0) >= 5:
        L.append(f"🧭 Los vientos de altura empujan las nubes *hacia el {_rumbo(v['alto_dir'] + 180)}*.")
    if v.get("viento_dir") is not None and v.get("viento_kmh") is not None:
        if v["viento_kmh"] < 3:
            L.append("💨 Viento en superficie: en calma.")
        else:
            L.append(f"💨 Viento en superficie: sopla del {_rumbo(v['viento_dir'])}, unos {v['viento_kmh']:.0f} km/h.")

    if r["rayos"] >= 5:
        L.append(f"⚡ *Sí hay actividad eléctrica:* se detectaron rayos en los últimos 15 minutos.")
    elif r["rayos"] > 0:
        L.append("⚡ Actividad eléctrica aislada (pocos rayos).")
    else:
        L.append("⚡ Sin actividad eléctrica por ahora.")

    L.append("")
    L.append("*Recomendaciones:*")
    if r["probabilidad"] == "ALTA":
        L += ["• Se sugiere suspender aplicaciones de agroquímicos y fertilizantes en la zona.",
              "• Revisar drenajes y evitar labores en lotes que se encharcan o cerca de quebradas.",
              "• Si hay rayos, no permanecer en campo abierto ni bajo árboles aislados."]
    else:
        L += ["• Se sugiere programar las labores de campo fuera de la franja de lluvia.",
              "• Estar atentos a la evolución del tiempo en las próximas horas."]
    L.append("")
    L.append(f"🌐 Siga el radar, las estaciones y el clima en nuestro *Portal Agroclimático FENALCE* (versión en desarrollo): {PORTAL}")
    L.append(f"📢 Consulte también los avisos oficiales de la *Oficina del Servicio de Pronósticos y Alertas (OSPA) del IDEAM*: {OSPA}")
    L.append("_FENALCE · Equipo de Agroclimatología. Estimación con imágenes de satélite; puede haber diferencias con lo que ocurra en cada finca._")
    return "\n".join(L)


# --------------------------------------------------------------------------- envío
def _ntfy(titulo, texto, prioridad=3, tags=None, qr=False):
    tema = os.environ.get("NTFY_TOPIC") or "fenalce-lluvia-2ifz77fnqg"
    wa = "whatsapp://send?text=" + urllib.parse.quote(texto)
    cuerpo = {"topic": tema, "title": titulo, "message": texto, "priority": prioridad, "tags": tags or [],
              "click": wa, "actions": [{"action": "view", "label": "Enviar por WhatsApp", "url": wa},
                                       {"action": "view", "label": "Abrir portal", "url": PORTAL}]}
    if qr:   # imagen del código QR del portal, para compartirla en el grupo
        cuerpo["attach"] = QR_URL
        cuerpo["filename"] = "QR_portal_agroclimatico_FENALCE.png"
    if len(json.dumps(cuerpo).encode()) > 8000:   # ntfy limita el tamaño; el botón lleva el texto completo
        cuerpo["message"] = texto[:1500] + "…"
    h = {"Content-Type": "application/json"}
    if os.environ.get("NTFY_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
    urllib.request.urlopen(urllib.request.Request("https://ntfy.sh", data=json.dumps(cuerpo).encode(), headers=h), timeout=20)


def _resumen(hora_sat, res, prueba):
    dt = datetime.strptime(hora_sat, "%Y-%m-%d %H:%M")
    rojas = [r["departamento"] for r in res if r["probabilidad"] == "ALTA"]
    amar = [r["departamento"] for r in res if r["probabilidad"] == "MEDIA"]
    L = (["🧪 PRUEBA"] if prueba else []) + [f"📋 Reporte de lluvias · {_hora(dt)}"]
    L.append(f"🔴 Roja: {_lista(rojas) or 'ninguno'}")
    L.append(f"🟡 Amarilla: {_lista(amar) or 'ninguno'}")
    L.append("🟢 Resto del país: sin lluvias fuertes previstas.")
    L.append(f"🌐 Portal: {PORTAL} (código QR adjunto)")
    return "\n".join(L)


# --------------------------------------------------------------------------- lógica
def ejecutar(forzar=False, prueba=False, ahora=None):
    ahora = (ahora or datetime.now(timezone.utc)).astimezone(ZONA)
    est = json.loads(ESTADO.read_text(encoding="utf-8")) if ESTADO.exists() else {}
    franja = f"{ahora:%Y-%m-%d}-{ahora.hour:02d}" if ahora.hour in HORAS_REPORTE and ahora.minute < 50 else None
    toca_reporte = forzar or (franja is not None and est.get("ultima_franja") != franja)
    ult_calc = est.get("ultimo_calculo")
    if not toca_reporte and ult_calc and (ahora - datetime.fromisoformat(ult_calc)).total_seconds() < 9 * 60:
        return 0   # el índice se recalcula cada ~10 min para vigilar subidas a rojo

    import indice_nubes
    hora_sat, res = indice_nubes.calcular()
    previos = est.get("niveles", {})
    enviados = 0

    # 1) subidas a ROJA, a cualquier hora (fuera de los reportes programados)
    rojo_desde = est.get("rojo_desde", {})
    suben = []
    for r in res:
        antes = previos.get(r["codigo"], "BAJA")
        ult = rojo_desde.get(r["codigo"])
        reciente = ult and (ahora - datetime.fromisoformat(ult)).total_seconds() < 3 * 3600
        if r["probabilidad"] == "ALTA" and ORDEN[antes] < ORDEN["ALTA"] and not reciente:
            suben.append(r)
    for r in res:
        if r["probabilidad"] == "ALTA":
            rojo_desde.setdefault(r["codigo"], ahora.isoformat())
        elif r["codigo"] in rojo_desde and (ahora - datetime.fromisoformat(rojo_desde[r["codigo"]])).total_seconds() >= 3 * 3600:
            rojo_desde.pop(r["codigo"])
    for r in suben:
        rojo_desde[r["codigo"]] = ahora.isoformat()
    est["rojo_desde"] = rojo_desde
    if suben and not toca_reporte:
        nombres = _lista([r["departamento"] for r in suben])
        _ntfy(("🧪 " if prueba else "") + f"⬆️🔴 Suben a alerta ROJA: {nombres}",
              f"⬆️ Suben a alerta roja: {nombres}.\nAbajo va el mensaje de cada departamento para reenviar.", 5, ["warning"])
        for r in sorted(suben, key=lambda r: -r["puntaje"])[:5]:
            _ntfy(f"⬆️🔴 {r['departamento']}: sube a alerta ROJA", texto_departamento(r, hora_sat, "sube", prueba), 5, ["warning"])
            enviados += 1

    # 2) reporte programado
    if toca_reporte:
        activos = [r for r in res if r["probabilidad"] != "BAJA"]
        _ntfy(("🧪 " if prueba else "") + f"📋 Reporte de lluvias ({len(activos)} departamentos)", _resumen(hora_sat, res, prueba), 3, ["clipboard"], qr=True)
        for r in sorted(activos, key=lambda r: -ORDEN[r["probabilidad"]])[:10]:
            emo, nom = COLOR[r["probabilidad"]]
            _ntfy(("🧪 " if prueba else "") + f"{emo} {r['departamento']}: alerta {nom.lower()} por lluvias",
                  texto_departamento(r, hora_sat, "reporte", prueba), 4 if r["probabilidad"] == "ALTA" else 3)
            enviados += 1
        if franja and not forzar:
            est["ultima_franja"] = franja

    est["niveles"] = {r["codigo"]: r["probabilidad"] for r in res}
    est["ultimo_calculo"] = ahora.isoformat()
    ESTADO.parent.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(est, ensure_ascii=False, indent=1), encoding="utf-8")
    log.info("Reporte de nubes: %d mensajes", enviados)
    return enviados


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--forzar", action="store_true")
    p.add_argument("--prueba", action="store_true")
    a = p.parse_args()
    ejecutar(forzar=a.forzar or a.prueba, prueba=a.prueba)
