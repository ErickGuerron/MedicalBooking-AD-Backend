-- =====================================================================
-- Sync of the clinical knowledge base: local SQL Server (MedicalBooking)
--                                   -> Azure SQL (Medilo)
-- Generated 2026-11-22 from a live comparison of both databases.
--
-- Direction : local -> Azure. Additive and idempotent (INSERT ... WHERE NOT EXISTS).
-- Never updates or deletes existing Azure rows. Azure-only rows are kept
-- (cholesterol/triglycerides analytes, the Azure ids of the *-SINT test types).
-- Run on  : Azure SQL database Medilo (sqlcmd -b -i sync-local-to-azure.sql).
-- Safety  : one transaction. Section 3 validates the ranges; the script COMMITs only
--           when there are zero ERROR rows, otherwise it ROLLBACKs.
-- =====================================================================
SET NOCOUNT ON;
SET XACT_ABORT ON;

-- ---------------------------------------------------------------------
-- 0. Reviewer mapping
-- Local reviewers that do not exist in Azure usuarios are remapped to the Azure
-- reviewer below (validado_por / revisado_por / aprobado_por). Change the email if
-- another clinician must own those validations.
--   local 3173F544-B1DF-4992-AF22-0263C43744FA (medico1@synthetic.test) -> Azure reviewer
DECLARE @reviewer_email nvarchar(255) = N'medico1@demo.test';
DECLARE @reviewer uniqueidentifier = (SELECT TOP 1 [id] FROM [usuarios] WHERE [email] = @reviewer_email);
IF @reviewer IS NULL THROW 50001, 'Reviewer email not found in Azure usuarios. Edit @reviewer_email.', 1;

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------
-- 1. Preconditions (conflicts that would make the inserts fail or duplicate data)
-- ---------------------------------------------------------------------
IF EXISTS (SELECT 1 FROM [conocimiento_fuentes] f WHERE EXISTS (SELECT 1 FROM (VALUES
  (N'F0000000-0000-4000-8000-000000000001', N'FUENTE-SINTETICA-E5'),
  (N'F0000000-0000-4000-8000-000000000002', N'NHANES'),
  (N'F0000000-0000-4000-8000-000000000003', N'MIMIC-IV'),
  (N'F0000000-0000-4000-8000-000000000004', N'ADA-SOC'),
  (N'F0000000-0000-4000-8000-000000000005', N'ABIM-LAB-2026'),
  (N'F0000000-0000-4000-8000-000000000006', N'HL7-FHIR-R4-OBS'),
  (N'F0000000-0000-4000-8000-000000000007', N'MSD-LAB-RANGES'),
  (N'F0000000-0000-4000-8000-000000000008', N'WHO-ANEMIA'),
  (N'F0000000-0000-4000-8000-000000000009', N'WHO-ANEMIA-2024'),
  (N'F0000000-0000-4000-8000-00000000000A', N'WHO-FERRITIN-2020'),
  (N'F0000000-0000-4000-8000-00000000000B', N'MEDLINEPLUS-CSC-2024'),
  (N'9824CE2F-37A9-43CA-B027-599A91EF67F8', N'MEDLINEPLUS')
) v([id],[codigo]) WHERE v.[codigo] = f.[codigo] AND v.[id] <> f.[id]))
  THROW 50002, 'conocimiento_fuentes: a codigo already exists in Azure with a different id.', 1;

-- ---------------------------------------------------------------------
-- 2. Data (dependency order)
-- ---------------------------------------------------------------------

