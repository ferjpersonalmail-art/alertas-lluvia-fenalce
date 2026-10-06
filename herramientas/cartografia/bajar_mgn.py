"""Descarga el MGN municipal (DANE) desde el servicio de la UPRA y lo guarda simplificado (~300 m)."""
import json, sys, requests

URL = "https://services.arcgis.com/wLfHepIACaM0pwj9/arcgis/rest/services/MGN_MPIO_POLITICO_2024/FeatureServer/0"
TOL = float(sys.argv[1]) if len(sys.argv) > 1 else 0.003
info = requests.get(URL, params={"f": "json"}, timeout=60).json()
print(info.get("name"), [f["name"] for f in info.get("fields", [])], info.get("maxRecordCount"))
feats, off = [], 0
while True:
    r = requests.get(URL + "/query", params={
        "where": "1=1", "outFields": "*", "outSR": 4326, "f": "geojson", "resultOffset": off,
        "resultRecordCount": 200, "maxAllowableOffset": TOL, "geometryPrecision": 4}, timeout=180).json()
    fs = r.get("features", [])
    feats += fs
    print("descargados", len(feats))
    if len(fs) < 200:
        break
    off += 200
json.dump({"type": "FeatureCollection", "features": feats}, open(r"C:\Users\jgomez\carto\mgn2024.geojson", "w", encoding="utf-8"),
          ensure_ascii=False)
