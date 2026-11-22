-- =====================================================================
-- Administrator reports and monitoring: two read-only permissions, granted to ADMINISTRADOR only.
-- Idempotent: safe to run more than once.
-- =====================================================================

MERGE [permisos] AS target
USING (VALUES
  ('reportes.leer',   'reportes',   'leer', N'Ver el resumen de citas, órdenes, pagos y tratamientos por periodo'),
  ('monitoreo.leer',  'monitoreo',  'leer', N'Ver el estado de la base principal y de la replicación')
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
    ('ADMINISTRADOR', 'reportes.leer'),
    ('ADMINISTRADOR', 'monitoreo.leer')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
