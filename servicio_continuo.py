"""
servicio_continuo.py — Mantiene el motor funcionando de forma continua dentro de GitHub Actions.

Por qué: el cron de GitHub no es confiable en repositorios nuevos o con poca actividad
(se observaron huecos de varias horas). En vez de depender de un disparo cada 10 min,
una sola ejecución dura ~5 h 40 min y dentro de ella:
  • cada 5 min corre el motor completo (radar + rayos + alertas por departamento);
  • cada ~2,5 min, entre corridas del motor, refresca solo los rayos (salida/rayos.geojson);
  • después de cada ciclo guarda estado/, historial/ y salida/ en el repositorio (git push).
Al terminar, el flujo de trabajo se vuelve a lanzar a sí mismo (ver alertas_lluvia.yml).

Uso local de prueba:  python servicio_continuo.py --duracion-min 12
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
log = logging.getLogger("continuo")


def sh(*cmd: str, check: bool = False) -> int:
    r = subprocess.run(cmd, cwd=BASE)
    if check and r.returncode:
        raise SystemExit(r.returncode)
    return r.returncode


def guardar(mensaje: str, push: bool):
    if not push:
        return
    for d in ("estado", "historial", "salida"):
        if (BASE / d).is_dir():
            sh("git", "add", "-A", d)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=BASE).returncode == 0:
        return
    sh("git", "commit", "-q", "-m", mensaje)
    for intento in range(3):
        sh("git", "pull", "-q", "--rebase", "--autostash")
        if sh("git", "push", "-q") == 0:
            return
        time.sleep(5 * (intento + 1))
    log.warning("No se pudo hacer push; se reintentará en el próximo ciclo")


# Pronostico de lluvia a 6 horas (pronostico_lluvia.yml): el cron de GitHub llega a atrasarse horas,
# asi que lo dispara este servicio, que ya esta corriendo, a la hora exacta de Colombia.
EMISIONES = {(11, 30): "tarde", (19, 30): "manana"}
ESTADO_PRONOSTICO = BASE / "estado" / "pronostico_disparado.json"


def disparar_pronostico():
    """A partir de las 11:30 (tarde) y de las 19:30 (manana del dia siguiente), y durante una hora, lanza el flujo
    una sola vez por emision. Nunca debe tumbar el motor de alertas."""
    try:
        ahora = datetime.now(timezone.utc) - timedelta(hours=5)          # hora de Colombia
        estado = json.loads(ESTADO_PRONOSTICO.read_text(encoding="utf-8")) if ESTADO_PRONOSTICO.exists() else {}
        for (h, m), ventana in EMISIONES.items():
            inicio = ahora.replace(hour=h, minute=m, second=0, microsecond=0)
            clave = f"{ahora:%Y-%m-%d}|{ventana}"
            if not (inicio <= ahora < inicio + timedelta(hours=1)) or clave in estado:
                continue
            if not os.environ.get("GH_TOKEN"):
                log.warning("Pronóstico: falta GH_TOKEN para dispararlo")
                return
            rc = sh("gh", "workflow", "run", "pronostico_lluvia.yml", "--ref", "main", "-f", f"ventana={ventana}")
            log.info("Pronóstico de lluvia (%s) disparado a las %s: código %s", ventana, ahora.strftime("%H:%M"), rc)
            if rc == 0:
                estado[clave] = ahora.strftime("%H:%M")
                estado = dict(sorted(estado.items())[-20:])
                ESTADO_PRONOSTICO.parent.mkdir(exist_ok=True)
                ESTADO_PRONOSTICO.write_text(json.dumps(estado, indent=1), encoding="utf-8")
    except Exception as e:  # noqa: BLE001 — el pronostico nunca debe tumbar las alertas
        log.warning("Pronóstico: %s", e)


def solo_rayos():
    import rayos_glm
    r = rayos_glm.solo_colombia(rayos_glm.descargar_rayos(30), BASE / "datos" / "municipios_mgn2024.geojson")
    rayos_glm.guardar_geojson(r, BASE / "salida" / "rayos.geojson")
    return len(r)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--duracion-min", type=float, default=340, help="minutos que dura el servicio")
    p.add_argument("--ciclo-min", type=float, default=5, help="cada cuánto corre el motor completo")
    p.add_argument("--sin-push", action="store_true", help="no hacer git push (pruebas locales)")
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    fin = time.time() + a.duracion_min * 60
    ciclo = a.ciclo_min * 60
    push = not a.sin_push
    while time.time() < fin - 60:
        t0 = time.time()
        hora = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        rc = sh(sys.executable, "motor_alertas.py", "--modo", "normal")
        log.info("Motor terminó con código %s", rc)
        disparar_pronostico()
        guardar(f"Bitácora de alertas {hora}", push)

        # Refresco intermedio de rayos, a mitad del ciclo
        medio = t0 + ciclo / 2
        if time.time() < medio and medio < fin - 60:
            time.sleep(medio - time.time())
            try:
                n = solo_rayos()
                log.info("Rayos refrescados: %d", n)
                guardar(f"Rayos {datetime.now(timezone.utc):%H:%M} UTC", push)
            except Exception as e:  # noqa: BLE001 — los rayos nunca deben tumbar el servicio
                log.warning("Rayos: %s", e)

        espera = t0 + ciclo - time.time()
        if espera > 0:
            time.sleep(min(espera, max(0, fin - time.time())))
    log.info("Servicio continuo terminado")


if __name__ == "__main__":
    main()