-- conocimiento_fuentes
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000002', N'NHANES', N'National Health and Nutrition Examination Survey (NHANES)', N'CDC / NCHS (Estados Unidos)', N'https://wwwn.cdc.gov/Nchs/Data/Nhanes/Public/2021/DataFiles/GLU_L.xpt', NULL, N'2021-2023 (cycle L)', NULL, CAST('2026-11-04 00:00:00' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000002');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000003', N'MIMIC-IV', N'MIMIC-IV, deidentified EHR dataset (BIDMC)', N'PhysioNet / MIT LCP', N'https://doi.org/10.13026/kpb9-mt58', N'10.13026/kpb9-mt58', N'3.1', NULL, CAST('2026-11-04 00:00:00' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000003');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000004', N'ADA-SOC', N'Standards of Care in Diabetes', N'American Diabetes Association', N'https://professional.diabetes.org/standards-of-care', N'10.2337/dc26-S002', N'2026 (Diabetes Care 2026;49 Suppl 1:S27-S49, sec. 2)', NULL, NULL, NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000004');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000005', N'ABIM-LAB-2026', N'ABIM Laboratory Test Reference Ranges', N'American Board of Internal Medicine', N'https://www.abim.org/media/e2wdwdqu/laboratory-reference-ranges.pdf', NULL, N'January 2026', CAST('2026-01-01' AS date), CAST('2026-11-04 00:00:00' AS datetime2), NULL, 0
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000005');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000006', N'HL7-FHIR-R4-OBS', N'HL7 FHIR R4 Observation resource (referenceRange)', N'HL7 International', N'https://hl7.org/fhir/R4/observation.html', N'HL7 FHIR 4.0.1', N'4.0.1', NULL, CAST('2026-11-04 00:00:00' AS datetime2), NULL, 0
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000006');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000007', N'MSD-LAB-RANGES', N'Merck Manual Professional: Laboratory Reference Ranges', N'Merck & Co.', N'https://www.merckmanuals.com/professional/resources/normal-laboratory-values/laboratory-reference-ranges', NULL, NULL, NULL, CAST('2026-11-04 00:00:00' AS datetime2), NULL, 0
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000007');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000008', N'WHO-ANEMIA', N'Haemoglobin concentrations for the diagnosis of anaemia and assessment of severity', N'World Health Organization', N'https://www.who.int/publications/i/item/WHO-NMH-NHD-MNM-11.1', N'WHO/NMH/NHD/MNM/11.1', N'2011', CAST('2011-05-31' AS date), CAST('2026-11-07 00:00:00' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000008');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-000000000009', N'WHO-ANEMIA-2024', N'Guideline on haemoglobin cutoffs to define anaemia in individuals and populations', N'World Health Organization', N'https://www.who.int/publications/i/item/9789240088542', N'ISBN 978-92-4-008854-2', N'2024', CAST('2024-03-05' AS date), CAST('2026-11-07 00:00:00' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-000000000009');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'9824CE2F-37A9-43CA-B027-599A91EF67F8', N'MEDLINEPLUS', N'MedlinePlus Connect', N'U.S. National Library of Medicine', N'https://medlineplus.gov/', N'MedlinePlus Connect', NULL, NULL, CAST('2026-11-02 21:34:46.157962' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'9824CE2F-37A9-43CA-B027-599A91EF67F8');
INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url], [identificador_publicacion], [version_publicacion], [fecha_publicacion], [fecha_consulta], [licencia], [vigente])
SELECT N'F0000000-0000-4000-8000-00000000000B', N'MEDLINEPLUS-CSC-2024', N'Conteo sanguineo completo (CSC) prueba de sangre', N'U.S. National Library of Medicine, MedlinePlus (version en espanol)', N'https://medlineplus.gov/spanish/ency/article/003642.htm', NULL, N'Revision 2024-10-14', CAST('2024-10-14' AS date), CAST('2026-11-09 00:00:00' AS datetime2), NULL, 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = N'F0000000-0000-4000-8000-00000000000B');

-- conocimiento_analitos
INSERT INTO [conocimiento_analitos] ([id], [codigo_loinc], [nombre], [descripcion], [unidad_canonica], [tipo_valor], [activo], [creado_en], [actualizado_en])
SELECT N'A1000000-0000-4000-8000-000000000001', N'1558-6', N'Glucosa en plasma en ayunas', N'Glucosa plasmatica medida en la muestra en ayunas.', N'mg/dL', N'NUMERICO', 1, CAST('2026-11-04 15:15:38.991274' AS datetime2), CAST('2026-11-04 15:15:38.991274' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = N'A1000000-0000-4000-8000-000000000001');
INSERT INTO [conocimiento_analitos] ([id], [codigo_loinc], [nombre], [descripcion], [unidad_canonica], [tipo_valor], [activo], [creado_en], [actualizado_en])
SELECT N'A1000000-0000-4000-8000-000000000002', N'718-7', N'Hemoglobina en sangre total', N'Concentracion de hemoglobina en sangre total.', N'g/dL', N'NUMERICO', 1, CAST('2026-11-04 15:15:38.995796' AS datetime2), CAST('2026-11-04 15:15:38.995796' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = N'A1000000-0000-4000-8000-000000000002');
INSERT INTO [conocimiento_analitos] ([id], [codigo_loinc], [nombre], [descripcion], [unidad_canonica], [tipo_valor], [activo], [creado_en], [actualizado_en])
SELECT N'7B915A90-F785-40A3-B519-3AE096539531', N'2276-4', N'Ferritina serica (WHO 2020)', NULL, N'ug/L', N'NUMERICO', 1, CAST('2026-11-08 01:54:27.677655' AS datetime2), CAST('2026-11-08 01:54:27.677655' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = N'7B915A90-F785-40A3-B519-3AE096539531');

-- tipos_prueba_laboratorio (matched by codigo; Azure ids win for existing codes)
INSERT INTO [tipos_prueba_laboratorio] ([id], [codigo], [nombre], [descripcion], [unidad_predeterminada], [activo])
SELECT N'38A6BEEB-0A5B-44B1-A82A-8961F1A5BB3C', N'GLUC-AYUNO', N'Glucosa en ayunas', N'Glucosa en plasma, muestra en ayunas.', N'mg/dL', 1
WHERE NOT EXISTS (SELECT 1 FROM [tipos_prueba_laboratorio] WHERE [codigo] = N'GLUC-AYUNO');
INSERT INTO [tipos_prueba_laboratorio] ([id], [codigo], [nombre], [descripcion], [unidad_predeterminada], [activo])
SELECT N'C775538C-DB1B-4C35-951D-98F880760533', N'HEMO-HGB', N'Hemoglobina', N'Hemoglobina en sangre total.', N'g/dL', 1
WHERE NOT EXISTS (SELECT 1 FROM [tipos_prueba_laboratorio] WHERE [codigo] = N'HEMO-HGB');

