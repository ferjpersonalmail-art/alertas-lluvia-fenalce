# Cartografía de municipios y regiones naturales

## Municipios
- `datos/municipios_mgn2024.geojson`: Marco Geoestadístico Nacional (MGN) 2024 del DANE, descargado del servicio
  público de la UPRA (`MGN_MPIO_POLITICO_2024`), simplificado a 0,002° (~220 m). 1.121 municipios.
  Reemplaza `municipios_mgn2018.geojson` (≈10 vértices por municipio, se veía tosco); el nuevo tiene ≈60.
- Scripts: `herramientas/cartografia/bajar_mgn.py` (descarga) y `convertir.py` (mismo esquema que antes:
  DPTO_CCDGO, DPTO_CNMBR, MPIO_CCNCT, MPIO_CNMBR, MPIO_NAREA).
- Como cada municipio se simplificó por separado, quedan grietas pequeñas entre vecinos; `clip_radar.py`
  las cierra al dibujar.

## Regiones naturales (`datos/regiones_municipios.json`)
Base por departamento (Caribe, Pacífica, Orinoquía, Amazonía; el resto Andina; litoral Pacífico de Valle,
Cauca y Nariño en Pacífica; Urabá en Caribe), ajustada por relieve con la altura mediana de cada municipio
(5 puntos dentro del municipio, SRTM 90 m vía OpenTopoData). Script: `herramientas/cartografia/alturas.py`
y `regiones.py`.

| Regla | Pasa a |
|---|---|
| Orinoquía / Amazonía con altura mediana ≥ 1.500 m (piedemonte alto del Meta y Casanare, valle de Sibundoy) | Andina |
| Chocó con altura mediana ≥ 1.000 m (El Carmen de Atrato, San José del Palmar) | Andina |
| Cundinamarca / Boyacá < 400 m al oriente (lon > −73,6) (Paratebueno) | Orinoquía |
| Andina < 400 m al sur de 2,5° N y al oriente de −76,8 (Piamonte, bota caucana) | Amazonía |
| Antioquia < 400 m al occidente de −76,3: al sur de 7° N (Vigía del Fuerte) / al norte (Urabá) | Pacífica / Caribe |
| Andina < 200 m al norte de 7,4° N y al occidente de −74 (Bajo Cauca: Caucasia, Nechí, Cáceres, Zaragoza) | Caribe |

Resultado: 21 municipios cambian respecto a la asignación por departamento. Es una aproximación al mapa de
regiones naturales del IGAC (que sigue el relieve y no los límites municipales); las capitales de piedemonte
(Florencia, Mocoa, Acacías, Villavicencio) quedan en su región de llanura. La Sierra Nevada y Perijá quedan
en el Caribe y el Magdalena Medio en la Andina.
