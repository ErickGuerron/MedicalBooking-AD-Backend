-- Knowledge base seed for the candidate evidence sources and NHANES reference drafts.
-- Target: SQL Server (local MedicalBooking today; Azure SQL when deployed).
-- Not a migration: run it after migration 0006 (sqlcmd -b -i candidate-sources.sql).
--
-- What it does, in order (all idempotent: each insert runs only when its key is absent):
--   1. conocimiento_fuentes: provenance rows for the candidate sources. The six
--      sources approved by the expert are active (vigente = 1); MSD-LAB-RANGES is
--      included because the expert approved the whole data set.
--      VERIFICATION STATUS (audit of the knowledge base, not a change of vigente):
--        ADA-SOC          verified: Standards of Care 2026, section 2 (DOI 10.2337/dc26-S002).
--        WHO-ANEMIA       superseded by WHO-ANEMIA-2024; its rows are RETIRADA.
--        WHO-ANEMIA-2024  verified against Table 2 (9 of 9 values match).
--        NHANES           real population data (CDC files); population percentiles, not criteria.
--        MIMIC-IV         test data only (open demo or credentialed); not a source of criteria.
--        MSD-LAB-RANGES   NOT verified and NOT used by any reference or rule.
--        ABIM-LAB-2026    NOT verified (PDF text not extracted) and NOT used.
--        HL7-FHIR-R4-OBS  import format, not a source of values; NOT used.
--        FUENTE-SINTETICA-E5 synthetic, applies only to synthetic analytes.
--      Decision (user): MSD, ABIM and HL7 are set to vigente = 0 because their ranges are not
--      verified. They are kept as history; no reference depends on them. Validation of a reference
--      requires vigente = 1 on its source, so these sources cannot back a validated reference.
UPDATE [conocimiento_fuentes]
   SET [vigente] = 0
 WHERE [codigo] IN ('MSD-LAB-RANGES', 'ABIM-LAB-2026', 'HL7-FHIR-R4-OBS') AND [vigente] = 1;
GO
--   2. conocimiento_analitos: two real analytes (glucose in fasting plasma and blood
--      hemoglobin). codigo_loinc stays NULL: the synthetic analytes hold the LOINC
--      codes 2339-0 and 718-7 (unique index), and the codes are not reassigned here.
--   3. conocimiento_referencias: two kinds of draft (BORRADOR, version 1), none visible to
--      patients until a doctor with conocimiento.validar publishes it:
--      3a. Diagnostic criteria (preferred for classifying a patient result): ADA fasting glucose
--          (section 3b, contexto AYUNO) and WHO 2011 hemoglobin cut-offs for anaemia (section 3b).
--      3b. NHANES 2021-2023 (cycle L) population reference intervals (section 3c). Kept as
--          evidence and cross-check; they describe a population, not a diagnosis.
--      Rows of the same analyte, sex, age and context overlap. The API accepts only one of
--      them as VALIDADA (409 on the second), so the clinician chooses which applies.
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
-- Section 3c rows are population percentiles of a US sample, not diagnostic thresholds.
-- Section 3b rows are guideline criteria (ADA, WHO), also drafts until a clinician validates them.
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

-- WHO-ANEMIA: added after the expert list. It is the diagnostic criterion for anaemia
-- (hemoglobin cut-offs by age and sex), which population percentiles cannot provide.
IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'WHO-ANEMIA')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_publicacion], [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000008', 'WHO-ANEMIA',
          N'Haemoglobin concentrations for the diagnosis of anaemia and assessment of severity',
          N'World Health Organization',
          N'https://www.who.int/publications/i/item/WHO-NMH-NHD-MNM-11.1',
          N'WHO/NMH/NHD/MNM/11.1', N'2011', '2011-05-31', '2026-11-07T00:00:00', 1);
GO

