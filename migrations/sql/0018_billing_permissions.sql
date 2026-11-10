-- =====================================================================
-- Billing (facturacion): permissions for invoices and checkout, and at
-- most one invoice per appointment. Idempotent: safe to run more than once.
-- =====================================================================

MERGE [permisos] AS target
USING (VALUES
  ('facturas.crear', 'facturas', 'crear', N'Crear la factura de una cita propia'),
  ('pagos.iniciar',  'pagos',    'iniciar', N'Iniciar el pago de una factura propia')
) AS source ([codigo], [recurso], [accion], [descripcion])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [recurso], [accion], [descripcion])
  VALUES (source.[codigo], source.[recurso], source.[accion], source.[descripcion]);
GO

-- ASSUMPTION (billing S4): the patient who owns the appointment invoices and pays it.
MERGE [roles_permisos] AS target
USING (
  SELECT r.[id] AS [rol_id], p.[id] AS [permiso_id]
  FROM (VALUES
    ('PACIENTE', 'facturas.crear'),
    ('PACIENTE', 'pagos.iniciar')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO

-- One invoice per appointment (billing S7). The application checks it first; this index is the
-- last guard against two concurrent requests.
IF NOT EXISTS (
  SELECT 1 FROM sys.indexes WHERE name = N'UX_facturas_cita' AND object_id = OBJECT_ID(N'dbo.facturas')
)
BEGIN
  CREATE UNIQUE INDEX [UX_facturas_cita] ON [facturas] ([cita_id]) WHERE [cita_id] IS NOT NULL;
END
GO
