# Regla combinada (Hb + VCM + RDW) contra deficiencia de hierro en NHANES

**Estado:** resultado de prueba. Umbrales candidatos, no validados por un clínico.

Generado por `nhanes_anemia_regla_combinada.py` con el protocolo versión 2 (datos descargados el 2026-11-09). Umbrales: VCM < 80 fL, RDW > 14.5 %. Intervalos de Wilson al 95 %, sin ponderar.

## NHANES 2017-2018

| Paso | n |
|---|---|
| Participantes del ciclo | 9254 |
| Edad 18 a 65 | 4449 |
| Sin embarazo (RIDEXPRG 1 o 3) | 4305 |
| Con hemoglobina, ferritina, PCR, sTfR, VCM y RDW | 3835 |
| Con respuesta de tabaquismo | 3835 |
| PCR <= 5 mg/L | 2964 |

Composición: 1536 hombres, 1428 mujeres; edad 18 a 65 años.

Deficiencia de hierro (ferritina < 15 µg/L): 200 de 2964 (6,7 %).

| Hemoglobina | Regla | VP | FP | FN | VN | Sensibilidad | Especificidad | VPP | VPN |
|---|---|---|---|---|---|---|---|---|---|
| Sin ajuste | Base: Hb baja | 101 | 99 | 99 | 2665 | 50,5 % (43,6 a 57,4) | 96,4 % (95,7 a 97,0) | 50,5 % (43,6 a 57,4) | 96,4 % (95,7 a 97,0) |
| Sin ajuste | Hb baja Y VCM bajo | 68 | 30 | 132 | 2734 | 34,0 % (27,8 a 40,8) | 98,9 % (98,5 a 99,2) | 69,4 % (59,7 a 77,6) | 95,4 % (94,6 a 96,1) |
| Sin ajuste | Anemia y señal S20 (dominio, límite de prueba 80 fL) | 68 | 30 | 132 | 2734 | 34,0 % (27,8 a 40,8) | 98,9 % (98,5 a 99,2) | 69,4 % (59,7 a 77,6) | 95,4 % (94,6 a 96,1) |
| Sin ajuste | Hb baja Y RDW alto | 91 | 47 | 109 | 2717 | 45,5 % (38,7 a 52,4) | 98,3 % (97,7 a 98,7) | 65,9 % (57,7 a 73,3) | 96,1 % (95,4 a 96,8) |
| Sin ajuste | Hb baja Y (VCM bajo O RDW alto) | 93 | 54 | 107 | 2710 | 46,5 % (39,7 a 53,4) | 98,0 % (97,5 a 98,5) | 63,3 % (55,2 a 70,6) | 96,2 % (95,4 a 96,8) |
| Sin ajuste | Hb baja Y VCM bajo Y RDW alto | 66 | 23 | 134 | 2741 | 33,0 % (26,9 a 39,8) | 99,2 % (98,8 a 99,4) | 74,2 % (64,2 a 82,1) | 95,3 % (94,5 a 96,1) |
| Con tabaquismo | Base: Hb baja | 105 | 119 | 95 | 2645 | 52,5 % (45,6 a 59,3) | 95,7 % (94,9 a 96,4) | 46,9 % (40,4 a 53,4) | 96,5 % (95,8 a 97,2) |
| Con tabaquismo | Hb baja Y VCM bajo | 68 | 31 | 132 | 2733 | 34,0 % (27,8 a 40,8) | 98,9 % (98,4 a 99,2) | 68,7 % (59,0 a 77,0) | 95,4 % (94,6 a 96,1) |
| Con tabaquismo | Anemia y señal S20 (dominio, límite de prueba 80 fL) | 68 | 31 | 132 | 2733 | 34,0 % (27,8 a 40,8) | 98,9 % (98,4 a 99,2) | 68,7 % (59,0 a 77,0) | 95,4 % (94,6 a 96,1) |
| Con tabaquismo | Hb baja Y RDW alto | 95 | 49 | 105 | 2715 | 47,5 % (40,7 a 54,4) | 98,2 % (97,7 a 98,7) | 66,0 % (57,9 a 73,2) | 96,3 % (95,5 a 96,9) |
| Con tabaquismo | Hb baja Y (VCM bajo O RDW alto) | 97 | 57 | 103 | 2707 | 48,5 % (41,7 a 55,4) | 97,9 % (97,3 a 98,4) | 63,0 % (55,1 a 70,2) | 96,3 % (95,6 a 97,0) |
| Con tabaquismo | Hb baja Y VCM bajo Y RDW alto | 66 | 23 | 134 | 2741 | 33,0 % (26,9 a 39,8) | 99,2 % (98,8 a 99,4) | 74,2 % (64,2 a 82,1) | 95,3 % (94,5 a 96,1) |

