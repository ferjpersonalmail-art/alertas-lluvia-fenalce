"""Región natural de cada municipio: base por departamento + ajuste por relieve (altura mediana del municipio).

Reglas (aproximan el mapa de regiones naturales del IGAC, que sigue el relieve y no los límites políticos):
  - Municipios de departamentos de Orinoquía, Amazonía o Pacífica con altura mediana >= 1000 m  -> Andina
    (piedemonte alto del Meta y Casanare, valle de Sibundoy, cordillera en el Chocó).
  - Municipios de departamentos andinos por debajo de 400 m:
      al oriente de la cordillera Oriental (lon > -73.6, lat < 7.2)        -> Orinoquía
      en la bota caucana / piedemonte amazónico (lat < 2.5, lon > -76.8)   -> Amazonía
      Atrato antioqueño (lon < -76.3, lat < 7.0)                          -> Pacífica
      Urabá (lon < -76.3, lat >= 7.0)                                     -> Caribe
  - Municipios de departamentos andinos por debajo de 200 m con lat >= 7.4 y lon < -73.5
    (Bajo Cauca y llanura del norte)                                     -> Caribe
  - Sierra Nevada de Santa Marta y Perijá se dejan en el Caribe.
"""
import json, sys
sys.path.insert(0, r"C:\Users\jgomez\alertas-push")
from reporte_regional import _region_base as region_de

alt = json.load(open(r"C:\Users\jgomez\carto\alturas.json"))
out, cambios = {}, []
for c, v in alt.items():
    base = region_de(c)
    h, lo, la = v["h"], v["lon"], v["lat"]
    r = base
    dep = c[:2]
    if base in ("orinoquia", "amazonia") and h >= 1500:          # piedemonte alto y valle de Sibundoy
        r = "andina"
    elif base == "pacifica" and dep == "27" and h >= 1000:       # cordillera en el Chocó
        r = "andina"
    elif base == "andina" and h < 400:
        if dep in ("25", "15") and lo > -73.6:                   # llanos de Cundinamarca y Boyacá
            r = "orinoquia"
        elif la < 2.5 and lo > -76.8:                            # bota caucana
            r = "amazonia"
        elif lo < -76.3:                                         # Atrato antioqueño / Urabá
            r = "pacifica" if la < 7.0 else "caribe"
        elif h < 200 and la >= 7.4 and lo < -74.0:               # Bajo Cauca
            r = "caribe"
    out[c] = r
    if r != base:
        cambios.append((c, base, r, round(h), lo, la))
json.dump(out, open(r"C:\Users\jgomez\alertas-push\datos\regiones_municipios.json", "w"), indent=0, sort_keys=True)
print(len(out), "municipios;", len(cambios), "cambian de región")
for x in sorted(cambios, key=lambda x: (x[1], x[2])):
    print(x)
