# Evaluación de los hallazgos clínicos (T9, actualizada)

Feature: `odd/tasks/clinical-findings-anemia-iron-diabetes.md`. Criterios: S11 (A1 a A4).
Estado: **proyecto académico. No es validación clínica.** Los hallazgos son sugerencias para revisión médica.

Versión: actualizada después de S17 (regla AGA para anemia), S18 (estado de embarazo en el resultado) y S19 (altitud y tabaquismo obligatorios en el perfil).

## 1. Método

- Se evalúan las **funciones del dominio** del repositorio (`anemia.py`, `iron.py`, `diabetes.py`), no umbrales escritos aparte.
- Datos: NHANES 2017-2018 (ciclo J) y el ciclo L del CDC. Nota: el CDC publica el ciclo L como **2021-2022**. En versiones anteriores de este documento aparecía como 2021-2023; esa etiqueta queda corregida aquí.
- Intervalos de confianza: Wilson al 95 %. Conteos sin ponderar.
- Scripts fuera del repositorio, en la carpeta temporal: `eval_t9_findings.py` (diabetes y distribuciones) y `nhanes_iron/roc.py` (referencias independientes).

## 2. Criterios de aceptación (S11) y resultado

| Criterio | Qué exige | Resultado |
|---|---|---|
| A1 Deterministas | 100 % de concordancia con las tablas citadas | **Cumple.** Pruebas de regla (anemia 33, hierro 16 más 10 de la regla v2, diabetes 13) verifican los cortes de OMS 2024 (Tablas 2 y 3), OMS 2020 (Tabla 1), AGA 2020 (recomendación 1) y ADA 2026 (Tablas 2.1 y 2.2) en los bordes. |
| A2 Exactitud | Sensibilidad y especificidad con IC 95 % contra una referencia **independiente** | **Medible en diabetes** (sección 3). Para anemia y hierro, solo con referencias independientes de la ferritina: sTfR y TSAT (sección 4b), con supuestos que siguen sin verificar. |
| A3 Aceptación | A1 al 100 %, ítems STARD reportados, resultados presentados como observados | **Cumple parcialmente.** A1 se cumple. STARD se reporta en la sección 5. No se afirma validez clínica. |
| A4 Ediciones médicas | Tasa y motivos, sin umbral | **No medible.** La corrección médica (T6) está en curso. |

## 3. Diabetes (A2 medible)

Índice: FPG ≥ 126 mg/dL con la regla del dominio (prueba única, sin HbA1c). Referencia: HbA1c ≥ 6,5 % del laboratorio. Son pruebas distintas, así que el índice no es la referencia.

| Ciclo | n (adultos ≥ 18 con FPG y HbA1c) | Positivos de referencia | Sensibilidad | Especificidad | TP / FN / FP / TN |
|---|---|---|---|---|---|
| 2017-2018 | 2 534 | 373 | 79,1 % (74,7 a 82,9) | 94,7 % (93,7 a 95,6) | 295 / 78 / 114 / 2 047 |
| 2021-2022 (L) | 3 327 | 409 | 76,8 % (72,4 a 80,6) | 96,1 % (95,3 a 96,7) | 314 / 95 / 115 / 2 803 |

Lectura: con una sola glucosa en ayunas, el sistema detecta cerca de 4 de cada 5 casos de HbA1c diabética. Los falsos negativos son esperables, porque la HbA1c y la glucosa en ayunas miden cosas distintas, y la ADA lo reconoce (concordancia incompleta).

Categorías de salida: 2017-2018 con PREDIABETES 1 208, SIN_EVIDENCIA 917 y PENDIENTE_CONFIRMACION 409; 2021-2022 con PREDIABETES 1 400, SIN_EVIDENCIA 1 498 y PENDIENTE_CONFIRMACION 429. No se produjo ninguna DIABETES_CONFIRMADA: cada ciclo de NHANES tiene un solo resultado por persona, así que no puede haber dos mediciones.

## 4. Anemia y deficiencia de hierro: distribución de salidas

**Advertencia de versión.** La tabla de esta sección se generó antes de S17 y S18. Muestra el comportamiento de la regla OMS 2020 para hierro en la muestra sana, no la regla v2 actual. Conviene volver a ejecutarla con la regla vigente.

**Por qué no hay exactitud directa:** la anemia se define por hemoglobina, y la deficiencia de hierro por ferritina. Compararlas con esos mismos valores daría una exactitud de 100 % por construcción. Por eso la exactitud de estos dos hallazgos solo se estima contra referencias independientes (sección 4b).

