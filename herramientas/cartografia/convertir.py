"""MGN 2024 (UPRA/DANE) -> mismo esquema que datos/municipios_mgn2018.geojson."""
import json, math
src = json.load(open(r"C:\Users\jgomez\carto\mgn2024.geojson", encoding="utf-8"))["features"]
out = []
for f in src:
    p = f["properties"]
    g = f["geometry"]
    ps = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    lat = sum(c[1] for c in ps[0][0]) / len(ps[0][0])
    km2 = p["Shape__Area"] * 111.32 ** 2 * math.cos(math.radians(lat))
    out.append({"type": "Feature", "geometry": g, "properties": {
        "DPTO_CCDGO": p["DPTO_CCDGO"], "DPTO_CNMBR": p["DPTO_CNMBR"], "MPIO_CCNCT": p["MPIO_CDPMP"],
        "MPIO_CNMBR": p["MPIO_CNMBR"], "MPIO_NAREA": round(km2, 1)}})
json.dump({"type": "FeatureCollection", "name": "MGN 2024 DANE (via UPRA), simplificado 0.002 grados", "features": out},
          open(r"C:\Users\jgomez\carto\municipios_mgn2024.geojson", "w", encoding="utf-8"), ensure_ascii=False,
          separators=(",", ":"))
print(len(out))