-- conocimiento_pruebas_analitos (tipo resolved by codigo)
INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id],[analito_id],[obligatorio],[orden_presentacion])
SELECT t.[id], N'A1000000-0000-4000-8000-000000000001', 1, 1
FROM [tipos_prueba_laboratorio] t WHERE t.[codigo] = N'GLUC-AYUNO'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] x WHERE x.[tipo_prueba_id] = t.[id] AND x.[analito_id] = N'A1000000-0000-4000-8000-000000000001');
INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id],[analito_id],[obligatorio],[orden_presentacion])
SELECT t.[id], N'A3000000-0000-4000-8000-000000000001', 0, 99
FROM [tipos_prueba_laboratorio] t WHERE t.[codigo] = N'HEMO-HGB'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] x WHERE x.[tipo_prueba_id] = t.[id] AND x.[analito_id] = N'A3000000-0000-4000-8000-000000000001');
INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id],[analito_id],[obligatorio],[orden_presentacion])
SELECT t.[id], N'A1000000-0000-4000-8000-000000000002', 1, 1
FROM [tipos_prueba_laboratorio] t WHERE t.[codigo] = N'HEMO-HGB'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] x WHERE x.[tipo_prueba_id] = t.[id] AND x.[analito_id] = N'A1000000-0000-4000-8000-000000000002');

-- conocimiento_referencias
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000001', N'A1000000-0000-4000-8000-000000000001', N'F0000000-0000-4000-8000-000000000002', 1, N'mg/dL', NULL, 6570, NULL, N'plasma', NULL, N'AYUNO', 82.000000, 123.000000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000001');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000002', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000002', 1, N'g/dL', N'M', 6570, NULL, N'sangre total', NULL, NULL, 12.000000, 17.000000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000002');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000003', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000002', 1, N'g/dL', N'F', 6570, NULL, N'sangre total', NULL, NULL, 10.300000, 15.200000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000003');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000004', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000002', 1, N'g/dL', NULL, 365, 2189, N'sangre total', NULL, NULL, 10.600000, 14.100000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000004');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000005', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000002', 1, N'g/dL', NULL, 2190, 4379, N'sangre total', NULL, NULL, 11.200000, 14.700000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000005');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000006', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000002', 1, N'g/dL', N'M', 4380, 6569, N'sangre total', NULL, NULL, 12.200000, 16.600000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000006');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000007', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000002', 1, N'g/dL', N'F', 4380, 6569, N'sangre total', NULL, NULL, 9.700000, 14.700000, NULL, CAST('2026-11-04' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000007');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000008', N'A1000000-0000-4000-8000-000000000001', N'F0000000-0000-4000-8000-000000000004', 1, N'mg/dL', NULL, 6570, NULL, N'plasma', NULL, N'AYUNO', NULL, 99.999000, NULL, CAST('2026-11-07' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000008');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000009', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000008', 1, N'g/dL', NULL, 183, 1825, N'sangre total', NULL, NULL, 11.000000, NULL, NULL, CAST('2011-05-31' AS date), NULL, N'RETIRADA', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000009');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000010', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000008', 1, N'g/dL', NULL, 1826, 4379, N'sangre total', NULL, NULL, 11.500000, NULL, NULL, CAST('2011-05-31' AS date), NULL, N'RETIRADA', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000010');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000011', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000008', 1, N'g/dL', NULL, 4380, 5478, N'sangre total', NULL, NULL, 12.000000, NULL, NULL, CAST('2011-05-31' AS date), NULL, N'RETIRADA', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000011');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000012', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000008', 1, N'g/dL', N'F', 5479, NULL, N'sangre total', NULL, NULL, 12.000000, NULL, NULL, CAST('2011-05-31' AS date), NULL, N'RETIRADA', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000012');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000013', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000008', 1, N'g/dL', N'M', 5479, NULL, N'sangre total', NULL, NULL, 13.000000, NULL, NULL, CAST('2011-05-31' AS date), NULL, N'RETIRADA', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000013');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000014', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', NULL, 183, 729, N'sangre total', NULL, NULL, 10.500000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000014');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000015', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', NULL, 730, 1825, N'sangre total', NULL, NULL, 11.000000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000015');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000016', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', NULL, 1826, 4379, N'sangre total', NULL, NULL, 11.500000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000016');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000017', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', NULL, 4380, 5478, N'sangre total', NULL, NULL, 12.000000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000017');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000018', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', N'F', 5479, 23743, N'sangre total', NULL, N'NO-EMBARAZO', 12.000000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000018');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000019', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', N'M', 5479, 23743, N'sangre total', NULL, NULL, 13.000000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000019');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000020', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', N'F', 5479, 23743, N'sangre total', NULL, N'EMBARAZO-T1', 11.000000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000020');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000021', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', N'F', 5479, 23743, N'sangre total', NULL, N'EMBARAZO-T2', 10.500000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000021');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'C1000000-0000-4000-8000-000000000022', N'A1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000009', 1, N'g/dL', N'F', 5479, 23743, N'sangre total', NULL, N'EMBARAZO-T3', 11.000000, NULL, NULL, CAST('2024-03-05' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'C1000000-0000-4000-8000-000000000022');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'3F8EAE5E-DC40-4C96-A823-22AF57D7F4BA', N'7B915A90-F785-40A3-B519-3AE096539531', N'F0000000-0000-4000-8000-00000000000A', 1, N'ug/L', NULL, 1826, NULL, NULL, NULL, N'INFLAMACION', 70.000000, NULL, NULL, CAST('2020-01-01' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'3F8EAE5E-DC40-4C96-A823-22AF57D7F4BA');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'3C8E0B16-A122-4FE2-A3FF-58974901B5A2', N'A3000000-0000-4000-8000-000000000001', N'F0000000-0000-4000-8000-00000000000B', 1, N'fL', NULL, NULL, NULL, NULL, NULL, NULL, 80.000000, 100.000000, NULL, CAST('2024-10-14' AS date), NULL, N'VALIDADA', @reviewer, CAST('2026-11-10 03:22:32.542418' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'3C8E0B16-A122-4FE2-A3FF-58974901B5A2');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'758B68AA-397C-48B3-853A-C7BD79BDD5B5', N'7B915A90-F785-40A3-B519-3AE096539531', N'F0000000-0000-4000-8000-00000000000A', 1, N'ug/L', NULL, 1826, NULL, NULL, NULL, N'SIN_INFLAMACION', 15.000000, NULL, NULL, CAST('2020-01-01' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'758B68AA-397C-48B3-853A-C7BD79BDD5B5');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'BBE7229A-4F3C-4572-81B0-D6041E1252FE', N'7B915A90-F785-40A3-B519-3AE096539531', N'F0000000-0000-4000-8000-00000000000A', 1, N'ug/L', NULL, NULL, 1825, NULL, NULL, N'INFLAMACION', 30.000000, NULL, NULL, CAST('2020-01-01' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'BBE7229A-4F3C-4572-81B0-D6041E1252FE');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'A42858C6-F78E-457A-9E02-E23127950B97', N'7B915A90-F785-40A3-B519-3AE096539531', N'F0000000-0000-4000-8000-00000000000A', 1, N'ug/L', NULL, NULL, 1825, NULL, NULL, N'SIN_INFLAMACION', 12.000000, NULL, NULL, CAST('2020-01-01' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'A42858C6-F78E-457A-9E02-E23127950B97');
INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad], [sexo_aplicable], [edad_min_dias], [edad_max_dias], [tipo_muestra], [metodo_analitico], [contexto], [referencia_min], [referencia_max], [valor_texto_esperado], [fecha_inicio], [fecha_fin], [estado], [validado_por], [validado_en])
SELECT N'EE85FA06-70D7-4DF5-AA18-E4CFAAD3FC44', N'7B915A90-F785-40A3-B519-3AE096539531', N'F0000000-0000-4000-8000-00000000000A', 1, N'ug/L', NULL, 3652, 21914, NULL, NULL, N'EMBARAZO-T1', 15.000000, NULL, NULL, CAST('2020-01-01' AS date), NULL, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = N'EE85FA06-70D7-4DF5-AA18-E4CFAAD3FC44');

