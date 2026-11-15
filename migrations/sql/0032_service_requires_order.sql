-- =====================================================================
-- Services: a flag for the services that need a medical order (revisiones de resultados).
-- The revision is booked for the doctor of the order, so the portal must know which services
-- need one. Idempotent: safe to run more than once.
-- =====================================================================

IF COL_LENGTH('servicios_medicos', 'requiere_orden_medica') IS NULL
BEGIN
  ALTER TABLE [servicios_medicos]
    ADD [requiere_orden_medica] BIT NOT NULL
      CONSTRAINT [DF_servicios_medicos_requiere_orden] DEFAULT 0;
END
GO

UPDATE [servicios_medicos]
SET [requiere_orden_medica] = 1
WHERE [especialidad_id] IS NULL
  AND [nombre] LIKE N'Revisi%n de resultados con el m%dico'
  AND [requiere_orden_medica] = 0;
GO
