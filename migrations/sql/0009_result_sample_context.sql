-- =====================================================================
-- E5.1 sample context for results: fasting status ("AYUNO") and similar.
-- Nullable: existing results have no known context, so no context-bound rule fires for them.
-- Idempotent: the column is added only when it does not exist.
-- =====================================================================

IF COL_LENGTH('resultados_laboratorio', 'contexto_muestra') IS NULL
  ALTER TABLE [resultados_laboratorio] ADD [contexto_muestra] varchar(100) NULL;
GO
