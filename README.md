# Alertas de lluvia por radar · FENALCE Agroclimatología

**Fase 1 (semiautomática).** Cada 10 minutos el sistema revisa el radar meteorológico, calcula por
departamento la intensidad, zona, duración, cantidad y desplazamiento de la lluvia, y cuando un
evento alcanza el nivel configurado **te llega una notificación al celular con el mensaje ya
redactado**. Al tocarla se abre WhatsApp con el texto escrito: eliges la mesa técnica, revisas y
envías.

```
Radar (RainViewer, cada 10 min) ──► dBZ ► mm/h ──► indicadores por municipio y departamento
        │                                                   │
        │                                     nivel 🟡 / 🟠 / 🔴 + mensaje para WhatsApp
        ▼                                                   ▼
Viento (Open-Meteo) ─────────────────────────► notificación en tu celular (ntfy o Telegram)
                                                            │  tocas
                                                            ▼
                                          WhatsApp con el texto listo ► eliges la mesa ► Enviar
```

Nada se publica en los grupos sin que tú lo revises. Esa es la diferencia con la Fase 2, que
publicaría directamente desde un número dedicado.

---

## Instalación (unos 15 minutos)

### 1. Subir el proyecto a GitHub
1. Crea un repositorio **público** en GitHub, por ejemplo `alertas-lluvia-fenalce`.
   Público porque GitHub Actions es gratis e ilimitado en repositorios públicos. En uno privado,
   correr cada 10 minutos gasta unos 4.300 minutos al mes y el plan gratuito incluye 2.000.
   El repositorio no contiene nada sensible: las claves van en *Secrets*.
2. Sube todos los archivos de esta carpeta, **incluida la carpeta oculta `.github/`**. Si al
   arrastrar no se sube, crea el archivo a mano: en GitHub, *Add file ▸ Create new file*, nombre
   `.github/workflows/alertas_lluvia.yml`, y pega el contenido.

### 2. Instalar ntfy en el celular
1. Instala **ntfy** (App Store o Google Play). Es gratis y no pide cuenta.
2. Toca **+** y suscríbete a un tema con un nombre difícil de adivinar, por ejemplo
   `fenalce-lluvia-7k2q9x`. Cualquiera que conozca el nombre puede leerlo, así que no uses algo obvio.

### 3. Conectar GitHub con tu celular
En el repositorio: *Settings ▸ Secrets and variables ▸ Actions ▸ New repository secret*
- Nombre: `NTFY_TOPIC` · Valor: el nombre del tema (`fenalce-lluvia-7k2q9x`)

### 4. Probar
*Actions ▸ Alertas de lluvia (radar) ▸ Run workflow*, escribe `prueba` en *modo* y ejecuta.
En menos de un minuto te llega una alerta de ejemplo marcada como **PRUEBA**. Tócala: debe abrirse
WhatsApp con el texto. Si no abre, cambia `enlace_whatsapp: app` por `web` en `config.yaml`.

Desde ese momento el sistema corre solo cada 10 minutos.

### 5. Ajustar departamentos y grupos
En `config.yaml`, sección `departamentos`: deja `activo: true` solo donde haya mesa técnica
autorizada y escribe en `grupo` el nombre del grupo de WhatsApp. Ese nombre aparece en la
notificación para que sepas a qué grupo reenviar. Una mesa regional puede repetirse en varios
departamentos.

---

## Uso diario
1. Llega la notificación, por ejemplo: `🟠 Tolima: lluvia fuerte → Mesa Técnica Agroclimática Tolima`.
2. Tócala. WhatsApp se abre con el mensaje escrito.
3. Elige el grupo, revisa o edita el texto y envía.
4. Cuando el evento termina llega un aviso ✅ de fin, por si quieres cerrarlo en el grupo.

Por cada evento llega **una** notificación al empezar y otra si **sube de nivel**. Si sigue en el
mismo nivel no se repite, salvo que actives `recordatorio_min`.

