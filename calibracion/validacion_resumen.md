# Validación de alertas contra estaciones IDEAM
Cálculos evaluados: 741 (25 Sep 14:10 a 03 Oct 21:50). Se mira la lluvia medida en las 2 h siguientes a cada cálculo.
*Lluvia fuerte* = alguna estación del departamento con ≥ 10 mm en 2 h. *Lluvia* = ≥ 2 mm.

## 1. Cuando damos cada nivel, ¿qué pasó?

| Nivel | Casos (depto × hora) | Lluvia fuerte en el depto | Alguna lluvia | Lluvia fuerte en los municipios nombrados* |
|---|---|---|---|---|
| ALTA | 3962 | 34 % | 57 % | 14 % (de 3165 con estación) |
| MEDIA | 7465 | 15 % | 36 % | 6 % (de 5948 con estación) |
| BAJA | 9321 | 5 % | 16 % | 2 % (de 2374 con estación) |

*Solo casos donde algún municipio nombrado tiene estación.

## 2. Cuando sí llovió fuerte, ¿lo avisamos?

Casos con lluvia fuerte medida: 2938
- Avisados en ROJA: 46 %
- Avisados en AMARILLA: 37 %
- Sin aviso: 17 %

## 3. Por departamento (niveles ALTA+MEDIA)

| Departamento | Estaciones | Avisos | Acertados (≥10 mm) | Lluvia fuerte sin aviso |
|---|---|---|---|---|
| Chocó | 14 | 639 | 28 % | 12 |
| Antioquia | 61 | 611 | 54 % | 13 |
| Cauca | 5 | 594 | 9 % | 0 |
| Nariño | 26 | 567 | 32 % | 5 |
| Bolívar | 10 | 555 | 8 % | 2 |
| Valle del Cauca | 17 | 520 | 24 % | 12 |
| Caquetá | 4 | 508 | 9 % | 0 |
| Cesar | 13 | 505 | 12 % | 4 |
| Santander | 28 | 488 | 26 % | 53 |
| Córdoba | 16 | 488 | 16 % | 1 |
| Norte de Santander | 12 | 483 | 3 % | 0 |
| Vichada | 5 | 470 | 12 % | 0 |
| Guaviare | 1 | 454 | 0 % | 0 |
| Meta | 15 | 418 | 28 % | 23 |
| Boyacá | 54 | 418 | 26 % | 103 |
| Cundinamarca | 49 | 402 | 31 % | 53 |
| Magdalena | 11 | 398 | 15 % | 3 |
| Sucre | 6 | 363 | 15 % | 0 |
| Casanare | 13 | 330 | 37 % | 57 |
| Tolima | 29 | 327 | 33 % | 16 |
| Caldas | 8 | 321 | 12 % | 6 |
| La Guajira | 15 | 301 | 29 % | 24 |
| Putumayo | 6 | 262 | 39 % | 5 |
| Risaralda | 5 | 245 | 29 % | 32 |
| Arauca | 4 | 234 | 6 % | 0 |
| Huila | 24 | 215 | 12 % | 18 |
| Atlántico | 9 | 185 | 26 % | 32 |
| Quindío | 6 | 126 | 50 % | 36 |

## 4. ¿Ayudan los rayos y el puntaje?

| Condición | Casos | Lluvia fuerte |
|---|---|---|
| puntaje ≥ 10 | 493 | 37 % |
| puntaje 7–10 | 4306 | 31 % |
| puntaje 3–7 | 6628 | 14 % |
| rayos ≥ 50 | 2318 | 44 % |
| rayos 10–50 | 2384 | 28 % |
| rayos 1–10 | 2616 | 17 % |
| sin rayos | 13430 | 6 % |