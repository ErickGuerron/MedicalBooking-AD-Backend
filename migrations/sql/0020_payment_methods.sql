-- =====================================================================
-- Saved payment methods: one Stripe Customer per user and the permission to manage the
-- user's saved cards. Only the Stripe customer id is stored. No card data (number, CVC,
-- expiry or a PaymentMethod copy) is ever written here. Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'dbo.stripe_clientes', N'U') IS NULL
BEGIN
  CREATE TABLE [stripe_clientes] (
    [usuario_id] uniqueidentifier NOT NULL CONSTRAINT [PK_stripe_clientes] PRIMARY KEY,
    [stripe_customer_id] varchar(255) NOT NULL CONSTRAINT [UX_stripe_clientes_customer] UNIQUE,
    [creado_en] datetime2 NOT NULL CONSTRAINT [DF_stripe_clientes_creado_en] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_stripe_clientes_usuarios] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
  );
END
GO

MERGE [permisos] AS target
USING (VALUES
  ('metodos_pago.gestionar', 'metodos_pago', 'gestionar', N'Gestionar las tarjetas guardadas propias')
) AS source ([codigo], [recurso], [accion], [descripcion])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [recurso], [accion], [descripcion])
  VALUES (source.[codigo], source.[recurso], source.[accion], source.[descripcion]);
GO

-- Patients manage their own saved cards. Ownership is checked in the application layer.
MERGE [roles_permisos] AS target
USING (
  SELECT r.[id] AS [rol_id], p.[id] AS [permiso_id]
  FROM (VALUES
    ('PACIENTE', 'metodos_pago.gestionar')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
