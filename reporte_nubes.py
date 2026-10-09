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
import base64
import json
import logging
import os
import sys
import unicodedata
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("reporte")
BASE = Path(__file__).resolve().parent
ZONA = timezone(timedelta(hours=-5))
HORAS_REPORTE = ()   # sin reportes a hora fija: todo se envía por evento (antes 5 a. m., 1 p. m. y 7 p. m.)
BOGOTA_KM2_EVENTO = 10   # km² con lluvia en la zona urbana de Bogotá para considerar que "empezó a llover"
SABANA_KM2_EVENTO = 50   # o km² con lluvia sumando los municipios de la Sabana (Cota, Chía, Funza, Madrid…)
ESTADO = BASE / "estado" / "reporte_nubes.json"
OSPA = "https://www.ideam.gov.co/nuestra-entidad/servicio-de-pronosticos-y-alertas"   # pronosticosyalertas.gov.co tiene el certificado vencido
PORTAL = "https://agroclima-fenalce-portal.vercel.app/"
QR_URL = "https://raw.githubusercontent.com/ferjpersonalmail-art/alertas-lluvia-fenalce/main/datos/qr_portal.png"
FIRMA = ("_Estimación con radar e imágenes de satélite; puede haber diferencias con lo que ocurra en cada finca._\n"
         "*FENALCE · Equipo de Agroclimatología*\nJuan Gómez · Jhon Valencia")
COLOR = {"ALTA": ("⛈️", "ALTA"), "MEDIA": ("🌦️", "MEDIA"), "BAJA": ("🌤️", "BAJA")}
ORDEN = {"BAJA": 0, "MEDIA": 1, "ALTA": 2}
# Departamentos que reciben avisos (máx. 2 al día c/u). Para agregar o quitar uno, editar su código DANE.
# Todo el país menos la región Amazonía (18 Caquetá, 86 Putumayo, 95 Guaviare; 91/94/97 inactivos)
PRESENCIA = {"05", "08", "13", "15", "17", "19", "20", "23", "25", "27", "41", "44", "47",
             "50", "52", "54", "63", "66", "68", "70", "73", "76", "81", "85", "99"}
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
        L.append(f"⬆️⛈️ *SUBE A PROBABILIDAD ALTA DE LLUVIA FUERTE · {r['departamento'].upper()}*")
    else:
        L.append(f"{emo} *PROBABILIDAD {nombre} DE LLUVIA FUERTE · {r['departamento'].upper()}*")
    L.append(f"🕘 Imagen de satélite de las {_hora(dt)} · válido para las próximas 2 horas")
    L.append("")
    frase = {"ALTA": "Es *muy probable* que se presenten lluvias fuertes",
             "MEDIA": "*Podrían* presentarse lluvias fuertes", "BAJA": "Baja posibilidad de lluvias fuertes"}
    zona = _lista(r.get("municipios") or [])
    L.append(f"🌧️ {frase[r['probabilidad']]}" + (f" en *{zona}* y alrededores." if zona else " en el departamento."))
    if r.get("llueve_en"):
        L.append(f"☔ El radar de lluvia *ya muestra lluvia* en {_lista(r['llueve_en'])}.")

    # nubes, en palabras
    if r["frio60"] >= 30:
        nubes = "nubes de tormenta muy desarrolladas"
    elif r["frio40"] >= 100:
        nubes = "nubes cargadas de agua"
    else:
        nubes = "nubosidad dispersa"
    L.append(f"☁️ Hay {nubes}.")
    if r.get("crec") is not None and r["frio40"] >= 100:   # ¿la lluvia se está formando o disipando? (30 min)
        if r["crec"] >= 1.3:
            L.append("📈 *La lluvia se está formando:* las nubes de tormenta están creciendo.")
        elif r["crec"] <= 0.7:
            L.append("📉 *La lluvia se está disipando:* las nubes de tormenta se están reduciendo.")
        else:
            L.append("➡️ Las nubes de lluvia se mantienen: ni crecen ni se disipan.")

    mov = r.get("movimiento")
    v = r.get("viento") or {}
    # municipios a donde podría llegar la lluvia (trayectoria de la tormenta, 30 a 120 min)
    llegar = ""
    try:
        import trayectoria
        g_, vel_ = trayectoria.rumbo_y_velocidad(r)
        dest = trayectoria.camino(r.get("codigo", ""), r.get("llueve_en") or r.get("municipios") or [], g_, vel_)
        cod_ = str(r.get("codigo", "")).zfill(2)
        nombres_ = [m["nom"] if m["dep"] == cod_ else f"{m['nom']} ({m['dep_nom']})" for m in dest]
        if nombres_:
            llegar = f" y podrían llegar a *{_lista(nombres_)}* en 1 a 2 horas"
    except Exception as e:
        log.warning("Trayectoria %s: %s", r.get("departamento"), e)
    if mov and mov["vel_kmh"] >= 5:
        vel_txt = f" (unos {mov['vel_kmh']:.0f} km/h)" if mov["vel_kmh"] <= 60 else ""
        L.append(f"🧭 Las nubes van *hacia el {mov['hacia']}*{vel_txt}{llegar}.")
    elif mov:
        L.append("🧭 Las nubes están casi quietas sobre la zona.")
    elif v.get("alto_dir") is not None and (v.get("alto_kmh") or 0) >= 5:
        L.append(f"🧭 Las nubes van *hacia el {_rumbo(v['alto_dir'] + 180)}*{llegar}.")
    if v.get("viento_dir") is not None and v.get("viento_kmh") is not None:
        if v["viento_kmh"] < 3:
            L.append("💨 Viento cerca del suelo: calmado.")
        else:
            L.append(f"💨 Viento cerca del suelo: viene del {_rumbo(v['viento_dir'])}, unos {v['viento_kmh']:.0f} km/h.")

    if r["rayos"] >= 5:
        L.append(f"⚡ *Sí hay actividad eléctrica:* se detectaron rayos en los últimos 15 minutos.")
    elif r["rayos"] > 0:
        L.append("⚡ Actividad eléctrica aislada (pocos rayos).")
    else:
        L.append("⚡ Sin actividad eléctrica por ahora.")

    if r["rayos"] >= 5:   # seguridad de las personas (no es recomendación agronómica)
        L.append("🛑 Con rayos, evite permanecer en campo abierto o bajo árboles aislados.")
    L.append("")
    L.append(f"🌐 Siga el radar, las estaciones y el clima en nuestro *Portal Agroclimático FENALCE* (versión en desarrollo): {PORTAL}")
    L.append(f"📢 Consulte también los avisos oficiales de la *Oficina del Servicio de Pronósticos y Alertas (OSPA) del IDEAM*: {OSPA}")
    L.append(FIRMA)
    return "\n".join(L)


