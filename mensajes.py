"""
mensajes.py — Redacción de los mensajes para WhatsApp.

WhatsApp interpreta *negrita* y _cursiva_. Los textos fijos (niveles, recomendaciones,
pie de página) se editan en config.yaml, sin tocar este archivo.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from analisis import NIVELES, Evaluacion, rumbo

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# --------------------------------------------------------------------------- formato
def hora_local(ts: int, cfg: dict) -> datetime:
    zona = timezone(timedelta(hours=float(cfg["general"].get("utc_offset_horas", -5))))
    return datetime.fromtimestamp(ts, tz=zona)


def fmt_hora(dt: datetime) -> str:
    h = dt.hour % 12 or 12
    return f"{h}:{dt.minute:02d} {'a. m.' if dt.hour < 12 else 'p. m.'}"


def fmt_fecha(dt: datetime) -> str:
    return f"{dt.day} {MESES[dt.month - 1]}"


def fmt_num(x: float, dec: int = 0) -> str:
    """Formato colombiano: 1.240,5"""
    s = f"{x:,.{dec}f}"
    return s.replace(",", "§").replace(".", ",").replace("§", ".")


def fmt_duracion(minutos: float) -> str:
    minutos = int(round(minutos / 10.0) * 10)
    if minutos < 60:
        return f"~{max(minutos, 10)} min"
    h, m = divmod(minutos, 60)
    return f"~{h} h" + (f" {m} min" if m else "")


def lista(nombres: list[str], maximo: int = 6) -> str:
    if not nombres:
        return ""
    if len(nombres) > maximo:
        return ", ".join(nombres[:maximo]) + f" y {len(nombres) - maximo} más"
    if len(nombres) == 1:
        return nombres[0]
    return ", ".join(nombres[:-1]) + " y " + nombres[-1]


# --------------------------------------------------------------------------- partes
def texto_intensidad(ev: Evaluacion) -> str:
    if ev.dbz_max >= 50:
        base = f"muy fuerte, núcleos de ~{ev.tasa_max:.0f} mm/h o más"
    elif ev.dbz_max >= 40:
        base = f"fuerte, núcleos de hasta ~{ev.tasa_max:.0f} mm/h"
    elif ev.dbz_max >= 30:
        base = f"moderada, hasta ~{ev.tasa_max:.0f} mm/h"
    elif ev.dbz_max >= 20:
        base = f"ligera, hasta ~{max(ev.tasa_max, 1):.0f} mm/h"
    else:
        base = "débil"
    if ev.granizo:
        base += "; posible granizo en los núcleos más intensos"
    return base


def texto_zona(ev: Evaluacion, maximo: int) -> str:
    if ev.municipios:
        return f"{ev.sector} ({lista(ev.municipios, maximo)})" if ev.sector else lista(ev.municipios, maximo)
    return ev.sector or "sin municipio definido"


def texto_extension(ev: Evaluacion) -> str:
    """Área real con lluvia (>=1 mm/h, sin bordes del radar). El % solo se da cuando es claro."""
    km2 = ev.area_lluvia_km2 or ev.area[30]
    pct = ev.pct_lluvia * 100
    if km2 < 5:
        txt = "lluvia en un área pequeña (menos de 5 km²)"
    else:
        redondo = 10 if km2 < 100 else 50 if km2 < 1000 else 100
        txt = f"lluvia sobre ~{fmt_num(round(km2 / redondo) * redondo)} km²"
        if pct >= 5:
            txt += f" (~{fmt_num(round(pct / 5) * 5)} % del departamento)"
        elif pct >= 1:
            txt += f" ({fmt_num(pct)} % del departamento)"
    if ev.area[40] >= 5:
        txt += f"; fuerte en ~{fmt_num(ev.area[40])} km²"
    return txt


def texto_duracion(ev: Evaluacion, inicio_estado: int | None) -> str:
    if inicio_estado and inicio_estado < (ev.inicio_ts or ev.t0):
        return f"lleva {fmt_duracion((ev.t0 - inicio_estado) / 60 + 10)}"
    if ev.viene_de_antes:
        return f"lleva más de {fmt_duracion(ev.duracion_min)}"
    return f"lleva {fmt_duracion(ev.duracion_min)}"


def texto_cantidad(ev: Evaluacion) -> str:
    if ev.acum_1h_max < 1:
        return "menos de 1 mm en la última hora"
    txt = f"hasta ~{ev.acum_1h_max:.0f} mm en la última hora"
    if ev.acum_1h_municipio:
        txt += f" ({ev.acum_1h_municipio})"
    if ev.duracion_min >= 70 and ev.acum_evento_max >= ev.acum_1h_max + 3:
        txt += f"; ~{ev.acum_evento_max:.0f} mm " + ("en las últimas 2 h" if ev.viene_de_antes else "desde que empezó")
    return txt


def texto_movimiento(ev: Evaluacion) -> str | None:
    m = ev.movimiento
    if not m or not m.get("fiable"):
        return None
    if m["vel_kmh"] < 5:
        return "casi estacionaria"
    return f"hacia el {m['hacia']}, ~{m['vel_kmh']:.0f} km/h"


def texto_viento(clima: dict | None) -> str | None:
    if not clima or clima.get("viento_kmh") is None:
        return None
    v, g = clima["viento_kmh"], clima.get("rafaga_kmh")
    if v < 3:
        txt = "en calma"
    else:
        txt = f"del {rumbo(clima.get('viento_dir') or 0)}, {v:.0f} km/h"
    if g is not None and g >= v + 10:
        txt += f" (ráfagas de {g:.0f} km/h)"
    return txt


# --------------------------------------------------------------------------- mensajes
def texto_alerta(ev: Evaluacion, nombre_dep: str, cfg: dict, tipo: str = "inicio",
                 clima: dict | None = None, inicio_estado: int | None = None, prueba: bool = False) -> str:
    m = cfg["mensajes"]
    nivel = NIVELES[ev.nivel - 1]
    meta = m["niveles"][nivel]
    dt0 = hora_local(ev.t0, cfg)
    L = []
    if prueba:
        L.append("🧪 *MENSAJE DE PRUEBA* (datos ficticios, no reenviar)")
    if tipo == "escalamiento":
        L.append(f"⬆️ *ACTUALIZACIÓN · LLUVIA EN {nombre_dep.upper()}*")
        L.append(f"{meta['emoji']} *Sube a {meta['nombre'].lower()}:* {meta['descripcion']}")
    elif tipo == "recordatorio":
        L.append(f"🔁 *CONTINÚA LA LLUVIA · {nombre_dep.upper()}*")
        L.append(f"{meta['emoji']} *{meta['nombre']}:* {meta['descripcion']}")
    else:
        L.append(f"🚨 *ALERTA DE LLUVIA · {nombre_dep.upper()}*")
        L.append(f"{meta['emoji']} *{meta['nombre']}:* {meta['descripcion']}")
    L.append(f"🕘 Radar de las {fmt_hora(dt0)} ({fmt_fecha(dt0)})")
    L.append("")
    L.append(f"🌧️ *Intensidad:* {texto_intensidad(ev)}")
    L.append(f"📍 *Zona:* {texto_zona(ev, int(m.get('max_municipios', 6)))}")
    L.append(f"🗺️ *Extensión:* {texto_extension(ev)}")
    L.append(f"⏱️ *Duración:* {texto_duracion(ev, inicio_estado)}")
    L.append(f"💧 *Cantidad:* {texto_cantidad(ev)}")
    mov = texto_movimiento(ev)
    if mov:
        L.append(f"🧭 *Desplazamiento:* {mov}")
    viento = texto_viento(clima)
    if viento:
        L.append(f"💨 *Viento:* {viento}")
    if ev.rayos_15min:
        L.append(f"⚡ *Actividad eléctrica:* {ev.rayos_15min} rayos en los últimos 15 min (satélite GOES)")
    if ev.proxima_hora:
        L.append(f"⚠️ *Próxima hora:* podría llegar a {lista([n for n, _ in ev.proxima_hora], 5)}")
    if clima and clima.get("lluvia_3h_mm") is not None:
        prob = clima.get("prob_max")
        L.append(f"📈 *Próximas 3 h (modelo):* ~{clima['lluvia_3h_mm']:.0f} mm"
                 + (f", probabilidad de lluvia {prob:.0f} %" if prob is not None else ""))
    recs = m.get("recomendaciones", {}).get(nivel, [])
    if recs:
        L.append("")
        L.append("*Recomendaciones:*")
        L.extend(f"• {r}" for r in recs)
    L.append("")
    portal = cfg["general"].get("portal_url")
    if portal:
        L.append(f"📡 {m.get('texto_portal', 'Radar en vivo')}: {portal}")
    L.append(f"_{cfg['general'].get('firma', '')}. {m.get('pie', '')}_".replace(". _", "_"))
    return "\n".join(L)


def texto_fin(nombre_dep: str, cfg: dict, inicio_ts: int, fin_ts: int, nivel_max: int) -> str:
    m = cfg["mensajes"]
    fin = hora_local(fin_ts, cfg)
    meta = m["niveles"][NIVELES[max(nivel_max, 1) - 1]]
    L = [f"✅ *FIN DE ALERTA DE LLUVIA · {nombre_dep.upper()}*",
         f"La lluvia significativa disminuyó hacia las {fmt_hora(fin)} ({fmt_fecha(fin)}).",
         f"Duración aproximada: {fmt_duracion((fin_ts - inicio_ts) / 60)} · nivel máximo: "
         f"{meta['nombre'].lower().replace('nivel ', '')} {meta['emoji']}"]
    if m.get("fin"):
        L.append(m["fin"])
    L.append(f"_{cfg['general'].get('firma', '')}_")
    return "\n".join(L)


def titulo_notificacion(ev_nivel: int, nombre_dep: str, grupo: str, cfg: dict, tipo: str) -> str:
    if tipo == "fin":
        return f"✅ {nombre_dep}: fin del evento → {grupo}"
    meta = cfg["mensajes"]["niveles"][NIVELES[ev_nivel - 1]]
    prefijo = "⬆️ " if tipo == "escalamiento" else ""
    return f"{prefijo}{meta['emoji']} {nombre_dep}: {meta['descripcion']} → {grupo}"
