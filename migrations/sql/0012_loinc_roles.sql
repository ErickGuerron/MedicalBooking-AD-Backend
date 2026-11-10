-- =====================================================================
-- LOINC codes for the shadow findings (decision L24/L26).
-- 1. Release the synthetic codes: the seeded fixtures are not clinical analytes. A synthetic
--    hemoglobin holding 718-7 and a synthetic glucose holding 2339-0 (not fasting) must not feed
--    the findings. Only the exact seeded values are released.
-- 2. Assign the verified codes to the real analytes, when they exist and have no code yet.
-- 3. Create the missing C-reactive protein and HbA1c analytes (fixed identifiers).
-- 4. Ferritin: the analyte created in development for the WHO 2020 Table 1 rows, by name.
-- Idempotent.
-- =====================================================================
UPDATE [conocimiento_analitos]
   SET [codigo_loinc] = NULL
 WHERE [id] = 'a0000000-0000-4000-8000-00000000b001' AND [codigo_loinc] = '718-7';
UPDATE [conocimiento_analitos]
   SET [codigo_loinc] = NULL
 WHERE [id] = 'a0000000-0000-4000-8000-00000000b002' AND [codigo_loinc] = '2339-0';
GO

UPDATE [conocimiento_analitos]
   SET [codigo_loinc] = '718-7'
 WHERE [id] = 'a1000000-0000-4000-8000-000000000002' AND [codigo_loinc] IS NULL
   AND NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [codigo_loinc] = '718-7');
UPDATE [conocimiento_analitos]
   SET [codigo_loinc] = '1558-6'
 WHERE [id] = 'a1000000-0000-4000-8000-000000000001' AND [codigo_loinc] IS NULL
   AND NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [codigo_loinc] = '1558-6');
UPDATE [conocimiento_analitos]
   SET [codigo_loinc] = '2276-4'
 WHERE [nombre] = N'Ferritina serica (WHO 2020)' AND [codigo_loinc] IS NULL
   AND NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [codigo_loinc] = '2276-4');
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos]
               WHERE [id] = 'a2000000-0000-4000-8000-000000000001' OR [codigo_loinc] = '1988-5')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [unidad_canonica], [tipo_valor], [codigo_loinc],
                                       [activo], [creado_en], [actualizado_en])
  VALUES ('a2000000-0000-4000-8000-000000000001', N'Proteina C reactiva (PCR)', 'mg/L', 'NUMERICO',
          '1988-5', 1, SYSUTCDATETIME(), SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos]
               WHERE [id] = 'a2000000-0000-4000-8000-000000000002' OR [codigo_loinc] = '4548-4')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [unidad_canonica], [tipo_valor], [codigo_loinc],
                                       [activo], [creado_en], [actualizado_en])
  VALUES ('a2000000-0000-4000-8000-000000000002', N'Hemoglobina glicosilada (HbA1c, NGSP)', '%', 'NUMERICO',
          '4548-4', 1, SYSUTCDATETIME(), SYSUTCDATETIME());
GO