def texto_nacional(res, hora_sat, prueba=False):
    """Un solo mensaje con todo el país, para publicar en el Canal de WhatsApp."""
    dt = datetime.strptime(hora_sat, "%Y-%m-%d %H:%M")
    rojas = [r for r in res if r["probabilidad"] == "ALTA"]
    amar = [r for r in res if r["probabilidad"] == "MEDIA"]
    L = (["🧪 *MENSAJE DE PRUEBA* (no reenviar)"] if prueba else [])
    L += ["🌦️ *REPORTE DE LLUVIAS · FENALCE*",
          f"🕘 Imagen de satélite de las {_hora(dt)} · válido para las próximas 2 horas", ""]

    def linea(r):
        zona = _lista((r.get("municipios") or [])[:3])
        extra = []
        mov = r.get("movimiento")
        if mov and mov["vel_kmh"] >= 5:
            extra.append(f"se mueven hacia el {mov['hacia']}")
        if r["rayos"] >= 5:
            extra.append("con rayos ⚡")
        return f"• *{r['departamento']}*" + (f": {zona}" if zona else "") + (f" ({', '.join(extra)})" if extra else "")

    if rojas:
        L.append("⛈️ *PROBABILIDAD ALTA* — es muy probable que se presenten lluvias fuertes:")
        L += [linea(r) for r in rojas[:10]]
        L.append("")
    if amar:
        L.append("🌦️ *PROBABILIDAD MEDIA* — podrían presentarse lluvias fuertes:")
        L += [linea(r) for r in amar[:10]]
        if len(amar) > 10:
            L.append(f"• y {len(amar) - 10} departamentos más")
        L.append("")
    if not rojas and not amar:
        L += ["🌤️ No se prevén lluvias fuertes en el país en las próximas 2 horas.", ""]
    else:
        L += ["🌤️ En el resto del país no se prevén lluvias fuertes.", ""]
    if any(r["rayos"] >= 5 for r in rojas + amar):
        L += ["🛑 Donde hay rayos, evite permanecer en campo abierto o bajo árboles aislados.", ""]
    L += [f"🌐 Radar y estaciones en nuestro *Portal Agroclimático FENALCE* (versión en desarrollo): {PORTAL}",
          f"📢 Avisos oficiales de la *OSPA – IDEAM*: {OSPA}",
          FIRMA]
    return "\n".join(L)


