-- =====================================================================
-- Watermarks for the external-write sync: the last rowversion (as BIGINT)
-- delivered per source table. One row per table. Idempotent.
-- =====================================================================

IF OBJECT_ID(N'dbo.sincronizacion_watermarks', N'U') IS NULL
BEGIN
  CREATE TABLE [sincronizacion_watermarks] (
    [tabla] nvarchar(128) NOT NULL CONSTRAINT [PK_sincronizacion_watermarks] PRIMARY KEY,
    [ultima_version] bigint NOT NULL CONSTRAINT [DF_sincronizacion_watermarks_version] DEFAULT 0,
    [actualizado_en] datetime2 NOT NULL CONSTRAINT [DF_sincronizacion_watermarks_actualizado] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [CK_sincronizacion_watermarks_version] CHECK ([ultima_version] >= 0)
  );
END
GO
