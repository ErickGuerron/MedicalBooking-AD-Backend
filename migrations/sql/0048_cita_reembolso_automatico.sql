-- =====================================================================
-- Outcome of the automatic refund requested when a visit is cancelled: PENDIENTE, PROCESADO,
-- FALLIDO, or the code of the rule that refused it. NULL: no automatic request. Idempotent.
-- =====================================================================

IF COL_LENGTH(N'citas', N'reembolso_automatico') IS NULL
  ALTER TABLE [citas] ADD [reembolso_automatico] varchar(40) NULL;
GO
