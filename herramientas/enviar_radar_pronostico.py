"""Envía a pedido el radar/satélite actual de un departamento y su pronóstico de 6 horas.
Uso: python herramientas/enviar_radar_pronostico.py 76"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE))
import reporte_nubes as rn
import pronostico_6h as p

cod = sys.argv[1] if len(sys.argv) > 1 else "76"
D = json.loads((BASE / "salida" / "indice_nubes.json").read_text(encoding="utf-8"))
r = [x for x in D["departamentos"] if x["codigo"] == cod][0]
tema = rn.tema_depto(r)
txt = rn.texto_departamento(r, D["hora_satelite"], "reporte")
rn._ntfy(f"📡 {r['departamento']}: radar y satélite ahora", txt, 4, tema=tema)
rn._enviar_clip(r, False, [tema])
t2 = p.texto(r["departamento"], p.pronostico(p.municipios(cod)))
rn._ntfy(f"🕕 {r['departamento']}: pronóstico 6 horas", t2, 3, tema=tema)
sys.stdout.reconfigure(encoding="utf-8")
print(txt)
print("-----")
print(t2)
