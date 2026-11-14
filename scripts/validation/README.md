# Validación del conocimiento clínico contra datos públicos

Scripts que comparan las reglas del módulo de conocimiento con encuestas públicas.
Producen cifras de prueba; no validan conocimiento. La validación la registra un
clínico con `conocimiento.validar`.

## Regla de anemia contra deficiencia de hierro (NHANES)

```
python scripts/validation/nhanes_anemia_regla_combinada.py
```

Requiere `pandas`. Descarga los archivos a `scripts/validation/datos/` (no versionado),
comprueba su SHA-256 y escribe `resultados_regla_combinada.md` y
`knowledge/validation/nhanes_anemia_regla_combinada.csv`. La anemia y el ajuste de tabaquismo se
calculan con `domain/anemia.py` y `domain/hb_adjustments.py`, cargados por ruta.

### Protocolo de muestreo

La fuente única de los criterios de la muestra es `protocolo_muestreo.json`. Los cortes de
hemoglobina vienen del dominio; no hay umbrales repetidos en el script.

| Criterio | Valor | Motivo |
|---|---|---|
| Encuesta | NHANES 2017-2018 (J) y agosto 2021 a agosto 2023 (L) | Ciclos con ferritina, sTfR y PCR |
| Edad | 18 a 65 años | La OMS 2024 no cubre más de 65; el cuestionario de tabaquismo SMQ020 se aplica desde los 18 |
| Embarazo | Se excluye `RIDEXPRG` 1 (embarazada) y 3 (no determinado) | Los cortes del embarazo son otros |
| Inflamación | Se excluye PCR > 5 mg/L | La ferritina sube con la inflamación (muestra sana, OMS 2024) |
| Datos completos | Hemoglobina, ferritina, PCR, sTfR, VCM y RDW | Misma cohorte que los análisis de ferritina contra sTfR |
| Tabaquismo | `SMQ020` = 2 o `SMQ040` = 3 es no fumador, con ajuste 0 | Ver la advertencia siguiente |
| Referencia | Ferritina < 15 µg/L | Corte OMS de deficiencia de hierro en adultos sanos |
| Ponderación | Ninguna | Limitación declarada: no son estimaciones poblacionales |

### Advertencia sobre el filtro de tabaquismo

`SMQ040` ("¿fuma actualmente?") solo se pregunta a quien respondió `SMQ020` = 1 (fumó al
menos 100 cigarrillos). Exigir `SMQ040` con valor elimina a quienes nunca fumaron. La
primera versión de la prueba tenía ese filtro y analizó solo a personas que fumaron
alguna vez (n = 1 084 y n = 245, frente a 2 964 y 929 con el criterio actual).

### Limitaciones

- El ciclo L midió ferritina solo en mujeres de 18 a 49 años. Los dos ciclos no cubren
  la misma población y sus valores predictivos no son comparables entre sí.
- NHANES no registra la altitud de residencia: el ajuste por altitud no se prueba aquí.
- La referencia es un marcador de hierro, no un diagnóstico clínico. Una anemia de otra
  causa cuenta como falso positivo.
- Los umbrales VCM < 80 fL y RDW > 14,5 % son valores de prueba. La guía BSH (Fletcher A, et al. Br J Haematol 2022;196:523-529, DOI 10.1111/bjh.17900)
  respalda solo un VCM por debajo del rango del laboratorio como sugerencia (grado 2C) y no da
  recomendación para el RDW. Ver `docs/evaluacion-hallazgos-clinicos.md`, sección 4c.

### Si el hash no coincide

El CDC puede republicar un archivo. El script se detiene en ese caso. Hay que revisar las
notas de cambio del archivo, volver a ejecutar y, si el cambio se acepta, subir la
`version` y la `fecha_descarga` del protocolo junto con el hash nuevo.