**Ejemplo de mensaje:**
```
⬆️ *ACTUALIZACIÓN · LLUVIA EN TOLIMA*
🔴 *Sube a nivel rojo:* lluvia muy fuerte, extensa o persistente
🕘 Radar de las 12:10 a. m. (24 sep)

🌧️ *Intensidad:* muy fuerte, núcleos de ~70 mm/h o más
📍 *Zona:* sector nororiente (Ibagué, Alvarado, Piedras y Anzoátegui)
🗺️ *Extensión:* lluvia en el 4 % del departamento; fuerte en ~376 km²
⏱️ *Duración:* lleva ~1 h 30 min
💧 *Cantidad:* hasta ~44 mm en la última hora (Ibagué)
🧭 *Desplazamiento:* hacia el suroccidente, ~25 km/h
💨 *Viento:* del oriente, 14 km/h (ráfagas de 31 km/h)
⚠️ *Próxima hora:* podría llegar a Rovira y Cajamarca

*Recomendaciones:* …
📡 Radar en vivo (plataforma en versión beta): https://agroclima-fenalce-portal.vercel.app/
```

## De dónde sale cada dato

| Línea | Cómo se calcula |
|---|---|
| Intensidad | Clase de dBZ más alta con al menos 3 píxeles (~4,5 km²), convertida a mm/h con Marshall-Palmer (Z = 200·R^1,6) |
| Zona | Municipios con lluvia moderada o más (≥10 km² o ≥10 % del municipio), ordenados por intensidad; el sector sale del centro de la lluvia respecto al departamento |
| Extensión | % del departamento con lluvia (≥20 dBZ) y km² con lluvia fuerte (≥40 dBZ) |
| Duración | Cuadros seguidos con lluvia significativa en el departamento. El evento termina tras 30 min sin ella |
| Cantidad | Suma de la lluvia de los cuadros de la última hora; máximo puntual y municipio donde ocurre |
| Desplazamiento | Correlación de fase entre el cuadro actual y los de hace 10, 20 y 30 min (mediana). Solo se muestra si es confiable |
| Viento | Open-Meteo en el centro de la lluvia (el radar no mide viento). Es viento en superficie; la tormenta se mueve con el viento en altura, por eso las dos direcciones pueden diferir |
| Próxima hora | Se proyecta la lluvia actual según su desplazamiento y se listan los municipios del departamento, hoy sin lluvia, que quedarían cubiertos |

## Niveles (valores iniciales, se editan en `config.yaml`)

Un nivel se cumple si se cumple **cualquiera** de sus criterios. Los criterios de acumulado deben
cumplirse sobre al menos 10 km² y solo cuentan mientras sigue lloviendo.

| Nivel | Criterios |
|---|---|
| 🟡 Amarilla | ≥200 km² con lluvia moderada (≥30 dBZ) **o** ≥25 km² con lluvia fuerte (≥40 dBZ) |
| 🟠 Naranja | ≥150 km² fuerte **o** ≥20 km² muy fuerte (≥50 dBZ) **o** ≥25 mm en 1 h **o** ≥40 mm en 2 h |
| 🔴 Roja | ≥600 km² fuerte **o** ≥100 km² muy fuerte **o** ≥50 mm en 1 h **o** ≥80 mm en 2 h |

Por defecto se notifica **desde naranja** (`notificacion.desde_nivel`). Los eventos amarillos
quedan en la bitácora sin avisar, para no saturar el celular. Puedes bajarlo a `amarilla`.

## Calibración (primeras semanas)
- Toda alerta, notificada o no, queda en `historial/alertas_AAAA-MM.csv` (se abre en Excel) con
  áreas, dBZ, acumulados, municipio y el mensaje enviado.
- Compara los acumulados con las estaciones de la red. Si el radar subestima de forma sistemática,
  ajusta `factor_calibracion`, o prueba `relacion_zr: tropical`, que suele ajustar mejor en lluvia
  convectiva tropical.
- Si llegan demasiadas alertas, sube los umbrales. Si se escapan eventos, bájalos.

