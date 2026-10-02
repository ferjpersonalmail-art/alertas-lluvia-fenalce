"""
validar_alertas.py — ¿Qué tanto aciertan las alertas del índice de nubes?

Compara cada cálculo guardado de salida/indice_nubes.json (historia de git) con la lluvia medida
en las estaciones automáticas del IDEAM (datos.gov.co, conjunto s54a-sgyg) en las 2 horas
siguientes, en el mismo departamento y en los municipios que nombra la alerta.

   python validar_alertas.py               → descarga lo que falte y escribe calibracion/validacion_*.{csv,md}
"""
from __future__ import annotations

import csv
import json
import subprocess
import unicodedata
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
CACHE = BASE / "calibracion" / "cache_validacion"
API = "https://www.datos.gov.co/resource/s54a-sgyg.json"
SENSORES = "('0240','0257')"
VENTANA_H = 2
FUERTE = 10.0     # mm en 2 h en al menos una estación
LLUVIA = 2.0


def norm(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = s.replace(",", " ").replace(".", " ").replace("-", " ")
    s = " ".join(w for w in s.split() if w not in ("d", "c", "dc", "de", "del", "la", "el"))
    alias = {"bogota": "cundinamarca", "archipielago san andres providencia santa catalina": "san andres",
             "san andres providencia": "san andres", "norte santander": "norte santander",
             "valle cauca": "valle cauca", "valle": "valle cauca"}
    return alias.get(s, s)


def socrata(q):
    u = API + "?" + urllib.parse.urlencode(q)
    return json.load(urllib.request.urlopen(u, timeout=300))


# --------------------------------------------------------------------------- pronósticos
def snapshots():
    shas = subprocess.run(["git", "log", "--format=%H", "origin/main", "--", "salida/indice_nubes.json"],
                          cwd=BASE, capture_output=True, text=True).stdout.split()
    vistos, out = set(), []
    for h in shas:
        txt = subprocess.run(["git", "show", f"{h}:salida/indice_nubes.json"], cwd=BASE, capture_output=True).stdout
        try:
            d = json.loads(txt.decode("utf-8"))
        except Exception:
            continue
        t = d.get("hora_satelite")
        if not t or t in vistos:
            continue
        vistos.add(t)
        out.append((datetime.strptime(t, "%Y-%m-%d %H:%M"), d["departamentos"]))
    out.sort(key=lambda x: x[0])
    return out


# --------------------------------------------------------------------------- observaciones
def estaciones():
    f = CACHE / "estaciones.json"
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    r = socrata({"$select": "codigoestacion,departamento,municipio,latitud,longitud,count(*) as n",
                 "$where": f"codigosensor in {SENSORES} and fechaobservacion > '2026-09-20T00:00:00'",
                 "$group": "codigoestacion,departamento,municipio,latitud,longitud", "$limit": "50000"})
    est = {}
    for x in r:
        est[x["codigoestacion"]] = {"dep": norm(x["departamento"]), "mun": norm(x["municipio"])}
    f.write_text(json.dumps(est), encoding="utf-8")
    return est


def lluvia_dia(dia: datetime):
    """{estacion: {hora_local: mm}} para un día (solo registros > 0). Cacheado si el día ya cerró."""
    f = CACHE / f"lluvia_{dia:%Y%m%d}.json"
    if f.exists():
        return {k: {int(h): v for h, v in d.items()} for k, d in json.loads(f.read_text()).items()}
    filas, off = [], 0
    while True:
        r = socrata({"$select": "codigoestacion,fechaobservacion,valorobservado",
                     "$where": f"codigosensor in {SENSORES} and valorobservado != '0' and "
                               f"fechaobservacion between '{dia:%Y-%m-%d}T00:00:00' and '{dia:%Y-%m-%d}T23:59:59'",
                     "$limit": "50000", "$offset": str(off), "$order": ":id"})
        filas += r
        if len(r) < 50000:
            break
        off += 50000
    out = defaultdict(lambda: defaultdict(float))
    for x in filas:
        try:
            v = float(x["valorobservado"])
        except ValueError:
            continue
        if 0 < v < 100:
            out[x["codigoestacion"]][int(x["fechaobservacion"][11:13])] += v
    out = {k: dict(d) for k, d in out.items()}
    if dia.date() < (datetime.now() - timedelta(days=1)).date():
        f.write_text(json.dumps(out), encoding="utf-8")
    return out


def ultima_observacion():
    r = socrata({"$select": "max(fechaobservacion) as m", "$where": f"codigosensor in {SENSORES}"})
    return datetime.strptime(r[0]["m"][:16], "%Y-%m-%dT%H:%M")


# --------------------------------------------------------------------------- comparación
def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    snaps = snapshots()
    hasta = ultima_observacion() - timedelta(hours=VENTANA_H)
    snaps = [s for s in snaps if s[0] <= hasta]
    print(f"{len(snaps)} cálculos del índice entre {snaps[0][0]} y {snaps[-1][0]}; estaciones hasta {hasta + timedelta(hours=VENTANA_H)}")
    est = estaciones()
    por_dep = defaultdict(list)
    for k, e in est.items():
        por_dep[e["dep"]].append(k)
    horas = defaultdict(dict)     # (estacion) -> {datetime_hora: mm}
    d = snaps[0][0].replace(hour=0, minute=0)
    while d <= hasta + timedelta(hours=VENTANA_H):
        for k, hh in lluvia_dia(d).items():
            for h, v in hh.items():
                horas[k][d.replace(hour=h)] = v
        print("  lluvia", d.date(), flush=True)
        d += timedelta(days=1)

    # control de calidad: fuera estaciones con acumulados imposibles (sensor dañado o contador acumulado)
    dias = max(1, (hasta - snaps[0][0]).days + 1)
    malas = [k for k, hh in horas.items() if sum(hh.values()) > 60 * dias or max(hh.values()) > 90]
    for k in malas:
        print("  estación descartada:", k, est.get(k), round(sum(horas[k].values())), "mm")
        horas.pop(k)
        if k in est:
            por_dep[est[k]["dep"]].remove(k)

    filas = []
    for t, deps in snaps:
        h0 = t.replace(minute=0)
        ventana = [h0 + timedelta(hours=i) for i in range(VENTANA_H + (1 if t.minute >= 30 else 0))]
        if t.minute >= 30:
            ventana = ventana[1:]
        for r in deps:
            dep = norm(r["departamento"])
            ks = por_dep.get(dep, [])
            if not ks:
                continue
            acum = {k: sum(horas[k].get(h, 0) for h in ventana) for k in ks}
            mun = {norm(m) for m in (r.get("municipios") or [])}
            ks_mun = [k for k in ks if est[k]["mun"] in mun]
            filas.append({
                "hora": t.strftime("%Y-%m-%d %H:%M"), "codigo": r["codigo"], "departamento": r["departamento"],
                "nivel": r["probabilidad"], "puntaje": r.get("puntaje"), "rayos": r.get("rayos"),
                "n_estaciones": len(ks), "max_mm_dep": round(max(acum.values()), 1),
                "n_est_lluvia": sum(v >= LLUVIA for v in acum.values()),
                "n_est_mun": len(ks_mun), "max_mm_mun": round(max((acum[k] for k in ks_mun), default=0), 1),
            })
    out = BASE / "calibracion" / "validacion_detalle.csv"
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0]))
        w.writeheader(); w.writerows(filas)
    resumen(filas, snaps)