-- WHO-ANEMIA-2024: current WHO guideline (5 March 2024, ISBN 978-92-4-008854-2, PMID 38530913).
-- It supersedes the 2011 cut-offs for children 6-23 and 24-59 months and adds pregnancy by trimester.
IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [codigo] = 'WHO-ANEMIA-2024')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_publicacion], [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000009', 'WHO-ANEMIA-2024',
          N'Guideline on haemoglobin cutoffs to define anaemia in individuals and populations',
          N'World Health Organization',
          N'https://www.who.int/publications/i/item/9789240088542',
          N'ISBN 978-92-4-008854-2', N'2024', '2024-03-05', '2026-11-07T00:00:00', 1);
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
-- 3b. Diagnostic criteria (BORRADOR, version 1). Preferred for classifying a patient result.
--
-- Glucose, ADA Standards of Care 2026, section 2 (Diabetes Care 2026;49 Suppl 1:S27-S49,
-- DOI 10.2337/dc26-S002, PMID 41358893). Table 2.2: IFG = FPG 100-125 mg/dL. Table 2.1: diabetes =
-- FPG >= 126 mg/dL, fasting = no caloric intake for >= 8 h, and the diagnosis needs two abnormal results
-- (the same test twice or two tests). A1C is less reliable with anaemia (section 2.4): use plasma glucose.
-- Glucose, ADA Standards of Care, fasting plasma glucose: normal < 100 mg/dL.
--   The model stores the normal band only. The guideline gives no lower limit (NULL), and
--   prediabetes (100-125) and diabetes (>= 126) are not separate rows: any value above 99.999
--   is returned as ALTO, so 100 mg/dL is flagged (correct: it is prediabetes). Labs report
--   glucose with at most one decimal, so the gap between 99.999 and 100 is never reported.
--   Non-fasting values are never classified by this row (contexto AYUNO must be known).
--   The adult band starts at 18 years (6570 days). Children are not covered by this row.
--
-- Hemoglobin, WHO 2011 cut-offs for anaemia (g/dL). Below the limit = BAJO (anaemia).
--   Age bands in days, approximated from years (6 months = 183, 5 years = 1826, 12 years = 4380,
--   15 years = 5479). contexto is NULL, so the rule applies without knowing sample context.
--   Pregnancy is not captured: WHO uses 11.0 g/dL for pregnant women, so the women's row
--   (12.0) would flag them BAJO when they are not pregnancy-adjusted. The clinician must decide
--   a pregnancy reference (contexto) before relying on the women's band.
--   VERIFICATION STATUS: checked against WHO/NMH/NHD/MNM/11.1, Table 1 (non-anaemia limits, g/L):
--     6-59 months 110 (row 009), 5-11 years 115 (row 010), 12-14 years 120 (row 011),
--     non-pregnant women 15+ 120 (row 012), men 15+ 130 (row 013). All five match.
--     Not modelled: pregnant women 110 (no pregnancy context). Rows 012 and 013 with contexto
--       NULL would also apply to pregnant women, so a pregnant woman with 11.5 g/dL is flagged BAJO
--       although she is not anaemic by the pregnancy cut-off. Do not validate 012 without a
--       pregnancy context.
--     Not modelled: severity bands (mild, moderate, severe) and altitude and smoking adjustments
--       (Tables 2 and 3). A smoker's measured value is corrected by -3 to -7 g/L before comparing.
--     Gap: Table 1 starts at 6 months. The document gives no cut-off for 0-5 months.
--
-- 3b.1 Glucose, adults, fasting (ADA)
-- The source row ADA-SOC was seeded without identifiers; fill them once (only when still empty).
UPDATE [conocimiento_fuentes]
   SET [identificador_publicacion] = N'10.2337/dc26-S002',
       [version_publicacion] = N'2026 (Diabetes Care 2026;49 Suppl 1:S27-S49, sec. 2)'
 WHERE [codigo] = 'ADA-SOC' AND [identificador_publicacion] IS NULL;
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000008')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000008', 'a1000000-0000-4000-8000-000000000001', f.[id], 1, 'mg/dL',
         NULL, 6570, NULL, 'plasma', NULL, 'AYUNO', NULL, 99.999, NULL,
         '2026-11-07', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'ADA-SOC';
GO

-- 3b.2 Hemoglobin, WHO anaemia cut-offs (lower limit only; no upper limit)
IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000009')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000009', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 183, 1825, 'sangre total', NULL, NULL, 11.0, NULL, NULL,
         '2011-05-31', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000010')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000010', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 1826, 4379, 'sangre total', NULL, NULL, 11.5, NULL, NULL,
         '2011-05-31', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000011')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000011', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 4380, 5478, 'sangre total', NULL, NULL, 12.0, NULL, NULL,
         '2011-05-31', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000012')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000012', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 5479, NULL, 'sangre total', NULL, NULL, 12.0, NULL, NULL,
         '2011-05-31', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000013')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000013', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'M', 5479, NULL, 'sangre total', NULL, NULL, 13.0, NULL, NULL,
         '2011-05-31', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA';
