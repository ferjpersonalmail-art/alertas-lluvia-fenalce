"""pronostico_6h.py - Pronóstico de lluvia de las próximas 6 horas por municipio (modelos numéricos, Open-Meteo).

Uso:  python pronostico_6h.py 76            -> imprime el mensaje
      python pronostico_6h.py 76 enviar     -> lo publica en el canal del departamento (WhatsApp)
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent


def _centro(geom):
    pts = []
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for p in polys:
        pts += p[0]
    xs, ys = zip(*pts)
    return sum(ys) / len(ys), sum(xs) / len(xs)


def municipios(cod_dep):
    import lluvia_municipios as lm
    lm.calcular()
    t = lm._CACHE["t"]
    nombre = {str(t.mun_codigo[i]).zfill(5): t.mun_nombre[i] for i in range(1, t.n_mun + 1)}
    g = json.loads((BASE / "datos" / "municipios_mgn2024.geojson").read_text(encoding="utf-8"))
    out = []
    for f in g["features"]:
        c = f["properties"]["MPIO_CCNCT"]
        if c.startswith(cod_dep):
            lat, lon = _centro(f["geometry"])
            out.append((c, nombre.get(c, f["properties"]["MPIO_CNMBR"].title()), lat, lon))
    return out


def pronostico(muns):
    lat = ",".join(f"{m[2]:.3f}" for m in muns)
    lon = ",".join(f"{m[3]:.3f}" for m in muns)
    url = ("https://api.open-meteo.com/v1/forecast?latitude=" + lat + "&longitude=" + lon +
           "&hourly=precipitation,precipitation_probability&forecast_hours=7&timezone=America%2FBogota")
    d = json.loads(urllib.request.urlopen(url, timeout=60).read())
    d = d if isinstance(d, list) else [d]
    res = []
    for m, x in zip(muns, d):
        h = x["hourly"]
        horas = h["time"][1:7]                     # las próximas 6 horas completas
        prob = [p or 0 for p in h["precipitation_probability"][1:7]]
        mm = [p or 0 for p in h["precipitation"][1:7]]
        i = max(range(len(prob)), key=lambda k: (prob[k], mm[k]))
        res.append({"nombre": m[1], "prob": max(prob), "mm": sum(mm), "hora": horas[i]})
    return res


def _h(iso):
    d = datetime.fromisoformat(iso)
    return f"{d.hour % 12 or 12} {'a. m.' if d.hour < 12 else 'p. m.'}"


def _lista(xs):
    xs = list(xs)
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " y " + xs[-1]


def texto(nombre_dep, res):
    import reporte_nubes as rn
    ahora = datetime.now(rn.ZONA)
    alta = sorted([r for r in res if r["prob"] >= 60], key=lambda r: -r["prob"])
    media = sorted([r for r in res if 30 <= r["prob"] < 60], key=lambda r: -r["prob"])
    L = [f"🕕 *PRONÓSTICO DE LLUVIA · PRÓXIMAS 6 HORAS · {nombre_dep.upper()}*",
         f"🕘 Elaborado a las {rn._hora(ahora)} · válido hasta las {rn._hora(ahora + timedelta(hours=6))}", ""]

    def linea(rs):   # nombres o cantidad, no los dos
        nombres = _lista(r["nombre"] for r in rs) if len(rs) <= 10 else f"{len(rs)} municipios"
        horas = [r["hora"] for r in rs]
        pico = max(set(horas), key=horas.count)
        return f"{nombres}, sobre todo hacia las {_h(pico)}".rstrip(".")

    def cuanto(rs):
        mx = max(r["mm"] for r in rs)
        return "" if mx >= 10 else (" (lluvias moderadas)" if mx >= 2 else " (lluvias débiles o lloviznas)")
    if alta:
        L.append(f"🌧️ *Probable lluvia*{cuanto(alta)} en {linea(alta)}.")
    if media:
        L.append(f"🌦️ *Posible lluvia*{cuanto(media)} en {linea(media)}.")
    if not alta and not media:
        L.append("🌤️ No se esperan lluvias importantes en el departamento en las próximas 6 horas.")
    fuertes = [r["nombre"] for r in sorted(res, key=lambda r: -r["mm"]) if r["mm"] >= 10]
    if fuertes:
        L.append(f"⛈️ Podría llover fuerte (más de 10 mm) en {_lista(fuertes[:4])}.")
    L += ["", "_Pronóstico de modelos numéricos del tiempo (Open-Meteo); las tormentas de la tarde pueden "
              "formarse en lugares distintos. Para lo que pasa ahora, vea el radar._",
          "*FENALCE · Equipo de Agroclimatología*\nJuan Gómez · Jhon Valencia"]
    return "\n".join(L)


if __name__ == "__main__":
    cod = sys.argv[1] if len(sys.argv) > 1 else "76"
    import reporte_nubes as rn
    muns = municipios(cod)
    res = pronostico(muns)
    dep = {"76": "Valle del Cauca"}.get(cod, cod)
    txt = texto(dep, res)
    if "enviar" in sys.argv:
        rn._ntfy(f"🕕 {dep}: pronóstico 6 horas", txt, 3, tema=rn.tema_depto({"codigo": cod, "departamento": dep}))
        print("enviado")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(txt)
        for r in sorted(res, key=lambda r: -r["prob"])[:12]:
            print(r)