## Otros modos
```bash
python motor_alertas.py --modo demo        # escenario ficticio, sin internet: ver el sistema funcionando
python motor_alertas.py --modo simular     # radar real, muestra los mensajes sin enviar ni guardar
python motor_alertas.py --modo prueba      # envía la alerta de ejemplo al celular
python motor_alertas.py --modo cobertura   # % de cada departamento que ve el radar
```
Los modos `simular`, `prueba` y `cobertura` también se pueden lanzar desde *Actions ▸ Run workflow*.

**Correr en tu PC en vez de GitHub:** instala Python 3.10+, ejecuta `pip install -r requirements.txt`,
define la variable de entorno `NTFY_TOPIC` y programa `python motor_alertas.py` cada 10 minutos con el
Programador de tareas de Windows. Solo funciona mientras el PC esté encendido.

**Telegram en vez de ntfy (opcional):** crea un bot con @BotFather y agrega los secrets
`TELEGRAM_TOKEN` y `TELEGRAM_CHAT_ID`. El mensaje llega con el botón "Enviar a WhatsApp". Pueden
estar activos los dos canales a la vez.

## Limitaciones y condiciones de uso
- **Cobertura:** donde no hay radar, no habrá alertas aunque llueva. Corre `--modo cobertura` para
  ver qué departamentos quedan ciegos (se espera poca cobertura en Amazonía y Orinoquía oriental).
- **Son estimaciones.** El radar mide reflectividad, no lluvia. Los mm son aproximados y el mensaje
  lo dice. Los ecos de montaña pueden generar lluvia falsa en puntos fijos, así que conviene revisar
  el radar antes de reenviar.
- **Condiciones de RainViewer:** desde 2026 la API gratuita es para uso personal y educativo (máx.
  100 solicitudes por minuto, zoom 7, solo el esquema de color "Universal Blue"). Para un servicio
  institucional permanente conviene consultar a RainViewer por una licencia o migrar la fuente.
  Solo habría que reemplazar `fuente_radar.py`. Los radares del IDEAM están en AWS como datos
  abiertos (CC BY 4.0), pero con un día de retraso: sirven para calibrar, no para alertar en vivo.
  Open-Meteo es gratuito para uso no comercial.
- **GitHub Actions** puede retrasar las ejecuciones programadas unos minutos. En repositorios
  públicos sin actividad por 60 días pausa la programación. Se reactiva en *Actions*.

## Archivos
| Archivo | Qué hace |
|---|---|
| `config.yaml` | Departamentos, grupos, umbrales y textos. **Lo único que normalmente se edita** |
| `motor_alertas.py` | Orquesta: radar → análisis → decisión → mensaje → notificación → bitácora |
| `fuente_radar.py` | Descarga y decodifica el radar de RainViewer (con caché) |
| `analisis.py` | Indicadores por municipio y departamento, niveles, desplazamiento y próxima hora |
| `mensajes.py` | Redacción de los mensajes de WhatsApp |
| `notificar.py` | Envío a ntfy y Telegram con enlace a WhatsApp |
| `clima_openmeteo.py` | Viento actual en el centro de la lluvia |
| `demo_escenario.py` | Escenario ficticio para demostraciones y pruebas |
| `datos/municipios_mgn2018.geojson` | 1.122 municipios, DANE MGN 2018 simplificado. Se puede cambiar por el del portal en `config.yaml ▸ territorio` |
| `estado/`, `historial/`, `salida/` | Se crean solos: eventos abiertos, bitácora y `alertas_activas.json` |

`salida/alertas_activas.json` se puede leer desde el portal (URL *raw* de GitHub) para mostrar un
aviso con las alertas vigentes.

## Siguiente paso: Fase 2
Publicación automática en los grupos autorizados desde un número dedicado "FENALCE Clima", con
lista blanca de grupos y límites de envío. El motor, los niveles y los mensajes de esta fase se
reutilizan tal cual; solo cambia el paso final: en vez de avisar a quien revisa, se publica en el grupo.
