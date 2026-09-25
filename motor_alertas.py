#!/usr/bin/env python3
"""
motor_alertas.py — Alertas de lluvia por radar para las mesas técnicas agroclimáticas.
FENALCE · Equipo de Agroclimatología · Fase 1 (semiautomática)

Uso:
  python motor_alertas.py                   ejecución normal (GitHub Actions la corre cada 10 min)
  python motor_alertas.py --modo simular    datos reales, muestra los mensajes sin enviarlos ni guardar estado
  python motor_alertas.py --modo prueba     envía una alerta de ejemplo a tu celular (verifica la configuración)
  python motor_alertas.py --modo cobertura  qué porcentaje de cada departamento ve el radar
  python motor_alertas.py --modo demo       escenario ficticio, sin internet, para ver el sistema funcionando
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import logging
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

import analisis as an
import clima_openmeteo
import fuente_radar as fr
import mensajes as msj
import notificar

BASE = Path(__file__).resolve().parent
log = logging.getLogger("motor")
PRIORIDAD = {0: 2, 1: 3, 2: 4, 3: 5}      # nivel -> prioridad ntfy


# =========================================================================== configuración
def cargar_config(ruta: Path) -> dict:
    cfg = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    cfg["departamentos"] = {str(k).zfill(2): v for k, v in cfg["departamentos"].items()}
    return cfg


def departamentos_activos(cfg: dict) -> dict[str, dict]:
    return {c: d for c, d in cfg["departamentos"].items() if d.get("activo", True)}


def construir_malla(cfg: dict, codigos: list[str], margen_grados: float = 1.0) -> fr.Malla:
    """Malla de teselas que cubre los departamentos activos más un margen."""
    campos = cfg["territorio"]["campos"]
    datos = json.loads((BASE / cfg["territorio"]["geojson"]).read_text(encoding="utf-8"))
    xs, ys = [], []
    for f in datos["features"]:
        if str(f["properties"][campos["codigo_departamento"]]).zfill(2) not in codigos:
            continue
        for anillo in an._anillos_exteriores(f["geometry"]):
            xs.extend(anillo[:, 0])
            ys.extend(anillo[:, 1])
    if not xs:
        raise SystemExit("No hay departamentos activos en config.yaml")
    r = cfg["radar"]
    return fr.Malla.desde_bbox(min(xs) - margen_grados, min(ys) - margen_grados,
                               max(xs) + margen_grados, max(ys) + margen_grados,
                               int(r.get("zoom", 6)), int(r.get("tamano_tesela", 512)))


# =========================================================================== estado y bitácora
class Registro:
    """Estado de los eventos (para no repetir mensajes) y bitácora en CSV."""

    COLUMNAS = ["fecha_radar", "departamento_codigo", "departamento", "evento_id", "accion", "nivel",
                "notificado", "canales_ok", "motivo", "area_lluvia_km2", "area_moderada_km2", "area_fuerte_km2",
                "area_muy_fuerte_km2", "pct_departamento", "dbz_max", "acum_1h_max_mm", "municipio_acum_1h_max",
                "acum_evento_max_mm", "prom_municipal_1h_max_mm", "municipio_prom_max", "duracion_min",
                "desplazamiento", "rayos_15min", "municipios", "mensaje"]

    def __init__(self, dir_base: Path, persistir: bool = True):
        self.dir_estado = dir_base / "estado"
        self.dir_hist = dir_base / "historial"
        self.dir_salida = dir_base / "salida"
        self.persistir = persistir
        self.ruta_estado = self.dir_estado / "estado_alertas.json"
        self.estado = {"eventos": {}}
        if self.ruta_estado.exists():
            try:
                self.estado = json.loads(self.ruta_estado.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                log.warning("estado_alertas.json dañado; se reinicia")
        self.estado.setdefault("eventos", {})
        self.filas = []
        self.cambio = False

    def anotar(self, cfg, accion, texto: str | None, canales_ok: str):
        ev: an.Evaluacion = accion.ev
        dt = msj.hora_local(ev.t0, cfg)
        mov = ev.movimiento or {}
        self.filas.append({
            "fecha_radar": dt.strftime("%Y-%m-%d %H:%M"), "departamento_codigo": accion.codigo,
            "departamento": cfg["departamentos"][accion.codigo]["nombre"], "evento_id": accion.evento.get("id", ""),
            "accion": accion.tipo, "nivel": an.NIVELES[accion.nivel - 1] if accion.nivel else "",
            "notificado": "si" if accion.notificar else "no", "canales_ok": canales_ok, "motivo": ev.motivo,
            "area_lluvia_km2": round(ev.area.get(20, 0)), "area_moderada_km2": round(ev.area.get(30, 0)),
            "area_fuerte_km2": round(ev.area.get(40, 0)), "area_muy_fuerte_km2": round(ev.area.get(50, 0)),
            "pct_departamento": round(100 * ev.pct_lluvia, 1), "dbz_max": ev.dbz_max,
            "acum_1h_max_mm": round(ev.acum_1h_max, 1), "municipio_acum_1h_max": ev.acum_1h_municipio,
            "acum_evento_max_mm": round(ev.acum_evento_max, 1), "prom_municipal_1h_max_mm": round(ev.acum_mun_max, 1),
            "municipio_prom_max": ev.acum_mun_nombre, "duracion_min": ev.duracion_min,
            "desplazamiento": (f"{mov.get('hacia')} {mov.get('vel_kmh', 0):.0f} km/h" if mov.get("fiable") else ""),
            "rayos_15min": "" if ev.rayos_15min is None else ev.rayos_15min,
            "municipios": "; ".join(ev.municipios), "mensaje": texto or ""})
        self.cambio = True

    def guardar(self, cfg, evaluaciones: dict, t0: int):
        if not self.persistir or not self.cambio:
            return
        self.dir_estado.mkdir(parents=True, exist_ok=True)
        self.estado["actualizado"] = msj.hora_local(t0, cfg).isoformat()
        self.ruta_estado.write_text(json.dumps(self.estado, ensure_ascii=False, indent=2), encoding="utf-8")
        if self.filas:
            self.dir_hist.mkdir(parents=True, exist_ok=True)
            ruta = self.dir_hist / f"alertas_{msj.hora_local(t0, cfg).strftime('%Y-%m')}.csv"
            nuevo = not ruta.exists()
            with ruta.open("a", newline="", encoding="utf-8-sig" if nuevo else "utf-8") as f:
                w = csv.DictWriter(f, fieldnames=self.COLUMNAS)
                if nuevo:
                    w.writeheader()
                w.writerows(self.filas)
        # resumen de alertas activas (para mostrar en el portal si se quiere)
        self.dir_salida.mkdir(parents=True, exist_ok=True)
        activas = []
        for cod, e in self.estado["eventos"].items():
            ev = evaluaciones.get(cod)
            activas.append({"codigo": cod, "departamento": cfg["departamentos"][cod]["nombre"],
                            "grupo": cfg["departamentos"][cod].get("grupo", ""), "evento_id": e.get("id"),
                            "nivel_max": an.NIVELES[e["nivel_max"] - 1], "notificado": e.get("notificado"),
                            "inicio": msj.hora_local(e["inicio_ts"], cfg).isoformat(),
                            "municipios": ev.municipios if ev else [], "mensaje": e.get("ultimo_mensaje", "")})
        (self.dir_salida / "alertas_activas.json").write_text(json.dumps(
            {"actualizado": msj.hora_local(t0, cfg).isoformat(), "alertas": activas}, ensure_ascii=False, indent=2),
            encoding="utf-8")


# =========================================================================== decisiones
@dataclass
class Accion:
    tipo: str              # inicio | escalamiento | recordatorio | fin
    codigo: str
    ev: an.Evaluacion
    notificar: bool
    nivel: int
    evento: dict = field(default_factory=dict)
    previo: dict | None = None     # estado del evento antes de esta acción (para reintentar si falla el envío)


def decidir(evaluaciones: dict[str, an.Evaluacion], estado: dict, cfg: dict) -> list[Accion]:
    """Compara lo que muestra el radar con los eventos ya abiertos y decide qué avisar."""
    n = cfg["notificacion"]
    orden = {nombre: i + 1 for i, nombre in enumerate(an.NIVELES)}
    nivel_min = orden.get(n.get("desde_nivel", "naranja"), 2)
    recordatorio = int(n.get("recordatorio_min", 0) or 0)
    eventos = estado["eventos"]
    acciones = []
    for cod, ev in evaluaciones.items():
        e = eventos.get(cod)
        if e is None:
            if ev.nivel >= 1:
                notif = ev.nivel >= nivel_min
                inicio = ev.inicio_ts or ev.t0
                e = {"id": f"{cod}-{msj.hora_local(inicio, cfg).strftime('%Y%m%d-%H%M')}", "inicio_ts": inicio,
                     "nivel_max": ev.nivel, "notificado": notif, "nivel_notificado": ev.nivel if notif else 0,
                     "ultimo_envio_ts": ev.t0 if notif else None}
                eventos[cod] = e
                acciones.append(Accion("inicio", cod, ev, notif, ev.nivel, e))
            continue
        previo = copy.deepcopy(e)
        if ev.terminado:
            eventos.pop(cod)
            acciones.append(Accion("fin", cod, ev, bool(e.get("notificado")) and n.get("avisar_fin", True),
                                   e["nivel_max"], e, previo))
            continue
        if ev.nivel > e["nivel_max"]:
            e["nivel_max"] = ev.nivel
            if ev.nivel >= nivel_min:
                tipo = "escalamiento" if e.get("notificado") else "inicio"
                e.update(notificado=True, nivel_notificado=ev.nivel, ultimo_envio_ts=ev.t0)
                acciones.append(Accion(tipo, cod, ev, True, ev.nivel, e, previo))
            else:
                acciones.append(Accion("escalamiento", cod, ev, False, ev.nivel, e, previo))
            continue
        if (recordatorio and e.get("notificado") and ev.nivel >= nivel_min
                and ev.t0 - (e.get("ultimo_envio_ts") or 0) >= recordatorio * 60):
            e["ultimo_envio_ts"] = ev.t0
            acciones.append(Accion("recordatorio", cod, ev, True, ev.nivel, e, previo))
    for cod in [c for c in eventos if c not in evaluaciones]:   # departamento desactivado en config
        eventos.pop(cod)
    return acciones


# =========================================================================== ejecución
def ejecutar(cfg: dict, dir_base: Path, persistir: bool, solo_consola: bool,
             api_url: str | None = None, ahora: float | None = None, dir_cache: Path | None = None,
             clima_fn=None) -> int:
    activos = departamentos_activos(cfg)
    codigos = sorted(activos)
    malla = construir_malla(cfg, codigos)
    log.info("Malla %s (%d×%d px), %d departamentos activos", malla.clave, malla.ancho, malla.alto, len(codigos))

    r = cfg["radar"]
    descargador = fr.Descargador(int(r.get("max_solicitudes_por_minuto", 60)))
    try:
        if r.get("fuente") == "goes" and not api_url:
            import fuente_goes
            cuadros = fuente_goes.cargar_cuadros(malla, dir_cache or (dir_base / "cache" / "goes"),
                                                 int(r.get("ventana_min", 120)))
        else:
          cuadros = fr.cargar_cuadros(api_url or r["api_url"], malla, dir_cache or (dir_base / "cache" / "radar"),
                                    descargador, int(r.get("ventana_min", 120)))
    except Exception as e:
        log.error("No se pudo leer el radar: %s", e)
        return 1
    log.info("%d cuadros de radar (%d descargas)", len(cuadros), descargador.solicitudes)
    if not cuadros:
        log.error("Sin cuadros de radar disponibles")
        return 1
    t0 = max(c.tiempo for c in cuadros)
    edad_min = ((ahora or time.time()) - t0) / 60
    if edad_min > float(r.get("antiguedad_maxima_min", 45)):
        log.warning("El último cuadro tiene %.0f min; no se emiten alertas con datos viejos", edad_min)
        return 0

    territorio = an.Territorio(BASE / cfg["territorio"]["geojson"], malla, cfg["territorio"]["campos"])
    evaluaciones = an.Analizador(territorio, cfg).analizar(cuadros, codigos)
    for cod, ev in sorted(evaluaciones.items(), key=lambda kv: -kv[1].nivel):
        if ev.nivel or ev.activo:
            log.info("%-20s nivel=%d activo=%s  ≥30dBZ=%.0f km²  ≥40dBZ=%.0f km²  máx=%d dBZ  %s",
                     activos[cod]["nombre"], ev.nivel, ev.activo, ev.area[30], ev.area[40], ev.dbz_max, ev.motivo)

    # rayos del satélite GOES-19 (GLM): capa para el portal y confirmación de tormentas
    if cfg.get("rayos", {}).get("activo", True) and not api_url:
        try:
            import rayos_glm
            rayos = rayos_glm.descargar_rayos(int(cfg.get("rayos", {}).get("ventana_min", 30)))
            rayos = rayos_glm.solo_colombia(rayos, BASE / cfg["territorio"]["geojson"],
                                            float(cfg.get("rayos", {}).get("margen_km", 25)))
            if persistir:
                rayos_glm.guardar_geojson(rayos, dir_base / "salida" / "rayos.geojson")
            conteo = rayos_glm.conteo_por_departamento(rayos, territorio, 15)
            for ev in evaluaciones.values():
                ev.rayos_15min = conteo.get(ev.indice, 0)
        except Exception as e:
            log.warning("Rayos GLM no disponibles: %s", e)

    registro = Registro(dir_base, persistir)
    acciones = decidir(evaluaciones, registro.estado, cfg)
    canales = notificar.crear_notificadores(cfg, solo_consola)

    # viento en el centro de la lluvia de los departamentos a notificar (una sola consulta)
    a_notificar = [a for a in acciones if a.notificar and a.tipo != "fin" and a.ev.centro_lluvia]
    climas = {}
    if a_notificar and cfg["notificacion"].get("consultar_viento", True):
        consulta = clima_fn or clima_openmeteo.consultar
        resultados = consulta([a.ev.centro_lluvia for a in a_notificar],
                              cfg["notificacion"].get("incluir_pronostico_modelo", False))
        climas = {a.codigo: c for a, c in zip(a_notificar, resultados)}

    portal = cfg["general"].get("portal_url")
    for a in acciones:
        dep = activos[a.codigo]
        texto, estado_canales = None, ""
        if a.notificar:
            if a.tipo == "fin":
                fin_ts = (a.ev.ultimo_activo_ts or a.ev.t0) + 600
                texto = msj.texto_fin(dep["nombre"], cfg, a.evento["inicio_ts"], fin_ts, a.evento["nivel_max"])
                prioridad = PRIORIDAD[0]
            else:
                texto = msj.texto_alerta(a.ev, dep["nombre"], cfg, a.tipo, climas.get(a.codigo),
                                         a.evento.get("inicio_ts"))
                prioridad = PRIORIDAD[a.nivel]
                a.evento["ultimo_mensaje"] = texto
            titulo = msj.titulo_notificacion(a.nivel, dep["nombre"], dep.get("grupo", ""), cfg, a.tipo)
            ok = notificar.enviar_a_todos(canales, titulo, texto, prioridad, portal)
            celular = {k: v for k, v in ok.items() if k != "consola"}
            estado_canales = ", ".join(f"{k}:{'ok' if v else 'falló'}" for k, v in celular.items())
            if celular and not any(celular.values()):   # no llegó a ningún celular: reintentar en la próxima
                log.error("No se pudo notificar %s (%s); se reintentará", dep["nombre"], a.tipo)
                if a.previo is None:
                    registro.estado["eventos"].pop(a.codigo, None)
                else:
                    registro.estado["eventos"][a.codigo] = a.previo
        else:
            log.info("Registrado sin notificar: %s %s (nivel %d)", dep["nombre"], a.tipo, a.nivel)
        registro.anotar(cfg, a, texto, estado_canales)
    registro.guardar(cfg, evaluaciones, t0)
    log.info("Listo: %d acciones (%d notificadas)", len(acciones), sum(a.notificar for a in acciones))
    return 0


# =========================================================================== otros modos
def modo_prueba(cfg: dict) -> int:
    """Envía una alerta ficticia para comprobar que llega al celular y abre WhatsApp."""
    ev = an.Evaluacion(codigo="73", indice=0, nivel=2, activo=True, t0=int(time.time()) // 600 * 600,
                       inicio_ts=int(time.time()) // 600 * 600 - 2400, duracion_min=40, area_dep_km2=23562,
                       area={10: 5200, 20: 4100, 30: 1900, 40: 420, 50: 12, 55: 0}, pct_lluvia=0.17,
                       dbz_max=45, tasa_max=34, acum_1h_max=18, acum_1h_municipio="Alvarado", acum_evento_max=22,
                       municipios=["Ibagué", "Alvarado", "Venadillo", "Lérida", "Piedras", "Coello", "Ambalema"],
                       sector="sector norte", movimiento={"fiable": True, "vel_kmh": 18, "hacia": "occidente"},
                       proxima_hora=[("Rovira", 30), ("Cajamarca", 50)])
    dep = cfg["departamentos"]["73"]
    texto = msj.texto_alerta(ev, dep["nombre"], cfg, "inicio",
                             {"viento_kmh": 12, "viento_dir": 85, "rafaga_kmh": 28}, prueba=True)
    titulo = "🧪 PRUEBA · " + msj.titulo_notificacion(2, dep["nombre"], dep.get("grupo", ""), cfg, "inicio")
    canales = notificar.crear_notificadores(cfg)
    ok = notificar.enviar_a_todos(canales, titulo, texto, PRIORIDAD[2], cfg["general"].get("portal_url"))
    log.info("Resultado del envío: %s", ok)
    return 0 if all(ok.values()) else 1


def modo_cobertura(cfg: dict, dir_base: Path) -> int:
    codigos = sorted(cfg["departamentos"])
    malla = construir_malla(cfg, codigos)
    descargador = fr.Descargador(int(cfg["radar"].get("max_solicitudes_por_minuto", 60)))
    cobertura = fr.cargar_cobertura(cfg["radar"]["api_url"], malla, descargador)
    t = an.Territorio(BASE / cfg["territorio"]["geojson"], malla, cfg["territorio"]["campos"])
    cubierta = np.bincount(t.dep_raster[cobertura], weights=t.area_px[cobertura], minlength=t.n_dep + 1)
    filas = []
    for cod in codigos:
        d = t.dep_indice.get(cod)
        if d:
            pct = 100 * cubierta[d] / t.dep_area[d]
            filas.append((cod, cfg["departamentos"][cod]["nombre"], pct))
    filas.sort(key=lambda f: -f[2])
    print(f"\n{'Departamento':<22}{'Cobertura radar':>16}")
    for cod, nombre, pct in filas:
        print(f"{nombre:<22}{pct:>15.0f} %")
    (dir_base / "salida").mkdir(exist_ok=True)
    with (dir_base / "salida" / "cobertura_radar.csv").open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["codigo", "departamento", "cobertura_pct"])
        w.writerows([(c, n, round(p, 1)) for c, n, p in filas])
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Alertas de lluvia por radar · FENALCE")
    p.add_argument("--modo", default="normal", choices=["normal", "simular", "prueba", "cobertura", "demo"])
    p.add_argument("--config", default=str(BASE / "config.yaml"))
    p.add_argument("--api-url", default=None, help="URL alternativa de weather-maps.json (pruebas)")
    args = p.parse_args()
    for flujo in (sys.stdout, sys.stderr):   # emojis y tildes también en consolas de Windows
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
                        datefmt="%H:%M:%S")
    cfg = cargar_config(Path(args.config))
    if args.modo == "prueba":
        return modo_prueba(cfg)
    if args.modo == "cobertura":
        return modo_cobertura(cfg, BASE)
    if args.modo == "demo":
        import demo_escenario
        return demo_escenario.correr(cfg)
    rc = ejecutar(cfg, BASE, persistir=(args.modo == "normal"), solo_consola=(args.modo == "simular"),
                    api_url=args.api_url)
    if args.modo == "normal" and cfg.get("reporte_nubes", {}).get("activo", True):
        try:
            import reporte_nubes
            reporte_nubes.ejecutar()
        except Exception as e:  # el reporte nunca debe tumbar las alertas
            log.warning("Reporte de nubes: %s", e)
    return rc


if __name__ == "__main__":
    sys.exit(main())