## NHANES 2021-2023

| Paso | n |
|---|---|
| Participantes del ciclo | 11933 |
| Edad 18 a 65 | 5746 |
| Sin embarazo (RIDEXPRG 1 o 3) | 5308 |
| Con hemoglobina, ferritina, PCR, sTfR, VCM y RDW | 1271 |
| Con respuesta de tabaquismo | 1268 |
| PCR <= 5 mg/L | 929 |

Composición: 929 mujeres; edad 18 a 49 años.

Deficiencia de hierro (ferritina < 15 µg/L): 141 de 929 (15,2 %).

| Hemoglobina | Regla | VP | FP | FN | VN | Sensibilidad | Especificidad | VPP | VPN |
|---|---|---|---|---|---|---|---|---|---|
| Sin ajuste | Base: Hb baja | 80 | 56 | 61 | 732 | 56,7 % (48,5 a 64,6) | 92,9 % (90,9 a 94,5) | 58,8 % (50,4 a 66,7) | 92,3 % (90,2 a 94,0) |
| Sin ajuste | Hb baja Y VCM bajo | 48 | 13 | 93 | 775 | 34,0 % (26,7 a 42,2) | 98,4 % (97,2 a 99,0) | 78,7 % (66,9 a 87,1) | 89,3 % (87,1 a 91,2) |
| Sin ajuste | Anemia y señal S20 (dominio, límite de prueba 80 fL) | 48 | 13 | 93 | 775 | 34,0 % (26,7 a 42,2) | 98,4 % (97,2 a 99,0) | 78,7 % (66,9 a 87,1) | 89,3 % (87,1 a 91,2) |
| Sin ajuste | Hb baja Y RDW alto | 67 | 22 | 74 | 766 | 47,5 % (39,5 a 55,7) | 97,2 % (95,8 a 98,1) | 75,3 % (65,4 a 83,1) | 91,2 % (89,1 a 92,9) |
| Sin ajuste | Hb baja Y (VCM bajo O RDW alto) | 68 | 26 | 73 | 762 | 48,2 % (40,1 a 56,4) | 96,7 % (95,2 a 97,7) | 72,3 % (62,6 a 80,4) | 91,3 % (89,1 a 93,0) |
| Sin ajuste | Hb baja Y VCM bajo Y RDW alto | 47 | 9 | 94 | 779 | 33,3 % (26,1 a 41,5) | 98,9 % (97,8 a 99,4) | 83,9 % (72,2 a 91,3) | 89,2 % (87,0 a 91,1) |
| Con tabaquismo | Base: Hb baja | 80 | 59 | 61 | 729 | 56,7 % (48,5 a 64,6) | 92,5 % (90,5 a 94,2) | 57,6 % (49,2 a 65,5) | 92,3 % (90,2 a 93,9) |
| Con tabaquismo | Hb baja Y VCM bajo | 48 | 13 | 93 | 775 | 34,0 % (26,7 a 42,2) | 98,4 % (97,2 a 99,0) | 78,7 % (66,9 a 87,1) | 89,3 % (87,1 a 91,2) |
| Con tabaquismo | Anemia y señal S20 (dominio, límite de prueba 80 fL) | 48 | 13 | 93 | 775 | 34,0 % (26,7 a 42,2) | 98,4 % (97,2 a 99,0) | 78,7 % (66,9 a 87,1) | 89,3 % (87,1 a 91,2) |
| Con tabaquismo | Hb baja Y RDW alto | 67 | 24 | 74 | 764 | 47,5 % (39,5 a 55,7) | 97,0 % (95,5 a 97,9) | 73,6 % (63,7 a 81,6) | 91,2 % (89,1 a 92,9) |
| Con tabaquismo | Hb baja Y (VCM bajo O RDW alto) | 68 | 28 | 73 | 760 | 48,2 % (40,1 a 56,4) | 96,4 % (94,9 a 97,5) | 70,8 % (61,1 a 79,0) | 91,2 % (89,1 a 93,0) |
| Con tabaquismo | Hb baja Y VCM bajo Y RDW alto | 47 | 9 | 94 | 779 | 33,3 % (26,1 a 41,5) | 98,9 % (97,8 a 99,4) | 83,9 % (72,2 a 91,3) | 89,2 % (87,0 a 91,1) |

