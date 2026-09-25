"""Manda al celular (ntfy) alertas de PRUEBA con el índice de nubes de este momento."""
import json, urllib.parse, urllib.request, yaml, os
from pathlib import Path
B = Path(__file__).parent
cfg = yaml.safe_load(open(B / "config.yaml", encoding="utf-8"))
tema = os.environ.get("NTFY_TOPIC") or cfg.get("notificacion", {}).get("ntfy", {}).get("tema") or "fenalce-lluvia-2ifz77fnqg"
d = json.loads((B / "salida" / "indice_nubes.json").read_text(encoding="utf-8"))
EMO = {"ALTA": "🔴", "MEDIA": "🟠", "BAJA": "🟢"}
sel = [r for r in d["departamentos"] if r["probabilidad"] != "BAJA"][:5]
for r in sel:
    zona = ", ".join(r["municipios"]) or "varios sectores"
    txt = (f"🧪 *MENSAJE DE PRUEBA* (no reenviar)\n"
           f"{EMO[r['probabilidad']]} *PROBABILIDAD {r['probabilidad']} DE LLUVIA FUERTE · {r['departamento'].upper()}*\n"
           f"🕘 Satélite de las {d['hora_satelite'][11:]} · próximas 1–2 horas\n\n"
           f"📍 *Zona:* {zona}\n"
           + "".join(f"• {x[0].upper() + x[1:]}\n" for x in r["razones"]) +
           "\n*Recomendación:* Se sugiere revisar drenajes y programar labores de campo fuera de la franja de lluvia.\n"
           "_FENALCE · Equipo de Agroclimatología. Estimación satelital; consulte también los avisos del IDEAM._")
    wa = "whatsapp://send?text=" + urllib.parse.quote(txt)
    body = json.dumps({"topic": tema, "title": f"🧪 {EMO[r['probabilidad']]} {r['departamento']}: prob. {r['probabilidad']} de lluvia fuerte",
                       "message": txt, "tags": ["test_tube"], "click": wa,
                       "actions": [{"action": "view", "label": "Enviar por WhatsApp", "url": wa}]}).encode()
    urllib.request.urlopen(urllib.request.Request("https://ntfy.sh", data=body, headers={"Content-Type": "application/json"}), timeout=20)
    print("enviada:", r["departamento"], r["probabilidad"])
print(len(sel), "alertas de prueba enviadas al tema", tema)