**Distribución de salidas (muestra sana, PCR ≤ 5 mg/L):**

| Hallazgo | Ciclo | Salidas |
|---|---|---|
| Anemia | 2017-2018 (n = 5 192) | Sin anemia 3 066; leve 125; moderada 53; grave 6; no evaluable 1 942 |
| Anemia | 2021-2022 (L) (n = 1 506) | Sin anemia 938; leve 97; moderada 52; grave 2; no evaluable 417 |
| Deficiencia de hierro (OMS 2020, Tabla 1) | 2017-2018 (n = 5 192) | Sin deficiencia 4 096; deficiencia 223; no evaluable 873 |
| Deficiencia de hierro (OMS 2020, Tabla 1) | 2021-2022 (L) (n = 1 506) | Sin deficiencia 832; deficiencia 115; no evaluable 559 |

**Motivos de "no evaluable":**
- Hierro, 2017-2018 y 2021-2022: casi todos por **embarazo desconocido** (873 y 559). Esto es un problema del script, que no pasa el campo de embarazo de NHANES; la regla lo trata como no evaluable, como exige S3. Hay que corregirlo antes de reportar cobertura.
- Anemia: mujeres sin estado de embarazo (966 y 417) y mayores de 65 años (976 en 2017-2018), fuera de la tabla OMS 2024.
- La muestra excluye PCR > 5 mg/L, así que las categorías con inflamación no aparecen.

**Concordancia descriptiva (anemia y deficiencia de hierro, ambos evaluables):**
- 2017-2018: anémicos con deficiencia 83, anémicos sin deficiencia 81, no anémicos con deficiencia 109, no anémicos sin deficiencia 2 809.
- 2021-2022 (L): anémicos con deficiencia 61, anémicos sin deficiencia 54, no anémicos con deficiencia 44, no anémicos sin deficiencia 766.

Es un dato de concordancia, no de exactitud.

## 4b. Regla v2 frente a referencias independientes (revisión posterior a S17)

Población: adultos de 18 a 65 años, no embarazadas, PCR ≤ 5 mg/L. Sin ponderar. Sin ajuste por altitud ni por tabaquismo (no medido en NHANES). Script: `nhanes_iron/roc.py`.

Regla v2: anemia según hemoglobina OMS 2024 (sin ajuste) → ferritina < 45 si hay anemia, < 15 si no. Regla OMS: ferritina < 15 para todos.

**Referencia sTfR (receptor soluble de transferrina), umbral variable.** Advertencia: el umbral de 4,4 mg/L no está verificado en una fuente primaria y depende del ensayo. Se muestra un rango.

2017-2018 (n = 2 946):

| sTfR > (mg/L) | Sens. v2 | Espec. v2 | Sens. OMS | Espec. OMS | AUC ferritina |
|---|---|---|---|---|---|
| 3,5 | 26,2 % | 97,7 % | 23,5 % | 98,4 % | 0,64 |
| 4,0 | 36,9 % | 96,9 % | 33,2 % | 97,7 % | 0,68 |
| 4,4 | 48,5 % | 96,5 % | 43,4 % | 97,3 % | 0,74 |
| 5,0 | 60,4 % | 95,6 % | 54,7 % | 96,6 % | 0,79 |
| 5,5 | 68,2 % | 95,2 % | 62,2 % | 96,1 % | 0,82 |

2021-2022 (L) (n = 930):

| sTfR > (mg/L) | Sens. v2 | Espec. v2 | Sens. OMS | Espec. OMS | AUC ferritina |
|---|---|---|---|---|---|
| 3,5 | 49,4 % | 93,2 % | 44,7 % | 96,1 % | 0,77 |
| 4,0 | 62,4 % | 91,8 % | 57,3 % | 94,8 % | 0,84 |
| 4,4 | 73,5 % | 90,8 % | 67,6 % | 93,8 % | 0,88 |
| 5,0 | 84,8 % | 88,7 % | 77,2 % | 91,6 % | 0,89 |
| 5,5 | 89,0 % | 87,4 % | 84,9 % | 90,8 % | 0,92 |

**Referencia TSAT (saturación de transferrina), solo 2017-2018 (n = 2 946):**

| TSAT < (%) | Sens. v2 | Espec. v2 | Sens. OMS | Espec. OMS | AUC ferritina |
|---|---|---|---|---|---|
| 10 | 94,6 % | 95,4 % | 84,7 % | 96,3 % | 0,98 |
| 12 | 82,4 % | 96,2 % | 72,3 % | 97,0 % | 0,95 |
| 16 | 53,0 % | 97,6 % | 46,6 % | 98,2 % | 0,88 |
| 20 | 31,3 % | 98,3 % | 27,3 % | 98,7 % | 0,79 |
| 25 | 18,9 % | 98,9 % | 16,5 % | 99,4 % | 0,71 |