-- conocimiento_contenidos
INSERT INTO [conocimiento_contenidos] ([id], [analito_id], [fuente_id], [idioma], [tipo], [texto], [url_referencia], [version], [estado], [revisado_por], [revisado_en])
SELECT N'A5E09C70-841C-446D-BB55-3F40FC65D1C6', N'A0000000-0000-4000-8000-00000000B001', N'9824CE2F-37A9-43CA-B027-599A91EF67F8', N'es', N'EDUCACION', N'Su sangre contiene glóbulos rojos, glóbulos blancos y plaquetas. El análisis o examen de sangre mide el número y tipos de células en la sangre. Esto ayuda a los médicos a verificar su estado general de salud. Las pruebas también pueden ayudar a diagnosticar enfermedades y afecciones como anemia, infecciones, problemas de coagulación, cáncer de la sangre y enfermedades del sistema inmunitario.
Los tipos específicos incluyen pruebas para:
  Glóbulos rojos: Número, tamaño y tipos de glóbulos rojos en la sangre
  Glóbulos blancos: Número y tipos de glóbulos blancos en la sangre
  Plaquetas: Número y tamaño de las plaquetas
 Hemoglobina: Proteína rica en hierro en los glóbulos rojos que transporta el oxígeno
  Hematocrito: La cantidad de espacio que toman los glóbulos rojos en su sangre
  Conteo de reticulocitos: El número de glóbulos rojos jóvenes en su sangre
La media del volumen corpuscular medio: El tamaño promedio de los glóbulos rojos
 

