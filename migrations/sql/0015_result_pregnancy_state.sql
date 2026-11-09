-- =====================================================================
-- Pregnancy state at sample time, on each lab result (clinical findings S18).
-- Values: NO (not pregnant), T1, T2, T3 (trimesters). NULL means unknown, never assumed.
-- Idempotent: the column and the constraint are added only when they do not exist.
-- =====================================================================

IF COL_LENGTH('resultados_laboratorio', 'estado_embarazo') IS NULL
  ALTER TABLE [resultados_laboratorio] ADD [estado_embarazo] varchar(3) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_resultados_estado_embarazo')
  ALTER TABLE [resultados_laboratorio] ADD CONSTRAINT [CK_resultados_estado_embarazo]
    CHECK ([estado_embarazo] IS NULL OR [estado_embarazo] IN ('NO', 'T1', 'T2', 'T3'));
GO