**Lectura.**
- La regla v2 tiene más sensibilidad que la OMS sola en todos los umbrales probados. El costo es de cerca de 1 punto de especificidad en 2017-2018 y de 2 a 3 puntos en 2021-2022.
- La ferritina ordena a las personas solo de forma moderada frente a sTfR (AUC 0,64 a 0,82 en 2017-2018). Esto limita cualquier corte fijo.
- Un corte de ferritina de 45 para todos los adultos daría mucha más sensibilidad, pero con demasiados falsos positivos. No se recomienda.
- Los números de 2017-2018 difieren levemente de los de la versión anterior de este documento por el filtro de TSAT.

**Límites de esta sección:** sTfR sube con la eritropoyesis y TSAT baja con la inflamación; ninguna es un estándar de oro. Los umbrales no están verificados contra una fuente primaria. El ensayo de NHANES puede no coincidir con los de los estudios de referencia.

## 5. Ítems STARD 2015 (checklist de reporte)

Fuente: Bossuyt et al., BMJ 2015;351:h5527, https://doi.org/10.1136/bmj.h5527.

- **Población y muestreo:** NHANES, muestra de adultos y niños con datos completos; criterios de inclusión y exclusión en la sección 1 y en el script.
- **Índice y referencia:** índice FPG ≥ 126 (dominio); referencia HbA1c ≥ 6,5 % (laboratorio), declaradas antes de evaluar.
- **Ejecución:** un solo resultado por persona; el índice se calcula con el código del dominio.
- **Resultados:** sensibilidad, especificidad, TP/FN/FP/TN e IC 95 % (sección 3, y 4b para hierro).
- **Limitaciones:** ver sección 6.

## 6. Limitaciones

1. **Anemia y hierro: solo referencias sustitutas.** sTfR y TSAT no son estándar de oro; sus umbrales no están verificados (sección 4b).
2. **Estado de embarazo ausente** es la principal causa de "no evaluable" en mujeres. Ahora la API lo acepta en el resultado (S18), pero no es obligatorio.
3. **Altitud y tabaquismo.** Ahora son obligatorios en el perfil del paciente (S19), pero los datos de NHANES no los incluyen y el análisis de la sección 4b no aplica el ajuste.
4. **Hemoglobina OMS 2024.** Los cortes de adultos son percentiles 5 de poblaciones sanas, sobre todo de ingresos altos. Por diseño, alrededor de 5 % de personas sanas quedan clasificadas como anémicas. Los países de ingresos bajos tuvieron percentiles más bajos, que se excluyeron del análisis de la OMS.
5. **Regla de la AGA para anemia (S17).** Se aplica solo a adultos de 18 años o más, no embarazadas y con PCR ≤ 5. El límite de 18 años es un supuesto del proyecto. La recomendación de la AGA se basa en evidencia de médula ósea, distinta de la de las referencias usadas aquí.
6. **Niños menores de 5 años** con ferritina y PCR son pocos en NHANES; el hierro pediátrico tiene poca potencia.
7. **Edad en años enteros.** Los cortes pediátricos usan meses exactos; aquí se aproxima.
8. **Diseño transversal y sin ponderar.** No es una estimación poblacional.
9. **Diabetes: referencia por HbA1c**, no por diagnóstico médico. La HbA1c puede ser inexacta en anemia o hemoglobinopatías (ADA 2026, rec. 2.4); esos casos no se excluyeron de la referencia.
10. **Sin validación clínica** por un profesional. Ninguna fila de conocimiento está validada para uso real.

## 7. Conclusión

- Las reglas cumplen A1: cada corte coincide con su tabla fuente, verificado por pruebas.
- La **diabetes** tiene exactitud medible contra HbA1c: sensibilidad 77 a 79 % y especificidad 95 a 96 %, con intervalos reportados.
- **Anemia y hierro** tienen solo estimaciones contra referencias sustitutas. La regla v2 mejora la sensibilidad frente a la OMS sola, con un costo pequeño de especificidad, pero la evidencia de los cortes de referencia es incompleta.
- La mayoría de los casos "no evaluables" se deben a datos ausentes (sobre todo el estado de embarazo y, en la práctica, altitud y tabaquismo). El sistema debe pedirlos.
- Ningún resultado es validación clínica. Para uso real faltan validación médica, referencias verificadas, la corrección médica (T6) y el modo sombra en producción (T8).
