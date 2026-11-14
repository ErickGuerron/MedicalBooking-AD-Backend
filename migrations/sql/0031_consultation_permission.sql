-- =====================================================================
-- Consultations: the consultas.abrir permission (a doctor opens the consultation of an
-- appointment of the own agenda), granted to MEDICO.
-- Idempotent: safe to run more than once.
-- =====================================================================

MERGE [permisos] AS target
USING (VALUES
  ('consultas.abrir', 'consultas', 'abrir',
   N'Abrir la consulta medica de una cita de la propia agenda')
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
    ('MEDICO', 'consultas.abrir')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