El recuento sanguíneo completo o hemograma incluye la mayor parte o la totalidad de estas pruebas. Es una de las pruebas de sangre más comunes.

  NIH: Instituto Nacional del Corazón, los Pulmones y la Sangre', N'https://medlineplus.gov/spanish/bloodcounttests.html', 1, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_contenidos] WHERE [id] = N'A5E09C70-841C-446D-BB55-3F40FC65D1C6');
INSERT INTO [conocimiento_contenidos] ([id], [analito_id], [fuente_id], [idioma], [tipo], [texto], [url_referencia], [version], [estado], [revisado_por], [revisado_en])
SELECT N'047F73A1-FB04-4706-9FA4-8F7CD8741211', N'A0000000-0000-4000-8000-00000000B002', N'9824CE2F-37A9-43CA-B027-599A91EF67F8', N'es', N'EDUCACION', N'¿Qué es la glucosa en sangre?
La glucosa en sangre, o azúcar en la sangre, es el azúcar principal que se encuentra en la sangre. Es la principal fuente de energía de su cuerpo. Proviene de los alimentos que consume. Su cuerpo descompone la mayor parte de ese alimento en glucosa y la libera en el torrente sanguíneo. Cuando su glucosa en sangre sube, le indica a su páncreas que libere insulina. La insulina es una hormona que ayuda a que la glucosa entre en las células para ser utilizada como energía.

¿Qué es la diabetes?
La diabetes es una enfermedad en la que los niveles de glucosa en la sangre son demasiado altos. Cuando tiene diabetes, su cuerpo no produce suficiente insulina, no puede usarla tan bien como debería, o ambas cosas a la vez. Eso provoca que demasiada glucosa permanezca en la sangre y no llegue a las células. Con el tiempo, tener demasiada glucosa en la sangre puede causar problemas de salud serios (llamadas complicaciones de la diabetes). Por ello, si tiene diabetes, es importante mantener sus niveles de glucosa en la sangre dentro de su rango objetivo.

¿Qué son los objetivos de glucosa en sangre?
Si tiene diabetes, su objetivo de glucosa en sangre (objetivo glucémico) es el rango que intenta alcanzar en lo posible. Los objetivos típicos son:

Antes de una comida: 80 a 130 mg/dL
Dos horas después del comienzo de una comida: Menos de 180 mg/dL


Sus objetivos de glucosa en sangre pueden ser diferentes según su edad, algún problema de salud adicional que tenga y otros factores. Hable con su equipo de atención médica sobre el mejor rango objetivo para usted.

¿Cuándo y cómo debo controlar mi glucosa en sangre?
Si tiene diabetes, es probable que deba controlar su glucosa en sangre todos los días para asegurarse de que sus valores de glucosa en sangre se encuentren en su rango objetivo. Algunas personas pueden necesitar controlar su glucosa en sangre varias veces al día. Pregúntele a su equipo de atención médica con qué frecuencia debe revisarlo.

La for', N'https://medlineplus.gov/spanish/bloodglucose.html', 1, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_contenidos] WHERE [id] = N'047F73A1-FB04-4706-9FA4-8F7CD8741211');
INSERT INTO [conocimiento_contenidos] ([id], [analito_id], [fuente_id], [idioma], [tipo], [texto], [url_referencia], [version], [estado], [revisado_por], [revisado_en])
SELECT N'2831500E-A7D7-4CF5-9452-98EA8271322C', N'A0000000-0000-4000-8000-00000000B001', N'9824CE2F-37A9-43CA-B027-599A91EF67F8', N'es', N'EDUCACION', N'¿Qué es un análisis de hemoglobina?
El análisis de hemoglobina mide los niveles de hemoglobina en la sangre. La hemoglobina es una proteína rica en hierro de los glóbulos rojos. Transporta oxígeno de los pulmones al resto del cuerpo. Sus células necesitan oxígeno para crecer, reproducirse y producir energía para que pueda funcionar. Los niveles de hemoglobina entregan información sobre la salud de sus glóbulos rojos. Los niveles anormales de hemoglobina podrían ser signo de un trastorno de la sangre.
Otros nombres: Hb, Hgb, H&H¿Para qué se usa?
El análisis de hemoglobina es un examen de sangre que se usa comúnmente para detectar anemia, una afección en la que su cuerpo no produce suficientes glóbulos rojos sanos.  Si tiene anemia, las células de su cuerpo no reciben el oxígeno que necesitan. Los niveles de hemoglobina usualmente se miden como parte de un conteo sanguíneo completo, un grupo de pruebas que mide la cantidad y tipo de sus células sanguíneas.
Una análisis de hemoglobina no es lo mismo que una prueba de hemoglobina glicosilada (HbA1c). La prueba HbA1c se usa para verificar el nivel promedio de glucosa en sangre (azúcar en la sangre).¿Por qué necesito un análisis de hemoglobina?
Su profesional de la salud podría pedir este análisis como parte de un examen de rutina o si usted:

Tiene síntomas de anemia como debilidad, mareos, palidez o manos y pies fríos
Tiene antecedentes familiares de talasemia, anemia falciforme u otros trastornos hereditarios de la sangre
Consume una dieta baja en hierro y otros minerales
Tiene una infección a largo plazo
Ha perdido mucha sangre por una lesión o procedimiento quirúrgico

