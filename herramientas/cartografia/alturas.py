"""Altura mediana de cada municipio (Open-Meteo, Copernicus DEM 90 m) con varios puntos dentro del municipio."""
import json, time, requests
import numpy as np
from PIL import Image, ImageDraw

F = json.load(open(r"C:\Users\jgomez\carto\municipios_mgn2024.geojson", encoding="utf-8"))["features"]
LO0, LA1, PASO = -79.2, 13.6, 0.05
W, H = int(12.6 / PASO), int(17.9 / PASO)
img = Image.new("I", (W, H), 0)
d = ImageDraw.Draw(img)
px = lambda lo, la: ((lo - LO0) / PASO, (LA1 - la) / PASO)
for i, f in enumerate(F, 1):
    g = f["geometry"]
    for poly in (g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]):
        d.polygon([px(*c) for c in poly[0]], fill=i)
a = np.array(img)
pts = {}
for i, f in enumerate(F, 1):
    ys, xs = np.nonzero(a == i)
    cod = f["properties"]["MPIO_CCNCT"]
    if len(xs):
        sel = np.linspace(0, len(xs) - 1, min(len(xs), 5)).astype(int)
        pts[cod] = [(LO0 + (xs[k] + .5) * PASO, LA1 - (ys[k] + .5) * PASO) for k in sel]
    else:
        g = f["geometry"]; r = (g["coordinates"][0] if g["type"] == "Polygon" else g["coordinates"][0][0])
        pts[cod] = [(sum(c[0] for c in r) / len(r), sum(c[1] for c in r) / len(r))]
todos = [(c, lo, la) for c, ps in pts.items() for lo, la in ps]
print("puntos:", len(todos))
alt = []
for k in range(0, len(todos), 100):
    b = todos[k:k + 100]
    for intento in range(12):
        try:
            r = requests.get("https://api.opentopodata.org/v1/srtm90m", timeout=60, params={
                "locations": "|".join(f"{x[2]:.4f},{x[1]:.4f}" for x in b)})
            if r.status_code == 429:
                print("límite, espero 30 s", k, flush=True); time.sleep(30); continue
            alt += [(x["elevation"] if x["elevation"] is not None else 0.0) for x in r.json()["results"]]; break
        except Exception as e:
            print("reintento", k, e, flush=True); time.sleep(10 * (intento + 1))
    else:
        raise SystemExit("sin respuesta de Open-Meteo")
    print("lote", k, len(todos), flush=True)
    time.sleep(1.5)
res = {}
for (c, lo, la), h in zip(todos, alt):
    res.setdefault(c, []).append(h)
cent = {c: (float(np.mean([p[0] for p in ps])), float(np.mean([p[1] for p in ps]))) for c, ps in pts.items()}
json.dump({c: {"h": float(np.median(v)), "lon": round(cent[c][0], 3), "lat": round(cent[c][1], 3)} for c, v in res.items()},
          open(r"C:\Users\jgomez\carto\alturas.json", "w"), indent=0)
print("municipios con altura:", len(res))