def pct(a, b):
    return f"{100 * a / b:.0f} %" if b else "–"


def resumen(filas, snaps):
    L = [f"# Validación de alertas contra estaciones IDEAM",
         f"Cálculos evaluados: {len(snaps)} ({snaps[0][0]:%d %b %H:%M} a {snaps[-1][0]:%d %b %H:%M}). "
         f"Se mira la lluvia medida en las {VENTANA_H} h siguientes a cada cálculo.",
         f"*Lluvia fuerte* = alguna estación del departamento con ≥ {FUERTE:.0f} mm en {VENTANA_H} h. "
         f"*Lluvia* = ≥ {LLUVIA:.0f} mm.", "",
         "## 1. Cuando damos cada nivel, ¿qué pasó?", "",
         "| Nivel | Casos (depto × hora) | Lluvia fuerte en el depto | Alguna lluvia | Lluvia fuerte en los municipios nombrados* |",
         "|---|---|---|---|---|"]
    for n in ("ALTA", "MEDIA", "BAJA"):
        g = [f for f in filas if f["nivel"] == n]
        gm = [f for f in g if f["n_est_mun"]]
        L.append(f"| {n} | {len(g)} | {pct(sum(f['max_mm_dep'] >= FUERTE for f in g), len(g))} | "
                 f"{pct(sum(f['max_mm_dep'] >= LLUVIA for f in g), len(g))} | "
                 f"{pct(sum(f['max_mm_mun'] >= FUERTE for f in gm), len(gm))} (de {len(gm)} con estación) |")
    ev = [f for f in filas if f["max_mm_dep"] >= FUERTE]
    L += ["", "*Solo casos donde algún municipio nombrado tiene estación.", "",
          "## 2. Cuando sí llovió fuerte, ¿lo avisamos?", "",
          f"Casos con lluvia fuerte medida: {len(ev)}",
          f"- Avisados en ROJA: {pct(sum(f['nivel']=='ALTA' for f in ev), len(ev))}",
          f"- Avisados en AMARILLA: {pct(sum(f['nivel']=='MEDIA' for f in ev), len(ev))}",
          f"- Sin aviso: {pct(sum(f['nivel']=='BAJA' for f in ev), len(ev))}", "",
          "## 3. Por departamento (niveles ALTA+MEDIA)", "",
          "| Departamento | Estaciones | Avisos | Acertados (≥10 mm) | Lluvia fuerte sin aviso |", "|---|---|---|---|---|"]
    por = defaultdict(list)
    for f in filas:
        por[f["departamento"]].append(f)
    for dep, g in sorted(por.items(), key=lambda x: -sum(f["nivel"] != "BAJA" for f in x[1])):
        av = [f for f in g if f["nivel"] != "BAJA"]
        L.append(f"| {dep} | {g[0]['n_estaciones']} | {len(av)} | {pct(sum(f['max_mm_dep'] >= FUERTE for f in av), len(av))} | "
                 f"{sum(f['nivel']=='BAJA' and f['max_mm_dep'] >= FUERTE for f in g)} |")
    L += ["", "## 4. ¿Ayudan los rayos y el puntaje?", "", "| Condición | Casos | Lluvia fuerte |", "|---|---|---|"]
    for nom, cond in [("puntaje ≥ 10", lambda f: (f["puntaje"] or 0) >= 10),
                      ("puntaje 7–10", lambda f: 7 <= (f["puntaje"] or 0) < 10),
                      ("puntaje 3–7", lambda f: 3 <= (f["puntaje"] or 0) < 7),
                      ("rayos ≥ 50", lambda f: (f["rayos"] or 0) >= 50),
                      ("rayos 10–50", lambda f: 10 <= (f["rayos"] or 0) < 50),
                      ("rayos 1–10", lambda f: 1 <= (f["rayos"] or 0) < 10),
                      ("sin rayos", lambda f: not f["rayos"])]:
        g = [f for f in filas if cond(f)]
        L.append(f"| {nom} | {len(g)} | {pct(sum(f['max_mm_dep'] >= FUERTE for f in g), len(g))} |")
    p = BASE / "calibracion" / "validacion_resumen.md"
    p.write_text("\n".join(L), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    main()
