-- =====================================================================
-- Doctor cancellation limits: admin approvals for a doctor's cancellation
-- that exceeds the monthly quota or gives less than 24 hours of notice.
-- One approval per appointment. Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'dbo.aprobaciones_cancelacion_medico', N'U') IS NULL
BEGIN
  CREATE TABLE [aprobaciones_cancelacion_medico] (
    [id] uniqueidentifier NOT NULL CONSTRAINT [PK_aprobaciones_cancelacion_medico] PRIMARY KEY,
    [cita_id] uniqueidentifier NOT NULL,
    [aprobado_por] uniqueidentifier NOT NULL,
    [motivo] nvarchar(1500) NOT NULL,
    [aprobado_en] datetime2 NOT NULL,
    CONSTRAINT [UX_aprobaciones_cancelacion_cita] UNIQUE ([cita_id]),
    CONSTRAINT [FK_aprobaciones_cancelacion_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id]),
    CONSTRAINT [FK_aprobaciones_cancelacion_admin] FOREIGN KEY ([aprobado_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_aprobaciones_cancelacion_motivo] CHECK (LEN(LTRIM(RTRIM([motivo]))) > 0)
  );
END
GO

MERGE [permisos] AS target
USING (VALUES
  ('citas.revisar_cancelaciones', 'citas', 'revisar_cancelaciones',
   N'Aprobar cancelaciones del medico fuera de cuota o de plazo')
) AS source ([codigo], [recurso], [accion], [descripcion])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [recurso], [accion], [descripcion])
  VALUES (source.[codigo], source.[recurso], source.[accion], source.[descripcion]);
GO

MERGE [roles_permisos] AS target
USING (
  SELECT r.[id] AS [rol_id], p.[id] AS [permiso_id]
  FROM (VALUES
    ('ADMINISTRADOR', 'citas.revisar_cancelaciones')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
