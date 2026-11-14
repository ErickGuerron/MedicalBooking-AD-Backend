-- =====================================================================
-- Checkout retry state on payment attempts (checkout-retry S6). Only counters and schedule
-- live here: no checkout link, no card data, no notification rows. Idempotent: safe to run
-- more than once.
-- =====================================================================

IF COL_LENGTH(N'dbo.pagos', N'reintentos_realizados') IS NULL
BEGIN
  ALTER TABLE [pagos] ADD [reintentos_realizados] int NOT NULL
    CONSTRAINT [DF_pagos_reintentos_realizados] DEFAULT 0;
END
GO

-- Separate batch: SQL Server cannot reference a column added earlier in the same batch.
IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_pagos_reintentos_rango')
BEGIN
  ALTER TABLE [pagos] ADD CONSTRAINT [CK_pagos_reintentos_rango]
    CHECK ([reintentos_realizados] BETWEEN 0 AND 5);
END
GO

IF COL_LENGTH(N'dbo.pagos', N'reintentos_agotados') IS NULL
BEGIN
  ALTER TABLE [pagos] ADD [reintentos_agotados] bit NOT NULL
    CONSTRAINT [DF_pagos_reintentos_agotados] DEFAULT 0;
END
GO

IF COL_LENGTH(N'dbo.pagos', N'ultimo_error_codigo') IS NULL
BEGIN
  ALTER TABLE [pagos] ADD [ultimo_error_codigo] varchar(100) NULL;
END
GO

IF COL_LENGTH(N'dbo.pagos', N'proximo_reintento_en') IS NULL
BEGIN
  ALTER TABLE [pagos] ADD [proximo_reintento_en] datetime2 NULL;
END
GO

-- The worker looks only for pending attempts with a retry still due.
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes WHERE name = N'IX_pagos_reintento_pendiente' AND object_id = OBJECT_ID(N'dbo.pagos')
)
BEGIN
  CREATE INDEX [IX_pagos_reintento_pendiente] ON [pagos] ([proximo_reintento_en])
    WHERE [estado] = 'PENDIENTE' AND [reintentos_agotados] = 0 AND [proximo_reintento_en] IS NOT NULL;
END
GO
