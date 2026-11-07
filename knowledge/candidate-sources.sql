-- Knowledge base seed for the candidate evidence sources and NHANES reference drafts.
-- Target: SQL Server (local MedicalBooking today; Azure SQL when deployed).
-- Not a migration: run it after migration 0006 (sqlcmd -b -i candidate-sources.sql).
--
-- What it does, in order (all idempotent: each insert runs only when its key is absent):
--   1. conocimiento_fuentes: provenance rows for the candidate sources. The six
--      sources approved by the expert are active (vigente = 1); MSD-LAB-RANGES is
--      included because the expert approved the whole data set.
--   2. conocimiento_analitos: two real analytes (glucose in fasting plasma and blood
--      hemoglobin). codigo_loinc stays NULL: the synthetic analytes hold the LOINC
--      codes 2339-0 and 718-7 (unique index), and the codes are not reassigned here.
--   3. conocimiento_referencias: NHANES 2021-2023 (cycle L) reference intervals as
--      BORRADOR (draft, version 1). They are not visible to patients until a doctor
--      with conocimiento.validar publishes them.
--
--   4. tipos_prueba_laboratorio + conocimiento_pruebas_analitos: one test type per real analyte
--      (GLUC-AYUNO, HEMO-HGB), each with its analyte as mandatory. Without this link no result can
--      be entered for the real analytes and their references would never apply.
--
-- Method (reproducible from the CDC public files, see the source row URL):
--   Weighted 2.5th and 97.5th percentiles of NHANES 08/2021-08/2023 (cycle L).
--   Glucose: fasting subsample (GLU_L LBXGLU, weight WTSAF2YR), adults >= 18 years,
--     excluding diagnosed diabetes (DIQ010 = 1) and HbA1c >= 6.5 % (GHB_L LBXGH).
--     n = 2783 remain.
--   Hemoglobin: CBC_L LBXHGB, weight WTMEC2YR, no exclusions. Adults by sex; children
--     by age band and by sex only from 12 years (sex-specific bands from the same cycle).
--   Cross-check, cycle J (2017-2018, same method): glucose 84.0-125.0 mg/dL;
--     hemoglobin adult M 12.5-17.1, adult F 10.6-15.5, 1-5 years 10.8-13.9,
--     6-11 years 11.4-14.7, 12-17 years M 12.2-16.8 and F 10.4-14.8 g/dL.
--
-- These are population percentiles of a US sample, not diagnostic thresholds.
-- Clinician validation is required before any of them applies to a patient result.
-- Age bands are in days: 18 years = 6570, 6 years = 2190, 12 years = 4380.

SET NOCOUNT ON;
SET QUOTED_IDENTIFIER ON;  -- required for inserts into tables with filtered indexes
SET ANSI_NULLS ON;
GO

-- =====================================================================
-- 1. Sources
-- =====================================================================

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'NHANES')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000002', 'NHANES',
          N'National Health and Nutrition Examination Survey (NHANES)', N'CDC / NCHS (Estados Unidos)',
          N'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/GLU_L.xpt', NULL,
          N'2021-2023 (cycle L)', '2026-11-04T00:00:00', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'MIMIC-IV')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000003', 'MIMIC-IV',
          N'MIMIC-IV, deidentified EHR dataset (BIDMC)', N'PhysioNet / MIT LCP',
          N'https://doi.org/10.13026/kpb9-mt58', N'10.13026/kpb9-mt58', N'3.1',
          '2026-11-04T00:00:00', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'ADA-SOC')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000004', 'ADA-SOC',
          N'Standards of Care in Diabetes', N'American Diabetes Association',
          N'https://professional.diabetes.org/standards-of-care', NULL, 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'ABIM-LAB-2026')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_publicacion], [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000005', 'ABIM-LAB-2026',
          N'ABIM Laboratory Test Reference Ranges', N'American Board of Internal Medicine',
          N'https://www.abim.org/media/e2wdwdqu/laboratory-reference-ranges.pdf', NULL,
          N'January 2026', '2026-01-01', '2026-11-04T00:00:00', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'HL7-FHIR-R4-OBS')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000006', 'HL7-FHIR-R4-OBS',
          N'HL7 FHIR R4 Observation resource (referenceRange)', N'HL7 International',
          N'https://hl7.org/fhir/R4/observation.html', N'HL7 FHIR 4.0.1', N'4.0.1',
          '2026-11-04T00:00:00', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'MSD-LAB-RANGES')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000007', 'MSD-LAB-RANGES',
          N'Merck Manual Professional: Laboratory Reference Ranges', N'Merck & Co.',
          N'https://www.merckmanuals.com/professional/resources/normal-laboratory-values/laboratory-reference-ranges',
          NULL, '2026-11-04T00:00:00', 1);
GO

-- PubMed is not inserted here: the importer creates one row per cited article
-- with codigo 'PMID-<pmid>' (knowledge_import.py), so a generic row would not
-- follow that convention.