También es posible que necesite esta prueba para monitorear el tratamiento de la anemia u otros problemas de salud que podrían afectar su nivel de hemoglobina.
¿Qué ocurre durante un análisis de hemoglobina?
Un profesional de la salud le toma una muestra de sangre de una vena de un brazo usando una aguja pequeña. Después de insertar la aguja, extrae un', N'https://medlineplus.gov/spanish/pruebas-de-laboratorio/analisis-de-hemoglobina', 1, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_contenidos] WHERE [id] = N'2831500E-A7D7-4CF5-9452-98EA8271322C');
INSERT INTO [conocimiento_contenidos] ([id], [analito_id], [fuente_id], [idioma], [tipo], [texto], [url_referencia], [version], [estado], [revisado_por], [revisado_en])
SELECT N'D1009FC7-BE24-4940-A14A-C0E6947AD421', N'A0000000-0000-4000-8000-00000000B002', N'9824CE2F-37A9-43CA-B027-599A91EF67F8', N'es', N'EDUCACION', N'¿Qué es la prueba de glucosa en la sangre?
La prueba de glucosa en la sangre mide los niveles de glucosa en su sangre. La glucosa es un tipo de azúcar. Es la principal fuente de energía del cuerpo. Proviene de los alimentos que consume. Una hormona llamada insulina ayuda a que la glucosa pase del torrente sanguíneo a las células para usarla como energía. Si tiene demasiada o muy poca glucosa en su sangre, puede ser una señal de una afección médica grave.
Los niveles altos de glucosa en la sangre (hiperglucemia) pueden ser un signo de prediabetes o diabetes. La prediabetes es una afección en la que los niveles de glucosa en sangre son más altos de lo que es saludable para usted, pero no lo suficientemente altos como para considerarse diabetes. Si tiene diabetes, su cuerpo no puede producir insulina o no puede usarla como debería, o ambas cosas a la vez. Demasiada glucosa permanece en la sangre y no llega a las células. Esto puede hacer que los niveles de glucosa suban demasiado. Si la glucosa en la sangre no se controla, puede causar problemas de salud graves y a largo plazo, como enfermedades cardíacas y problemas de los nervios.
Niveles altos de glucosa en la sangre también pueden ser causados por otras enfermedades que afectan los niveles de insulina o glucosa en la sangre, como problemas con el páncreas y glándulas suprarrenales, y efectos secundarios de algunas medicinas.
Niveles bajos de glucosa en la sangre (hipoglucemia) suele ser causada por ciertos medicamentos para la diabetes tipo 1 o tipo 2, incluyendo la insulina. En personas que no tienen diabetes, la hipoglucemia es mucho menos común. Puede ser causada por ciertos medicamentos y afecciones, como algunas enfermedades de los riñones o del hígado. Sin tratamiento, niveles bajos de glucosa en la sangre demasiado severos pueden causar problemas de salud graves, incluyendo convulsiones y daño cerebral.
Existen varios tipos de análisis de sangre para medir los niveles de glucosa en sangre. Su profesional de ', N'https://medlineplus.gov/spanish/pruebas-de-laboratorio/prueba-de-glucosa-en-la-sangre', 1, N'BORRADOR', NULL, NULL
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_contenidos] WHERE [id] = N'D1009FC7-BE24-4940-A14A-C0E6947AD421');

-- conocimiento_reglas
INSERT INTO [conocimiento_reglas] ([id], [codigo], [version], [nombre], [descripcion], [tipo], [codigo_diagnostico], [expresion_logica], [mensaje_propuesto], [prioridad], [estado], [vigente_desde], [vigente_hasta], [aprobado_por], [aprobado_en], [creado_en])
SELECT N'E1000000-0000-4000-8000-000000000001', N'GLU-AYUNO-ALTERADA-ADA', 1, N'Glucosa en ayunas alterada (ADA)', N'Glucosa en ayunas >= 100 mg/dL. Criterio ADA de glucosa alterada en ayunas (prediabetes).', N'HALLAZGO', NULL, N'TODOS', N'Glucosa en ayunas igual o mayor a 100 mg/dL: corresponde a glucosa alterada en ayunas segun el criterio ADA. Requiere revision medica; no es un diagnostico.', N'INFORMATIVA', N'BORRADOR', CAST('2026-11-07 00:00:00' AS datetime2), NULL, NULL, NULL, CAST('2026-11-07 18:57:33.002219' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_reglas] WHERE [id] = N'E1000000-0000-4000-8000-000000000001');
INSERT INTO [conocimiento_reglas] ([id], [codigo], [version], [nombre], [descripcion], [tipo], [codigo_diagnostico], [expresion_logica], [mensaje_propuesto], [prioridad], [estado], [vigente_desde], [vigente_hasta], [aprobado_por], [aprobado_en], [creado_en])
SELECT N'E1000000-0000-4000-8000-000000000002', N'GLU-AYUNO-DIABETES-ADA', 1, N'Glucosa en ayunas en rango de diabetes (ADA)', N'Glucosa en ayunas >= 126 mg/dL. Un criterio ADA de diabetes; requiere confirmacion.', N'ALERTA', NULL, N'TODOS', N'Glucosa en ayunas igual o mayor a 126 mg/dL: cumple un criterio ADA de diabetes, que debe confirmarse con una segunda medicion u otro criterio diagnostico. Requiere revision medica; no es un diagnostico.', N'INFORMATIVA', N'BORRADOR', CAST('2026-11-07 00:00:00' AS datetime2), NULL, NULL, NULL, CAST('2026-11-07 18:57:33.009231' AS datetime2)
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_reglas] WHERE [id] = N'E1000000-0000-4000-8000-000000000002');

