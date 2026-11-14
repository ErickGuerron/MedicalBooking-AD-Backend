-- =====================================================================
-- E8 patient self-registration: email verification tokens.
-- Only the SHA-256 hash of each token is stored, never the token itself.
-- Idempotent: the table is created only when it does not exist.
-- =====================================================================

IF OBJECT_ID('verificaciones_correo', 'U') IS NULL
BEGIN
  CREATE TABLE [verificaciones_correo] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [usuario_id] uniqueidentifier NOT NULL,
    [token_hash] char(64) NOT NULL,
    [creado_en] datetime2 NOT NULL,
    [expira_en] datetime2 NOT NULL,
    [usado_en] datetime2 NULL,
    CONSTRAINT [UQ_verificaciones_token] UNIQUE ([token_hash]),
    CONSTRAINT [FK_verificaciones_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
  )
END
GO
