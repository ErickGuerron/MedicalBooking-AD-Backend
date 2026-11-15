-- =====================================================================
-- Order confirmation: the laboratory closes a completed order, with or without its report.
-- Null while open; once set, results and reports can no longer change. Idempotent.
-- =====================================================================

IF COL_LENGTH('ordenes_laboratorio', 'confirmada_en') IS NULL
BEGIN
  ALTER TABLE [ordenes_laboratorio] ADD [confirmada_en] datetime2 NULL;
END
GO
