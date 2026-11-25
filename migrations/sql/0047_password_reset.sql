-- =====================================================================
-- Password reset links: single use, one hour, stored only as a hash. A new request retires the
-- previous unused links of the same account. Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'[restablecimientos_contrasena]', N'U') IS NULL
BEGIN
  CREATE TABLE [restablecimientos_contrasena] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [usuario_id] uniqueidentifier NOT NULL,
    [token_hash] char(64) NOT NULL,
    [creado_en] datetime2 NOT NULL,
    [expira_en] datetime2 NOT NULL,
    [usado_en] datetime2 NULL,
    CONSTRAINT [UQ_restablecimiento_token] UNIQUE ([token_hash]),
    CONSTRAINT [FK_restablecimiento_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
  )
END
GO
