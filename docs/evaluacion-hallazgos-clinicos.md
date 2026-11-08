# Evaluación de los hallazgos clínicos (T9)

Feature: `odd/tasks/clinical-findings-anemia-iron-diabetes.md`. Criterios: S11 (A1 a A4).
Estado: **proyecto académico. No es validación clínica.** Los hallazgos son sugerencias para revisión médica.

## 1. Método

- Se evalúan las **funciones del dominio** del repositorio (`anemia.py`, `iron.py`, `diabetes.py`), no umbrales escritos aparte.
- Datos: NHANES 2017-2018 (J) y 2021-2023 (L), archivos XPT del CDC.
- Intervalos de confianza: Wilson al 95 %. Conteos sin ponderar.
- Script: `eval_t9_findings.py` (fuera del repositorio, en la carpeta temporal).

## 2. Criterios de aceptación (S11) y resultado

| Criterio | Qué exige | Resultado |
|---|---|---|
| A1 Deterministas | 100 % de concordancia con las tablas citadas | **Cumple.** 62 pruebas de regla (anemia 33, hierro 16, diabetes 13) verifican los cortes de OMS 2024 (Tablas 2 y 3, ferritina y PCR) y de ADA 2026 (Tablas 2.1 y 2.2) en los bordes. |
| A2 Exactitud | Sensibilidad y especificidad con IC 95 % contra una referencia **independiente** | **Solo es medible en diabetes.** Anemia y deficiencia de hierro se definen con los mismos valores que se usarían como referencia (ver sección 3). |
| A3 Aceptación | A1 al 100 %, ítems STARD reportados, resultados presentados como observados | **Cumple parcialmente.** A1 se cumple. STARD se reporta en la sección 5. No se afirma validez clínica. |
| A4 Ediciones médicas | Tasa y motivos, sin umbral | **No medible.** La corrección médica (T6) todavía no existe. |

## 3. Diabetes (A2 medible)

Índice: FPG ≥ 126 mg/dL con la regla del dominio (prueba única, sin HbA1c). Referencia: HbA1c ≥ 6,5 % del laboratorio. Son pruebas distintas, así que el índice no es la referencia.

| Ciclo | n (adultos ≥ 18 con FPG y HbA1c) | Positivos de referencia | Sensibilidad | Especificidad | TP / FN / FP / TN |
|---|---|---|---|---|---|
| 2017-2018 | 2 534 | 373 | 79,1 % (74,7 a 82,9) | 94,7 % (93,7 a 95,6) | 295 / 78 / 114 / 2 047 |
| 2021-2023 | 3 327 | 409 | 76,8 % (72,4 a 80,6) | 96,1 % (95,3 a 96,7) | 314 / 95 / 115 / 2 803 |

Lectura: con una sola glucosa en ayunas, el sistema detecta cerca de 4 de cada 5 casos de HbA1c diabética. Los falsos negativos son esperables, porque la HbA1c y la glucosa en ayunas miden cosas distintas, y la ADA lo reconoce (sección 2, concordancia incompleta).

Categorías de salida: 2017-2018 con PREDIABETES 1 208, SIN_EVIDENCIA 917 y PENDIENTE_CONFIRMACION 409; 2021-2023 con PREDIABETES 1 400, SIN_EVIDENCIA 1 498 y PENDIENTE_CONFIRMACION 429. No se produjo ninguna DIABETES_CONFIRMADA: cada ciclo de NHANES tiene un solo resultado por persona, así que no puede haber dos mediciones.

## 4. Anemia y deficiencia de hierro (descriptivo, sin A2)

**Por qué no hay exactitud:** la anemia se define por hemoglobina, y la deficiencia de hierro por ferritina. Compararlas con esos mismos valores daría una exactitud de 100 % por construcción. Sin una referencia independiente (por ejemplo, receptor de transferrina soluble o desenlace clínico), el proyecto no puede reportar sensibilidad ni especificidad de estos dos hallazgos.

**Distribución de salidas (muestra sana, PCR ≤ 5 mg/L):**

| Hallazgo | Ciclo | Salidas |
|---|---|---|
| Anemia | 2017-2018 (n = 5 192) | Sin anemia 3 066; leve 125; moderada 53; grave 6; no evaluable 1 942 |
| Anemia | 2021-2023 (n = 1 506) | Sin anemia 938; leve 97; moderada 52; grave 2; no evaluable 417 |
| Deficiencia de hierro (regla WHO 2020, Tabla 1) | 2017-2018 (n = 5 192) | Sin deficiencia 4 096; deficiencia 223; no evaluable 873 |
| Deficiencia de hierro (regla WHO 2020, Tabla 1) | 2021-2023 (n = 1 506) | Sin deficiencia 832; deficiencia 115; no evaluable 559 |

