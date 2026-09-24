"""
notificar.py — Te avisa al celular con el mensaje listo para reenviar por WhatsApp.

Canales (se activan solos si están configuradas sus variables de entorno):
  • ntfy      NTFY_TOPIC (obligatoria), NTFY_TOKEN y NTFY_SERVER (opcionales)
  • Telegram  TELEGRAM_TOKEN y TELEGRAM_CHAT_ID
  • Consola   siempre (queda en el registro de la ejecución)

Al tocar la notificación de ntfy se abre WhatsApp con el texto ya escrito: eliges la
mesa técnica y envías. En Telegram el botón "Enviar a WhatsApp" hace lo mismo.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse

import requests

log = logging.getLogger("notificar")

LIMITE_NTFY_BYTES = 7900   # ntfy.sh acepta hasta 8.192 bytes en una publicación JSON


def enlace_whatsapp(texto: str, tipo: str = "app") -> str:
    q = urllib.parse.quote(texto, safe="")
    return f"whatsapp://send?text={q}" if tipo == "app" else f"https://wa.me/?text={q}"


class Consola:
    nombre = "consola"

    def enviar(self, titulo: str, texto: str, prioridad: int, url_radar: str | None) -> bool:
        print("\n" + "=" * 72 + f"\n{titulo}\n" + "-" * 72 + f"\n{texto}\n" + "=" * 72, flush=True)
        return True


class Ntfy:
    nombre = "ntfy"

    def __init__(self, servidor: str, tema: str, token: str | None = None, enlace: str = "app"):
        self.servidor = servidor.rstrip("/")
        self.tema = tema
        self.token = token
        self.enlace = enlace

    def _cuerpo(self, titulo, texto, prioridad, url_radar, click, mensaje=None):
        datos = {"topic": self.tema, "title": titulo, "message": mensaje or texto,
                 "priority": int(prioridad), "tags": ["cloud_with_rain"], "click": click}
        if url_radar:
            datos["actions"] = [{"action": "view", "label": "Ver radar", "url": url_radar}]
        return json.dumps(datos, ensure_ascii=False).encode("utf-8")

    def enviar(self, titulo: str, texto: str, prioridad: int, url_radar: str | None) -> bool:
        click = enlace_whatsapp(texto, self.enlace)
        cuerpo = self._cuerpo(titulo, texto, prioridad, url_radar, click)
        if len(cuerpo) > LIMITE_NTFY_BYTES:   # notificación corta; el texto completo va en el enlace
            resumen = "\n".join(texto.splitlines()[:9]) + "\n…\nToca para abrir el mensaje completo en WhatsApp."
            cuerpo = self._cuerpo(titulo, texto, prioridad, url_radar, click, resumen)
        if len(cuerpo) > LIMITE_NTFY_BYTES:   # mensaje demasiado largo para el enlace
            log.warning("Mensaje muy largo para el enlace de WhatsApp; se envía solo el texto")
            cuerpo = self._cuerpo(titulo, texto, prioridad, url_radar, url_radar or "")
        cabeceras = {"Content-Type": "application/json; charset=utf-8"}
        if self.token:
            cabeceras["Authorization"] = f"Bearer {self.token}"
        try:
            r = requests.post(self.servidor, data=cuerpo, headers=cabeceras, timeout=20)
            if r.status_code >= 400:
                log.error("ntfy respondió %s: %s", r.status_code, r.text[:300])
                return False
            return True
        except requests.RequestException as e:
            log.error("No se pudo enviar a ntfy: %s", e)
            return False


class Telegram:
    nombre = "telegram"

    def __init__(self, token: str, chat_id: str):
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"
        self.chat_id = chat_id

    def enviar(self, titulo: str, texto: str, prioridad: int, url_radar: str | None) -> bool:
        botones = [[{"text": "📲 Enviar a WhatsApp", "url": enlace_whatsapp(texto, "web")}]]
        if url_radar:
            botones.append([{"text": "📡 Ver radar", "url": url_radar}])
        datos = {"chat_id": self.chat_id, "text": f"{titulo}\n\n{texto}"[:4096],
                 "disable_web_page_preview": True, "reply_markup": {"inline_keyboard": botones}}
        try:
            r = requests.post(self.url, json=datos, timeout=20)
            if r.status_code >= 400:   # p. ej., enlace demasiado largo: se envía sin botón
                log.warning("Telegram rechazó el botón (%s); se envía solo el texto", r.status_code)
                datos.pop("reply_markup")
                r = requests.post(self.url, json=datos, timeout=20)
            return r.status_code < 400
        except requests.RequestException as e:
            log.error("No se pudo enviar a Telegram: %s", e)
            return False


def crear_notificadores(cfg: dict, solo_consola: bool = False) -> list:
    canales = [Consola()]
    if solo_consola:
        return canales
    n = cfg.get("notificacion", {})
    tema = os.environ.get("NTFY_TOPIC", "").strip()
    if tema:
        canales.append(Ntfy(os.environ.get("NTFY_SERVER", n.get("ntfy_servidor", "https://ntfy.sh")),
                            tema, os.environ.get("NTFY_TOKEN") or None, n.get("enlace_whatsapp", "app")))
    tg_token, tg_chat = os.environ.get("TELEGRAM_TOKEN", "").strip(), os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if tg_token and tg_chat:
        canales.append(Telegram(tg_token, tg_chat))
    if len(canales) == 1:
        log.warning("Sin canal de celular configurado (NTFY_TOPIC o TELEGRAM_*): solo se imprime en consola")
    return canales


def enviar_a_todos(canales: list, titulo: str, texto: str, prioridad: int, url_radar: str | None) -> dict:
    return {c.nombre: c.enviar(titulo, texto, prioridad, url_radar) for c in canales}