# --------------------------------------------------------------------------- envío
TEMA = os.environ.get("NTFY_TOPIC") or "fenalce-lluvia-2ifz77fnqg"
TEMA_MATINAL = "fenalce-matinal-2ifz77"   # reporte de la mañana por departamentos (el puente lo publica en los grupos)


def tema_depto(r) -> str:
    """Tema de ntfy de cada departamento (lo usan los técnicos regionales): fenalce-tolima-2ifz77."""
    nombre = unicodedata.normalize("NFKD", r["departamento"]).encode("ascii", "ignore").decode().lower()
    return "fenalce-" + "-".join(w for w in nombre.replace(",", " ").replace(".", " ").split()) + "-2ifz77"


def _ntfy(titulo, texto, prioridad=3, tags=None, qr=False, tema=None):
    tema = tema or TEMA
    wa = "whatsapp://send?text=" + urllib.parse.quote(texto)
    cuerpo = {"topic": tema, "title": titulo, "message": texto, "priority": prioridad, "tags": tags or [],
              "actions": [{"action": "view", "label": "Enviar por WhatsApp", "url": wa},
                                       {"action": "view", "label": "Abrir portal", "url": PORTAL}]}
    if qr:   # imagen del código QR del portal, para compartirla en el grupo
        cuerpo["attach"] = QR_URL
        cuerpo["filename"] = "QR_portal_agroclimatico_FENALCE.png"
    if len(json.dumps(cuerpo).encode()) > 7800:   # ntfy acepta ~8 KB; el botón de WhatsApp lleva el texto completo
        cuerpo["message"] = texto[:500] + "…\n\n👉 Toque «Enviar por WhatsApp» para el texto completo."
    if len(json.dumps(cuerpo).encode()) > 7800:
        cuerpo["actions"] = cuerpo["actions"][1:]
        cuerpo["message"] = texto[:3500]
    h = {"Content-Type": "application/json"}
    if os.environ.get("NTFY_TOKEN"):
        h["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
    urllib.request.urlopen(urllib.request.Request("https://ntfy.sh", data=json.dumps(cuerpo).encode(), headers=h), timeout=20)


def _h(t):   # encabezado HTTP con tildes/emojis (RFC 2047)
    return "=?UTF-8?B?" + base64.b64encode(t.encode()).decode() + "?="


def _enviar_clip(r, prueba=False, temas=(None,)):
    """Clip animado de la lluvia de la última hora en el departamento (para reenviar al grupo)."""
    try:
        import clip_radar
        info = clip_radar.generar(r["codigo"], r["departamento"], r.get("llueve_en") or r.get("municipios") or [],
                                  nivel=r.get("probabilidad"), movimiento=r.get("movimiento"),
                                  viento=r.get("viento"), flechas=r.get("flechas"))
        if not info:
            return
        f = info["archivo"]
        nombre = unicodedata.normalize("NFKD", r["departamento"]).encode("ascii", "ignore").decode().replace(" ", "_")
        h = {"Filename": f"radar_{nombre}_{info['hora'].split()[0].replace(':', 'h')}.gif",
             "Title": _h(("🧪 " if prueba else "") + f"🎞️ Radar {r['departamento']} · {info['hora']}"),
             "Message": _h(f"Imagen más reciente del radar: {info['hora']} (hace {info['hace_min']} min). "
                           "Ábrala y use Compartir → WhatsApp para enviarla al grupo."),
             "Tags": "film_frames"}
        if os.environ.get("NTFY_TOKEN"):
            h["Authorization"] = "Bearer " + os.environ["NTFY_TOKEN"]
        datos = f.read_bytes()
        for tema in temas:
            urllib.request.urlopen(urllib.request.Request(f"https://ntfy.sh/{tema or TEMA}", data=datos, headers=h,
                                                          method="PUT"), timeout=60)
    except Exception as e:
        log.warning("Clip %s: %s", r.get("departamento"), e)


def _resumen(hora_sat, res, prueba):
    dt = datetime.strptime(hora_sat, "%Y-%m-%d %H:%M")
    rojas = [r["departamento"] for r in res if r["probabilidad"] == "ALTA"]
    amar = [r["departamento"] for r in res if r["probabilidad"] == "MEDIA"]
    L = (["🧪 PRUEBA"] if prueba else []) + [f"📋 Reporte de lluvias · {_hora(dt)}"]
    L.append(f"⛈️ Probabilidad alta: {_lista(rojas) or 'ninguno'}")
    L.append(f"🌦️ Probabilidad media: {_lista(amar) or 'ninguno'}")
    L.append("🌤️ Resto del país: sin lluvias fuertes previstas.")
    L.append(f"🌐 Portal: {PORTAL} (código QR adjunto)")
    return "\n".join(L)


# --------------------------------------------------------------------------- lógica
def _eventos(res, est, avisado, hoy, prueba):
    """Mensajes por evento (no a hora fija). Cada uno sale cuando el evento EMPIEZA y como máximo una vez
    por media jornada (mañana / tarde-noche):
      • Nacional: tormentas fuertes (probabilidad alta) en 2 o más regiones a la vez; máximo 1 por día.
      • Bogotá: empieza a llover en la zona urbana (radar)."""
    n = 0
    try:
        import reporte_regional
        regiones = {reporte_regional.POR_DEPTO.get(r["codigo"], "andina") for r in res if r["probabilidad"] == "ALTA"}
        extensa = len(regiones) >= 2
        dia = hoy.split("-manana")[0].split("-tarde")[0]   # nacional: máximo 1 por día
        if extensa and not est.get("nacional_extensa") and est.get("nacional_dia") != dia \
                and avisado.get("_nacional") != hoy:
            txt = reporte_regional.generar(res, prueba)
            _ntfy(("🧪 " if prueba else "") + "📋 Reporte nacional: tormentas en varias regiones", txt, 4, ["clipboard"])
            _enviar_clip({"codigo": "CO", "departamento": "Colombia", "llueve_en": [], "probabilidad": None,
                          "flechas": reporte_regional.flechas()}, prueba)
            if not prueba:
                est["nacional_dia"] = dia
            n += 1
        est["nacional_extensa"] = extensa
    except Exception as e:
        log.warning("Evento nacional: %s", e)
    try:
        import lluvia_bogota
        eb = lluvia_bogota.calcular()
        km_sabana = sum(a for _, _, a in eb.get("sabana", []))
        llueve = (bool(eb.get("bogota")) and eb["bogota"]["km2"] >= BOGOTA_KM2_EVENTO) or km_sabana >= SABANA_KM2_EVENTO
        if llueve and not est.get("bogota_llueve") and avisado.get("_bogota") != hoy:
            lluvia_bogota.enviar(prueba, e=eb)
            if not prueba:
                avisado["_bogota"] = hoy
            n += 1
        est["bogota_llueve"] = llueve
    except Exception as e:
        log.warning("Evento Bogotá: %s", e)
    return n


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
    # máximo 2 avisos por departamento al día (hora Colombia): uno en la mañana (antes de las 12 m.) y otro en la
    # tarde/noche, sea el inmediato o el del reporte programado. "hoy" es la media jornada actual.
    hoy = f"{ahora:%Y-%m-%d}-" + ("manana" if ahora.hour < 12 else "tarde")
    avisado = {c: d for c, d in est.get("avisado", {}).items() if d == hoy}
    est["avisado"] = avisado
    suben = []
    for r in res:
        antes = previos.get(r["codigo"], "BAJA")
        ult = rojo_desde.get(r["codigo"])
        reciente = ult and (ahora - datetime.fromisoformat(ult)).total_seconds() < 6 * 3600
        # Aviso si (a) hay probabilidad ALTA con rayos, aunque ya viniera en alto (tormenta que sigue activa en la
        # nueva media jornada), o (b) probabilidad MEDIA pero el radar ya muestra lluvia en 3 o más municipios.
        alta = r["probabilidad"] == "ALTA" and r["rayos"] >= 20
        media_llueve = r["probabilidad"] == "MEDIA" and len(r.get("llueve_en") or []) >= 3 and r["rayos"] >= 5
        if (alta or media_llueve) and r["codigo"] in PRESENCIA and avisado.get(r["codigo"]) != hoy:
            r["_tipo"] = "sube" if (alta and ORDEN[antes] < ORDEN["ALTA"]) else "sigue"
            suben.append(r)
    for r in res:
        if r["probabilidad"] == "ALTA":
            rojo_desde.setdefault(r["codigo"], ahora.isoformat())
        elif r["codigo"] in rojo_desde and (ahora - datetime.fromisoformat(rojo_desde[r["codigo"]])).total_seconds() >= 6 * 3600:
            rojo_desde.pop(r["codigo"])
    for r in suben:
        rojo_desde[r["codigo"]] = ahora.isoformat()
        if not prueba:
            avisado[r["codigo"]] = hoy
    est["rojo_desde"] = rojo_desde
    if suben and not toca_reporte:
        nombres = _lista([r["departamento"] for r in suben])
        if len(suben) > 1:   # con un solo departamento basta su propio mensaje
            _ntfy(("🧪 " if prueba else "") + f"⛈️ Avisos de lluvia: {nombres}",
                  f"⛈️ Avisos de lluvia fuerte para: {nombres}.\nAbajo va el mensaje de cada departamento para reenviar.", 5, ["warning"])
        for i, r in enumerate(sorted(suben, key=lambda r: -r["puntaje"])):
            # el técnico del departamento siempre recibe el suyo; al supervisor le llegan máximo 5
            temas = [tema_depto(r)] + ([None] if i < 5 else [])
            tipo = r.get("_tipo", "sube")
            txt = texto_departamento(r, hora_sat, tipo, prueba)
            if tipo == "sube":
                tit = f"⬆️⛈️ {r['departamento']}: sube a probabilidad alta"
            elif r["probabilidad"] == "ALTA":
                tit = f"⛈️ {r['departamento']}: probabilidad alta de lluvia fuerte"
            else:
                tit = f"🌦️ {r['departamento']}: está lloviendo"
            for tm in temas:
                _ntfy(("🧪 " if prueba else "") + tit, txt, 5, ["warning"], tema=tm)
            _enviar_clip(r, prueba, temas)
            enviados += 1

    # 2) eventos sin hora fija: reporte nacional y Bogotá (máximo 2 al día cada uno: mañana y tarde/noche)
    if not toca_reporte:
        enviados += _eventos(res, est, avisado, hoy, prueba)

    # 3) reporte forzado a mano (--forzar): mensaje nacional y los departamentos en roja
    if toca_reporte:
        rojas = [r for r in res if r["probabilidad"] == "ALTA"]
        amar = [r for r in res if r["probabilidad"] == "MEDIA"]
        try:   # reporte por regiones (Andina, Caribe, Pacífica, Orinoquía, Amazonía)
            import reporte_regional
            txt_nac = reporte_regional.generar(res, prueba)
        except Exception as e:
            log.warning("Reporte regional: %s", e)
            txt_nac = texto_nacional(res, hora_sat, prueba)
        _ntfy(("🧪 " if prueba else "") + f"📋 Reporte nacional: {len(rojas)} con probabilidad alta, {len(amar)} media",
              txt_nac, 4 if rojas else 3, ["clipboard"], qr=True)
        try:
            import reporte_regional
            flechas_reg = reporte_regional.flechas()
        except Exception:
            flechas_reg = None
        _enviar_clip({"codigo": "CO", "departamento": "Colombia", "llueve_en": [], "probabilidad": None,
                      "flechas": flechas_reg}, prueba)
        enviados += 1
        if ahora.hour == 5:   # en la madrugada: dónde llueve y condiciones de cada departamento de nuestras zonas
            try:
                import lluvia_municipios
                lluvia_municipios.enviar(prueba, tema=TEMA_MATINAL, clip_nacional=False)
            except Exception as e:
                log.warning("Reporte de la mañana por departamentos: %s", e)
        try:                  # Bogotá y la Sabana, en cada reporte programado
            import lluvia_bogota
            lluvia_bogota.enviar(prueba)
        except Exception as e:
            log.warning("Reporte de Bogotá: %s", e)
        para_avisar = [r for r in rojas if r["codigo"] in PRESENCIA and avisado.get(r["codigo"]) != hoy]
        for i, r in enumerate(sorted(para_avisar, key=lambda r: -r["puntaje"])):
            if not prueba:
                avisado[r["codigo"]] = hoy
            temas = [tema_depto(r)] + ([None] if i < 5 else [])
            txt = texto_departamento(r, hora_sat, "reporte", prueba)
            for tm in temas:
                _ntfy(("🧪 " if prueba else "") + f"⛈️ {r['departamento']}: probabilidad alta de lluvia fuerte", txt, 4, tema=tm)
            _enviar_clip(r, prueba, temas)
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
