"""Compara, para el momento actual, LibreWXR (colores de la página) vs GOES directo (mm/h)."""
import logging, sys, yaml, numpy as np
from pathlib import Path
import motor_alertas as ma, analisis as an, fuente_radar as fr, fuente_goes as fg
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
B = Path(__file__).parent
cfg = yaml.safe_load(open(B / "config.yaml", encoding="utf-8"))
act = ma.departamentos_activos(cfg); cod = sorted(act)
malla = ma.construir_malla(cfg, cod)
t = an.Territorio(B / cfg["territorio"]["geojson"], malla, cfg["territorio"]["campos"])
A = an.Analizador(t, cfg)
g = fg.cargar_cuadros(malla, B / "cache" / "goes", 60)
l = fr.cargar_cuadros("https://api.librewxr.net/public/weather-maps.json", malla, B / "cache" / "librewxr",
                      fr.Descargador(60), 60)
eg, el = A.analizar(g, cod), A.analizar(l, cod)
print(f"\nGOES último cuadro {g[-1].tiempo}  LibreWXR último {l[-1].tiempo}")
print(f"{'Departamento':18} | {'GOES: lluvia km2  >=30  >=40  nivel':38} | LibreWXR: lluvia km2  >=30  >=40  nivel")
for c in sorted(cod, key=lambda c: -(eg[c].area[30] + el[c].area[30]))[:12]:
    a, b = eg[c], el[c]
    print(f"{act[c]['nombre'][:18]:18} | {a.area_lluvia_km2:8.0f} {a.area[30]:6.0f} {a.area[40]:5.0f}  {a.nivel}           | {b.area_lluvia_km2:8.0f} {b.area[30]:6.0f} {b.area[40]:5.0f}  {b.nivel}")