GO

-- 3c. NHANES 2021-2023 reference drafts (BORRADOR, version 1): population reference, evidence only
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
-- 3d. WHO 2024 haemoglobin cut-offs (BORRADOR, version 1). Supersede the 2011 drafts 3b.2,
-- which are set to RETIRADA below (kept as history, never validated).
-- Source: WHO 2024, Table 2 (5th percentile), converted from g/L to g/dL. No adjustment applied.
-- Age bands in days, approximated from years: 6 months = 183, 24 months = 730, 5 years = 1826,
--   12 years = 4380, 15 years = 5479, 65 years = 23743.
-- Adults over 65 are outside the guideline: the bands stop at 65 years, so they are NO_EVALUABLE.
-- Pregnancy rows use contexto EMBARAZO-T1/T2/T3; the laboratory must supply the trimester.
-- PREGNANCY DESIGN: the non-pregnant women row (018) uses contexto NO-EMBARAZO, not NULL. A NULL
--   context is a wildcard and would overlap the trimester rows. With NO-EMBARAZO, a woman whose
--   pregnancy status is unknown gets NO_EVALUABLE (no false classification), a non-pregnant woman
--   is classified by 018, and a pregnant woman with a trimester is classified by 020-022 only.
--   Men and children keep contexto NULL.
-- NOT MODELLED: severity bands (WHO Table 3) and smoking and altitude adjustments (Tables 4 and 5).
--   Infants under 6 months have no cut-off in this guideline (research gap).

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000014')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000014', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 183, 729, 'sangre total', NULL, NULL, 10.5, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000015')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000015', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 730, 1825, 'sangre total', NULL, NULL, 11.0, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000016')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000016', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 1826, 4379, 'sangre total', NULL, NULL, 11.5, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000017')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000017', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         NULL, 4380, 5478, 'sangre total', NULL, NULL, 12.0, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000018')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000018', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 5479, 23743, 'sangre total', NULL, 'NO-EMBARAZO', 12.0, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

