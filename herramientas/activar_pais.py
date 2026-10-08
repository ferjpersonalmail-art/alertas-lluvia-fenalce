"""Pone en el grupo «Tiempo y clima FENALCE» del puente todos los departamentos de PRESENCIA."""
import json, sys, pathlib, yaml
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import reporte_nubes
cfg = yaml.safe_load(open(pathlib.Path(__file__).resolve().parents[1] / "config.yaml", encoding="utf-8"))
deps = None
for k, v in cfg.items():
    if isinstance(v, dict) and "05" in v:
        deps = v
nombres = sorted(deps[c]["nombre"] for c in reporte_nubes.PRESENCIA)
p = pathlib.Path(r"C:\Users\jgomez\puente-whatsapp\config.json")
c = json.loads(p.read_text(encoding="utf-8"))
for g in c["grupos"]:
    if g["nombre"].startswith("Tiempo y clima"):
        g["departamentos"] = nombres
p.write_text(json.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
print(len(nombres), nombres)