-- conocimiento_reglas_criterios
INSERT INTO [conocimiento_reglas_criterios] ([id], [regla_id], [analito_id], [operador], [umbral_numerico], [valor_codificado], [unidad], [contexto], [orden_criterio])
SELECT N'B1000000-0000-4000-8000-000000000001', N'E1000000-0000-4000-8000-000000000001', N'A1000000-0000-4000-8000-000000000001', N'GE', 100.000000, NULL, N'mg/dL', N'AYUNO', 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] WHERE [id] = N'B1000000-0000-4000-8000-000000000001');
INSERT INTO [conocimiento_reglas_criterios] ([id], [regla_id], [analito_id], [operador], [umbral_numerico], [valor_codificado], [unidad], [contexto], [orden_criterio])
SELECT N'B1000000-0000-4000-8000-000000000002', N'E1000000-0000-4000-8000-000000000002', N'A1000000-0000-4000-8000-000000000001', N'GE', 126.000000, NULL, N'mg/dL', N'AYUNO', 1
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] WHERE [id] = N'B1000000-0000-4000-8000-000000000002');

-- conocimiento_reglas_fuentes
INSERT INTO [conocimiento_reglas_fuentes] ([regla_id], [fuente_id])
SELECT N'E1000000-0000-4000-8000-000000000001', N'F0000000-0000-4000-8000-000000000004'
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] WHERE [regla_id] = N'E1000000-0000-4000-8000-000000000001' AND [fuente_id] = N'F0000000-0000-4000-8000-000000000004');
INSERT INTO [conocimiento_reglas_fuentes] ([regla_id], [fuente_id])
SELECT N'E1000000-0000-4000-8000-000000000002', N'F0000000-0000-4000-8000-000000000004'
WHERE NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] WHERE [regla_id] = N'E1000000-0000-4000-8000-000000000002' AND [fuente_id] = N'F0000000-0000-4000-8000-000000000004');

-- ---------------------------------------------------------------------
-- 3. Validation of ranges and integrity (after the inserts, same transaction)
--    ERROR blocks the COMMIT. WARNING is informational.
-- ---------------------------------------------------------------------
CREATE TABLE #val ([severidad] varchar(8), [chequeo] varchar(60), [detalle] nvarchar(400));

INSERT INTO #val SELECT 'ERROR', 'RANGO_INVERTIDO', CONCAT(a.[nombre], ' / ', r.[id])
  FROM [conocimiento_referencias] r JOIN [conocimiento_analitos] a ON a.[id] = r.[analito_id]
 WHERE r.[referencia_min] > r.[referencia_max];

INSERT INTO #val SELECT 'ERROR', 'EDAD_INVERTIDA', CAST(r.[id] AS varchar(40))
  FROM [conocimiento_referencias] r WHERE r.[edad_min_dias] > r.[edad_max_dias];

INSERT INTO #val SELECT 'ERROR', 'FECHAS_INVERTIDAS', CAST(r.[id] AS varchar(40))
  FROM [conocimiento_referencias] r WHERE r.[fecha_fin] < r.[fecha_inicio];

INSERT INTO #val SELECT 'ERROR', 'REFERENCIA_SIN_LIMITES', CAST(r.[id] AS varchar(40))
  FROM [conocimiento_referencias] r JOIN [conocimiento_analitos] a ON a.[id] = r.[analito_id]
 WHERE a.[tipo_valor] = 'NUMERICO' AND r.[referencia_min] IS NULL AND r.[referencia_max] IS NULL;

INSERT INTO #val SELECT 'ERROR', 'LIMITE_NEGATIVO', CAST(r.[id] AS varchar(40))
  FROM [conocimiento_referencias] r WHERE r.[referencia_min] < 0 OR r.[referencia_max] < 0;

INSERT INTO #val SELECT 'ERROR', 'VALIDADA_FUENTE_NO_VIGENTE', CONCAT(f.[codigo], ' / ', r.[id])
  FROM [conocimiento_referencias] r JOIN [conocimiento_fuentes] f ON f.[id] = r.[fuente_id]
 WHERE r.[estado] = 'VALIDADA' AND f.[vigente] = 0;

INSERT INTO #val SELECT 'ERROR', 'VALIDADA_ANALITO_INACTIVO', CAST(r.[id] AS varchar(40))
  FROM [conocimiento_referencias] r JOIN [conocimiento_analitos] a ON a.[id] = r.[analito_id]
 WHERE r.[estado] = 'VALIDADA' AND a.[activo] = 0;