-- Existing databases loaded the row with contexto NULL: set NO-EMBARAZO on drafts only.
UPDATE [conocimiento_referencias]
   SET [contexto] = 'NO-EMBARAZO'
 WHERE [id] = 'c1000000-0000-4000-8000-000000000018' AND [estado] = 'BORRADOR' AND [contexto] IS NULL;
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000019')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000019', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'M', 5479, 23743, 'sangre total', NULL, NULL, 13.0, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000020')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000020', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 5479, 23743, 'sangre total', NULL, 'EMBARAZO-T1', 11.0, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000021')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000021', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 5479, 23743, 'sangre total', NULL, 'EMBARAZO-T2', 10.5, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c1000000-0000-4000-8000-000000000022')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [sexo_aplicable], [edad_min_dias], [edad_max_dias],
                                          [tipo_muestra], [metodo_analitico], [contexto],
                                          [referencia_min], [referencia_max], [valor_texto_esperado],
                                          [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
  SELECT 'c1000000-0000-4000-8000-000000000022', 'a1000000-0000-4000-8000-000000000002', f.[id], 1, 'g/dL',
         'F', 5479, 23743, 'sangre total', NULL, 'EMBARAZO-T3', 11.0, NULL, NULL,
         '2024-03-05', NULL, 'BORRADOR', NULL, NULL
  FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'WHO-ANEMIA-2024';
GO

-- The 2011 drafts (3b.2) are superseded by the rows above. Retire them (never validated; kept as history).
UPDATE [conocimiento_referencias]
   SET [estado] = 'RETIRADA'
 WHERE [id] IN ('c1000000-0000-4000-8000-000000000009', 'c1000000-0000-4000-8000-000000000010',
                'c1000000-0000-4000-8000-000000000011', 'c1000000-0000-4000-8000-000000000012',
                'c1000000-0000-4000-8000-000000000013')
   AND [estado] = 'BORRADOR';
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
-- 5. Declarative rules for fasting glucose (ADA cut-offs), BORRADOR: not evaluated until a doctor
-- validates them (the engine reads only VALIDADA rules). Findings are preliminary, never a diagnosis.
--   GLU-AYUNO-ALTERADA-ADA: fasting glucose >= 100 mg/dL (impaired fasting glucose, ADA).
--   GLU-AYUNO-DIABETES-ADA: fasting glucose >= 126 mg/dL (ADA criterion; ADA requires confirmation
--     by a second test or another criterion, which the rule message states).
-- The criteria carry contexto AYUNO: a result without a known fasting context never satisfies them.

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas] WHERE [id] = 'e1000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_reglas] ([id], [codigo], [version], [nombre], [descripcion], [tipo],
                                     [expresion_logica], [mensaje_propuesto], [prioridad], [estado],
                                     [vigente_desde], [aprobado_por], [aprobado_en], [creado_en])
  VALUES ('e1000000-0000-4000-8000-000000000001', 'GLU-AYUNO-ALTERADA-ADA', 1,
          N'Glucosa en ayunas alterada (ADA)',
          N'Glucosa en ayunas >= 100 mg/dL. Criterio ADA de glucosa alterada en ayunas (prediabetes).',
          'HALLAZGO', 'TODOS',
          N'Glucosa en ayunas igual o mayor a 100 mg/dL: corresponde a glucosa alterada en ayunas segun el criterio ADA. Requiere revision medica; no es un diagnostico.',
          'INFORMATIVA', 'BORRADOR', '2026-11-07', NULL, NULL, SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas] WHERE [id] = 'e1000000-0000-4000-8000-000000000002')
  INSERT INTO [conocimiento_reglas] ([id], [codigo], [version], [nombre], [descripcion], [tipo],
                                     [expresion_logica], [mensaje_propuesto], [prioridad], [estado],
                                     [vigente_desde], [aprobado_por], [aprobado_en], [creado_en])
  VALUES ('e1000000-0000-4000-8000-000000000002', 'GLU-AYUNO-DIABETES-ADA', 1,
          N'Glucosa en ayunas en rango de diabetes (ADA)',
          N'Glucosa en ayunas >= 126 mg/dL. Un criterio ADA de diabetes; requiere confirmacion.',
          'ALERTA', 'TODOS',
          N'Glucosa en ayunas igual o mayor a 126 mg/dL: cumple un criterio ADA de diabetes, que debe confirmarse con una segunda medicion u otro criterio diagnostico. Requiere revision medica; no es un diagnostico.',
          'INFORMATIVA', 'BORRADOR', '2026-11-07', NULL, NULL, SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] WHERE [id] = 'b1000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_reglas_criterios] ([id], [regla_id], [analito_id], [operador],
                                               [umbral_numerico], [unidad], [contexto], [orden_criterio])
  VALUES ('b1000000-0000-4000-8000-000000000001', 'e1000000-0000-4000-8000-000000000001',
          'a1000000-0000-4000-8000-000000000001', 'GE', 100.0, 'mg/dL', 'AYUNO', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] WHERE [id] = 'b1000000-0000-4000-8000-000000000002')
  INSERT INTO [conocimiento_reglas_criterios] ([id], [regla_id], [analito_id], [operador],
                                               [umbral_numerico], [unidad], [contexto], [orden_criterio])
  VALUES ('b1000000-0000-4000-8000-000000000002', 'e1000000-0000-4000-8000-000000000002',
          'a1000000-0000-4000-8000-000000000001', 'GE', 126.0, 'mg/dL', 'AYUNO', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] WHERE [regla_id] = 'e1000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_reglas_fuentes] ([regla_id], [fuente_id])
  SELECT 'e1000000-0000-4000-8000-000000000001', f.[id] FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'ADA-SOC';
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] WHERE [regla_id] = 'e1000000-0000-4000-8000-000000000002')
  INSERT INTO [conocimiento_reglas_fuentes] ([regla_id], [fuente_id])
  SELECT 'e1000000-0000-4000-8000-000000000002', f.[id] FROM [conocimiento_fuentes] f WHERE f.[codigo] = 'ADA-SOC';
GO

-- =====================================================================
-- Verification (run after the inserts)
-- =====================================================================
-- SELECT codigo, vigente FROM conocimiento_fuentes ORDER BY codigo;
-- SELECT a.nombre, r.estado, r.sexo_aplicable, r.edad_min_dias, r.edad_max_dias,
--        r.referencia_min, r.referencia_max, r.unidad, r.contexto
--   FROM conocimiento_referencias r JOIN conocimiento_analitos a ON a.id = r.analito_id
--   WHERE r.id LIKE 'c1000000%' ORDER BY a.nombre, r.edad_min_dias;