-- =====================================================================
-- 2. Analytes (real, without LOINC for now)
-- =====================================================================

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = 'a1000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_analitos] ([id], [codigo_loinc], [nombre], [descripcion], [unidad_canonica],
                                       [tipo_valor], [activo], [creado_en], [actualizado_en])
  VALUES ('a1000000-0000-4000-8000-000000000001', NULL, N'Glucosa en plasma en ayunas',
          N'Glucosa plasmatica medida en la muestra en ayunas.', 'mg/dL', 'NUMERICO', 1,
          SYSUTCDATETIME(), SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = 'a1000000-0000-4000-8000-000000000002')
  INSERT INTO [conocimiento_analitos] ([id], [codigo_loinc], [nombre], [descripcion], [unidad_canonica],
                                       [tipo_valor], [activo], [creado_en], [actualizado_en])
  VALUES ('a1000000-0000-4000-8000-000000000002', NULL, N'Hemoglobina en sangre total',
          N'Concentracion de hemoglobina en sangre total.', 'g/dL', 'NUMERICO', 1,
          SYSUTCDATETIME(), SYSUTCDATETIME());
GO

-- =====================================================================
-- 3. NHANES 2021-2023 reference drafts (BORRADOR, version 1)
-- Fuente: NHANES. Vigencia desde 2026-11-04 (fecha de carga); estado BORRADOR.
-- =====================================================================

-- Glucose, adults, fasting, both sexes
IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000001', 'a1000000-0000-4000-8000-000000000001', f.[id], 1, 'mg/dL',
         NULL, 6570, NULL, 'plasma', NULL, 'AYUNO', 82.0, 123.0, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

-- Hemoglobin, adults, by sex
IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000002')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000002', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'M', 6570, NULL, 'sangre total', NULL, NULL, 12.0, 17.0, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000003')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000003', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 6570, NULL, 'sangre total', NULL, NULL, 10.3, 15.2, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

-- Hemoglobin, children, age bands (both sexes) and sex-specific from 12 years
IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000004')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000004', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 365, 2189, 'sangre total', NULL, NULL, 10.6, 14.1, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000005')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000005', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 2190, 4379, 'sangre total', NULL, NULL, 11.2, 14.7, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000006')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000006', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'M', 4380, 6569, 'sangre total', NULL, NULL, 12.2, 16.6, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000007')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000007', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 4380, 6569, 'sangre total', NULL, NULL, 9.7, 14.7, NULL,
         '2026-11-04', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'NHANES';
GO

-- =====================================================================
-- 4. Test types for the real analytes
-- A result must belong to the test it is entered under, so an analyte with no test can never
-- receive a result and its references would never apply. The real analytes get their own test
-- types instead of joining the synthetic ones: a test completes only when every mandatory analyte
-- has a result, and mixing synthetic and real analytes would require both on every order.
-- =====================================================================

IF NOT EXISTS (SELECT 1 FROM [tipos_prueba_laboratorio] WHERE [codigo] = 'GLUC-AYUNO')
  INSERT INTO [tipos_prueba_laboratorio] ([id], [codigo], [nombre], [descripcion], [unidad_predeterminada], [activo])
  VALUES (NEWID(), 'GLUC-AYUNO', N'Glucosa en ayunas', N'Glucosa en plasma, muestra en ayunas.', N'mg/dL', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [tipos_prueba_laboratorio] WHERE [codigo] = 'HEMO-HGB')
  INSERT INTO [tipos_prueba_laboratorio] ([id], [codigo], [nombre], [descripcion], [unidad_predeterminada], [activo])
  VALUES (NEWID(), 'HEMO-HGB', N'Hemoglobina', N'Hemoglobina en sangre total.', N'g/dL', 1);
GO

INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id], [analito_id], [obligatorio], [orden_presentacion])
SELECT t.[id], 'a1000000-0000-4000-8000-000000000001', 1, 1
FROM [tipos_prueba_laboratorio] t
WHERE t.[codigo] = 'GLUC-AYUNO'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] p
                  WHERE p.[tipo_prueba_id] = t.[id] AND p.[analito_id] = 'a1000000-0000-4000-8000-000000000001');
GO

INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id], [analito_id], [obligatorio], [orden_presentacion])
SELECT t.[id], 'a1000000-0000-4000-8000-000000000002', 1, 1
FROM [tipos_prueba_laboratorio] t
WHERE t.[codigo] = 'HEMO-HGB'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] p
                  WHERE p.[tipo_prueba_id] = t.[id] AND p.[analito_id] = 'a1000000-0000-4000-8000-000000000002');
GO

-- =====================================================================
-- Verification (run after the inserts)
-- =====================================================================
-- SELECT codigo, vigente FROM conocimiento_fuentes ORDER BY codigo;
-- SELECT a.nombre, r.estado, r.sexo_aplicable, r.edad_min_dias, r.edad_max_dias,
--        r.referencia_min, r.referencia_max, r.unidad, r.contexto
--   FROM conocimiento_referencias r JOIN conocimiento_analitos a ON a.id = r.analito_id
--   WHERE r.id LIKE 'c1000000%' ORDER BY a.nombre, r.edad_min_dias;