-- Only one VALIDADA reference may apply to the same analyte/sex/context/age/date window
-- (the API answers 409 on the second one).
INSERT INTO #val SELECT 'ERROR', 'VALIDADAS_SOLAPADAS', CONCAT(a.[id], ' <-> ', b.[id])
  FROM [conocimiento_referencias] a JOIN [conocimiento_referencias] b
    ON a.[analito_id] = b.[analito_id] AND a.[id] < b.[id]
 WHERE a.[estado] = 'VALIDADA' AND b.[estado] = 'VALIDADA'
   AND (a.[sexo_aplicable] IS NULL OR b.[sexo_aplicable] IS NULL OR a.[sexo_aplicable] = b.[sexo_aplicable])
   AND (a.[contexto] IS NULL OR b.[contexto] IS NULL OR a.[contexto] = b.[contexto])
   AND ISNULL(a.[edad_min_dias], 0) <= ISNULL(b.[edad_max_dias], 2147483647)
   AND ISNULL(b.[edad_min_dias], 0) <= ISNULL(a.[edad_max_dias], 2147483647)
   AND a.[fecha_inicio] <= ISNULL(b.[fecha_fin], '9999-12-31')
   AND b.[fecha_inicio] <= ISNULL(a.[fecha_fin], '9999-12-31');

INSERT INTO #val SELECT 'WARNING', 'UNIDAD_DISTINTA_A_CANONICA', CONCAT(a.[nombre], ': ', r.[unidad], ' vs ', a.[unidad_canonica])
  FROM [conocimiento_referencias] r JOIN [conocimiento_analitos] a ON a.[id] = r.[analito_id]
 WHERE a.[unidad_canonica] IS NOT NULL AND r.[unidad] <> a.[unidad_canonica];

INSERT INTO #val SELECT 'WARNING', 'BORRADOR_PENDIENTE_VALIDACION', CONCAT(f.[codigo], ' / ', a.[nombre], ' x', COUNT(*))
  FROM [conocimiento_referencias] r JOIN [conocimiento_fuentes] f ON f.[id] = r.[fuente_id]
  JOIN [conocimiento_analitos] a ON a.[id] = r.[analito_id]
 WHERE r.[estado] = 'BORRADOR' GROUP BY f.[codigo], a.[nombre];

INSERT INTO #val SELECT 'ERROR', 'REGLA_VALIDADA_SIN_CRITERIOS', CAST(g.[id] AS varchar(40))
  FROM [conocimiento_reglas] g WHERE g.[estado] = 'VALIDADA'
   AND NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] c WHERE c.[regla_id] = g.[id]);

INSERT INTO #val SELECT 'ERROR', 'REGLA_VALIDADA_SIN_FUENTE_VIGENTE', CAST(g.[id] AS varchar(40))
  FROM [conocimiento_reglas] g WHERE g.[estado] = 'VALIDADA'
   AND NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] rf JOIN [conocimiento_fuentes] f ON f.[id] = rf.[fuente_id]
                    WHERE rf.[regla_id] = g.[id] AND f.[vigente] = 1);

INSERT INTO #val SELECT 'ERROR', 'CRITERIO_NUMERICO_SIN_UMBRAL', CAST(c.[id] AS varchar(40))
  FROM [conocimiento_reglas_criterios] c
 WHERE c.[operador] IN ('LT','LE','GE','GT') AND c.[umbral_numerico] IS NULL;

SELECT [severidad], [chequeo], [detalle] FROM #val ORDER BY [severidad], [chequeo], [detalle];

-- Reconciliation: row counts in Azure after the sync (expected minimums from local)
SELECT 'conocimiento_fuentes' AS tabla, COUNT(*) AS filas_azure, 12 AS minimo_esperado FROM [conocimiento_fuentes]
UNION ALL SELECT 'conocimiento_analitos', COUNT(*), 8 FROM [conocimiento_analitos]
UNION ALL SELECT 'conocimiento_pruebas_analitos', COUNT(*), 5 FROM [conocimiento_pruebas_analitos]
UNION ALL SELECT 'conocimiento_referencias', COUNT(*), 30 FROM [conocimiento_referencias]
UNION ALL SELECT 'conocimiento_contenidos', COUNT(*), 4 FROM [conocimiento_contenidos]
UNION ALL SELECT 'conocimiento_reglas', COUNT(*), 4 FROM [conocimiento_reglas]
UNION ALL SELECT 'conocimiento_reglas_criterios', COUNT(*), 4 FROM [conocimiento_reglas_criterios]
UNION ALL SELECT 'conocimiento_reglas_fuentes', COUNT(*), 4 FROM [conocimiento_reglas_fuentes];

IF EXISTS (SELECT 1 FROM #val WHERE [severidad] = 'ERROR')
BEGIN
  ROLLBACK TRANSACTION;
  PRINT 'ROLLBACK: validation errors found. Nothing was written.';
END
ELSE
BEGIN
  COMMIT TRANSACTION;
  PRINT 'COMMIT: knowledge base synchronized and validated.';
END
DROP TABLE #val;
