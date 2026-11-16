-- =====================================================================
-- Services: a service can depend on another one (servicio_adicional_id). The invoice of an
-- appointment for a service with a dependency carries a second line: the dependent service, so
-- the patient sees what the blood sample costs. Replaces the requiere_toma_sangre flag and the
-- BLOOD_SAMPLE_SERVICE_ID environment variable. One dependency per service.
-- Idempotent. Flagged exams are linked to the "Toma de muestra de sangre" service by name; the
-- migration fails when a flagged exam has no such service, so no exam loses its sample line.
-- =====================================================================

IF COL_LENGTH('servicios_medicos', 'servicio_adicional_id') IS NULL
BEGIN
  ALTER TABLE [servicios_medicos]
    ADD [servicio_adicional_id] UNIQUEIDENTIFIER NULL
      CONSTRAINT [FK_servicios_medicos_servicio_adicional]
      REFERENCES [servicios_medicos] ([id]);
END
GO

IF COL_LENGTH('servicios_medicos', 'requiere_toma_sangre') IS NOT NULL
BEGIN
  EXEC sp_executesql N'
    UPDATE [servicios_medicos]
    SET [servicio_adicional_id] = (
      SELECT TOP 1 [id] FROM [servicios_medicos] WHERE [nombre] = N''Toma de muestra de sangre''
    )
    WHERE [requiere_toma_sangre] = 1 AND [servicio_adicional_id] IS NULL;

    IF EXISTS (
      SELECT 1 FROM [servicios_medicos]
      WHERE [requiere_toma_sangre] = 1 AND [servicio_adicional_id] IS NULL
    )
      THROW 50000, N''Migration 0036: a flagged exam has no blood sample service'', 1;';
END
GO

IF COL_LENGTH('servicios_medicos', 'requiere_toma_sangre') IS NOT NULL
BEGIN
  ALTER TABLE [servicios_medicos] DROP CONSTRAINT [DF_servicios_medicos_requiere_toma_sangre];
  ALTER TABLE [servicios_medicos] DROP COLUMN [requiere_toma_sangre];
END
GO