## NHANES ciclos combinados

| Paso | n |
|---|---|
| Suma de los ciclos | 3893 |

Composición: 1536 hombres, 2357 mujeres; edad 18 a 65 años.

Deficiencia de hierro (ferritina < 15 µg/L): 341 de 3893 (8,8 %).

| Hemoglobina | Regla | VP | FP | FN | VN | Sensibilidad | Especificidad | VPP | VPN |
|---|---|---|---|---|---|---|---|---|---|
| Sin ajuste | Base: Hb baja | 181 | 155 | 160 | 3397 | 53,1 % (47,8 a 58,3) | 95,6 % (94,9 a 96,3) | 53,9 % (48,5 a 59,1) | 95,5 % (94,8 a 96,1) |
| Sin ajuste | Hb baja Y VCM bajo | 116 | 43 | 225 | 3509 | 34,0 % (29,2 a 39,2) | 98,8 % (98,4 a 99,1) | 73,0 % (65,6 a 79,3) | 94,0 % (93,2 a 94,7) |
| Sin ajuste | Anemia y señal S20 (dominio, límite de prueba 80 fL) | 116 | 43 | 225 | 3509 | 34,0 % (29,2 a 39,2) | 98,8 % (98,4 a 99,1) | 73,0 % (65,6 a 79,3) | 94,0 % (93,2 a 94,7) |
| Sin ajuste | Hb baja Y RDW alto | 158 | 69 | 183 | 3483 | 46,3 % (41,1 a 51,6) | 98,1 % (97,5 a 98,5) | 69,6 % (63,3 a 75,2) | 95,0 % (94,3 a 95,7) |
| Sin ajuste | Hb baja Y (VCM bajo O RDW alto) | 161 | 80 | 180 | 3472 | 47,2 % (42,0 a 52,5) | 97,7 % (97,2 a 98,2) | 66,8 % (60,6 a 72,4) | 95,1 % (94,3 a 95,7) |
| Sin ajuste | Hb baja Y VCM bajo Y RDW alto | 113 | 32 | 228 | 3520 | 33,1 % (28,4 a 38,3) | 99,1 % (98,7 a 99,4) | 77,9 % (70,5 a 83,9) | 93,9 % (93,1 a 94,6) |
| Con tabaquismo | Base: Hb baja | 185 | 178 | 156 | 3374 | 54,3 % (48,9 a 59,5) | 95,0 % (94,2 a 95,7) | 51,0 % (45,8 a 56,1) | 95,6 % (94,9 a 96,2) |
| Con tabaquismo | Hb baja Y VCM bajo | 116 | 44 | 225 | 3508 | 34,0 % (29,2 a 39,2) | 98,8 % (98,3 a 99,1) | 72,5 % (65,1 a 78,8) | 94,0 % (93,2 a 94,7) |
| Con tabaquismo | Anemia y señal S20 (dominio, límite de prueba 80 fL) | 116 | 44 | 225 | 3508 | 34,0 % (29,2 a 39,2) | 98,8 % (98,3 a 99,1) | 72,5 % (65,1 a 78,8) | 94,0 % (93,2 a 94,7) |
| Con tabaquismo | Hb baja Y RDW alto | 162 | 73 | 179 | 3479 | 47,5 % (42,3 a 52,8) | 97,9 % (97,4 a 98,4) | 68,9 % (62,8 a 74,5) | 95,1 % (94,4 a 95,8) |
| Con tabaquismo | Hb baja Y (VCM bajo O RDW alto) | 165 | 85 | 176 | 3467 | 48,4 % (43,1 a 53,7) | 97,6 % (97,1 a 98,1) | 66,0 % (59,9 a 71,6) | 95,2 % (94,4 a 95,8) |
| Con tabaquismo | Hb baja Y VCM bajo Y RDW alto | 113 | 32 | 228 | 3520 | 33,1 % (28,4 a 38,3) | 99,1 % (98,7 a 99,4) | 77,9 % (70,5 a 83,9) | 93,9 % (93,1 a 94,6) |

Los ciclos no cubren la misma población (ver composición de cada uno); el combinado sirve para estrechar intervalos, no como estimación poblacional.