La regla de hierro se re-ejecutó después de la ampliación a la Tabla 1 de la OMS 2020 (aprobada en L13). Antes solo cubría niños de 6 a 59 meses y mujeres de 15 a 49 años, con 4 109 y 662 "no evaluable". Ahora cubre todas las edades, pero exige el estado de embarazo en mujeres de 10 a 59 años.

**Motivos de "no evaluable":**
- Hierro, 2017-2018 y 2021-2023: casi todos por **embarazo desconocido** (873 y 559). NHANES no registra el dato para una parte de las mujeres, así que la regla lo trata como no evaluable, como exige S3.
- Anemia, 2017-2018 y 2021-2023: mujeres sin estado de embarazo (966 y 417), y mayores de 65 años (976 en 2017-2018), fuera de la tabla de OMS 2024.
- La muestra de la evaluación excluye PCR > 5 mg/L, así que las categorías con inflamación no aparecen. Esto debe evaluarse aparte.

**Concordancia descriptiva (anemia × deficiencia de hierro, ambos evaluables):**
- 2017-2018: anémicos con deficiencia 83, anémicos sin deficiencia 81, no anémicos con deficiencia 109, no anémicos sin deficiencia 2 809.
- 2021-2023: anémicos con deficiencia 61, anémicos sin deficiencia 54, no anémicos con deficiencia 44, no anémicos sin deficiencia 766.

Es un dato de concordancia, no de exactitud.

## 5. Ítems STARD 2015 (checklist de reporte)

Fuente: Bossuyt et al., BMJ 2015;351:h5527, https://doi.org/10.1136/bmj.h5527.

- **Población y muestreo:** NHANES, muestra de adultos y niños con datos completos; criterios de inclusión y exclusión en la sección 1 y en el script.
- **Índice y referencia:** índice FPG ≥ 126 (dominio); referencia HbA1c ≥ 6,5 % (laboratorio), declaradas antes de evaluar.
- **Ejecución:** un solo resultado por persona; el índice se calcula con el código del dominio.
- **Resultados:** sensibilidad, especificidad, TP/FN/FP/TN e IC 95 % (sección 3).
- **Limitaciones:** ver sección 6.

## 6. Limitaciones

1. **Sin referencia independiente para anemia y hierro.** No se reporta exactitud para esos dos hallazgos.
2. **Estado de embarazo ausente** es la principal causa de "no evaluable" en mujeres. En un sistema real, esto obliga a registrar el dato.
3. **Edad en años × 12.** Los cortes pediátricos usan meses exactos; aquí se aproxima. Afecta a los límites 6, 12, 24 y 60 meses, que la Tabla 1 de la OMS 2020 usa para ferritina.
4. **Niños menores de 5 años** con ferritina y PCR son pocos en NHANES, así que el hierro pediátrico tiene poca potencia.
4b. **Estado de embarazo ausente en NHANES.** La regla de hierro exige ese dato en mujeres de 10 a 59 años. La mayoría de "no evaluable" en hierro viene de ahí, y no del corte.
5. **Altitud y tabaquismo** no se aplican en el dominio actual (S12 fuera de alcance). Los resultados no los incluyen.
6. **Diseño transversal y sin ponderar.** No es una estimación poblacional.
7. **Diabetes: referencia por HbA1c**, no por diagnóstico médico. La HbA1c puede ser inexacta en anemia o hemoglobinopatías (ADA 2026, rec. 2.4); esos casos no se excluyeron de la referencia.
8. **Sin validación clínica** por un profesional. Ninguna fila de conocimiento está validada para uso real.

## 7. Conclusión

- Las reglas cumplen A1: cada corte coincide con su tabla fuente, verificado por pruebas.
- La **diabetes** tiene una exactitud medible contra HbA1c: sensibilidad 77–79 % y especificidad 95–96 %, con intervalos reportados.
- **Anemia y deficiencia de hierro** no tienen exactitud medible con NHANES. Lo que sí muestran los datos es que la mayoría de casos "no evaluables" se deben a datos ausentes (sobre todo el estado de embarazo), algo que el sistema debe pedir al laboratorio.
- Con la Tabla 1 de la OMS 2020, la deficiencia de hierro se detecta en 223 personas (2017-2018) y 115 (2021-2023). Eso es un dato descriptivo de la muestra sana, no una prevalencia poblacional.
- Ningún resultado es validación clínica. Para uso real faltan validación médica, referencias independientes y el modo sombra (T8).
