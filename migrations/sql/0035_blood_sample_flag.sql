-- =====================================================================
-- Services: a flag for the lab exams that need a blood sample. When it is set, the invoice of an
-- appointment for that service carries a second line: the blood sample service (see
-- BLOOD_SAMPLE_SERVICE_ID), so the patient sees what the sample costs. Idempotent.
-- =====================================================================

IF COL_LENGTH('servicios_medicos', 'requiere_toma_sangre') IS NULL
BEGIN
  ALTER TABLE [servicios_medicos]
    ADD [requiere_toma_sangre] BIT NOT NULL
      CONSTRAINT [DF_servicios_medicos_requiere_toma_sangre] DEFAULT 0;
END
GO

UPDATE [servicios_medicos]
SET [requiere_toma_sangre] = 1
WHERE [nombre] IN (N'Hemograma completo', N'Perfil lipídico', N'Glucosa en ayunas')
  AND [requiere_toma_sangre] = 0;
GO
