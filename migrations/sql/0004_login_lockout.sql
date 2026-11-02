-- =====================================================================
-- E1 hardening: failed-login counter for brute-force protection (spec section 10).
-- Additive and idempotent: existing rows get 0, no data is rewritten.
-- The lock itself uses usuarios.bloqueado_hasta, which already exists in the DBML.
-- =====================================================================

IF COL_LENGTH(N'dbo.credenciales_usuario', N'intentos_fallidos') IS NULL
BEGIN
  ALTER TABLE [credenciales_usuario]
    ADD [intentos_fallidos] int NOT NULL
      CONSTRAINT [DF_credenciales_intentos] DEFAULT 0;
END
GO

-- Separate batch: SQL Server cannot resolve a column added earlier in the same batch.
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_credenciales_intentos')
  ALTER TABLE [credenciales_usuario]
    ADD CONSTRAINT [CK_credenciales_intentos] CHECK ([intentos_fallidos] >= 0);
GO
