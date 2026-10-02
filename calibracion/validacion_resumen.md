# Validación de alertas contra estaciones IDEAM
Cálculos evaluados: 456 (25 Sep 14:10 a 01 Oct 21:50). Se mira la lluvia medida en las 2 h siguientes a cada cálculo.
*Lluvia fuerte* = alguna estación del departamento con ≥ 10 mm en 2 h. *Lluvia* = ≥ 2 mm.

## 1. Cuando damos cada nivel, ¿qué pasó?

| Nivel | Casos (depto × hora) | Lluvia fuerte en el depto | Alguna lluvia | Lluvia fuerte en los municipios nombrados* |
|---|---|---|---|---|
| ALTA | 2758 | 30 % | 52 % | 11 % (de 2242 con estación) |
| MEDIA | 3300 | 12 % | 33 % | 5 % (de 2580 con estación) |
| BAJA | 6710 | 5 % | 16 % | 2 % (de 1252 con estación) |

*Solo casos donde algún municipio nombrado tiene estación.

## 2. Cuando sí llovió fuerte, ¿lo avisamos?

Casos con lluvia fuerte medida: 1572
- Avisados en ROJA: 53 %
- Avisados en AMARILLA: 25 %
- Sin aviso: 22 %

## 3. Por departamento (niveles ALTA+MEDIA)

| Departamento | Estaciones | Avisos | Acertados (≥10 mm) | Lluvia fuerte sin aviso |
|---|---|---|---|---|
| Chocó | 14 | 382 | 24 % | 12 |
| Antioquia | 61 | 364 | 43 % | 13 |
| Cauca | 5 | 352 | 7 % | 0 |
| Valle del Cauca | 17 | 329 | 20 % | 12 |
| Nariño | 27 | 303 | 41 % | 5 |
| Bolívar | 10 | 282 | 4 % | 2 |
| Vichada | 5 | 258 | 17 % | 0 |
| Caquetá | 4 | 255 | 5 % | 0 |
| Córdoba | 16 | 251 | 1 % | 1 |
| Santander | 28 | 248 | 29 % | 53 |
| Cesar | 13 | 242 | 13 % | 4 |
| Norte de Santander | 12 | 235 | 0 % | 0 |
| Guaviare | 1 | 228 | 0 % | 0 |
| Boyacá | 54 | 223 | 35 % | 67 |
| Cundinamarca | 49 | 217 | 29 % | 0 |
| Meta | 15 | 207 | 30 % | 18 |
| Magdalena | 11 | 200 | 14 % | 3 |
| Sucre | 6 | 188 | 7 % | 0 |
| La Guajira | 15 | 177 | 19 % | 24 |
| Caldas | 8 | 170 | 15 % | 6 |
| Tolima | 30 | 158 | 38 % | 11 |
| Putumayo | 6 | 147 | 37 % | 1 |
| Casanare | 13 | 146 | 43 % | 50 |
| Risaralda | 5 | 145 | 32 % | 16 |
| Huila | 24 | 105 | 10 % | 15 |
| Atlántico | 9 | 91 | 15 % | 12 |
| Arauca | 4 | 82 | 1 % | 0 |
| Quindío | 6 | 73 | 51 % | 26 |

## 4. ¿Ayudan los rayos y el puntaje?

| Condición | Casos | Lluvia fuerte |
|---|---|---|
| puntaje ≥ 10 | 248 | 31 % |
| puntaje 7–10 | 2564 | 30 % |
| puntaje 3–7 | 3246 | 12 % |
| rayos ≥ 50 | 1416 | 42 % |
| rayos 10–50 | 1489 | 23 % |
| rayos 1–10 | 1566 | 13 % |
| sin rayos | 8297 | 5 % |