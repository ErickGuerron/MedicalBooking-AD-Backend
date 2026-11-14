-- Doctor cancellation (scheduling): the reason of a cancellation is stored on the appointment.
-- Patient cancellations keep both columns NULL. Idempotent: safe to run more than once.
IF COL_LENGTH(N'dbo.citas', N'motivo_cancelacion_codigo') IS NULL
  ALTER TABLE [citas] ADD [motivo_cancelacion_codigo] varchar(40) NULL;
GO
IF COL_LENGTH(N'dbo.citas', N'motivo_cancelacion_texto') IS NULL
  ALTER TABLE [citas] ADD [motivo_cancelacion_texto] nvarchar(500) NULL;
GO

-- Permission for a doctor to cancel an appointment of an own agenda. Patients keep citas.reservar.
MERGE [permisos] AS target
USING (VALUES
  ('citas.cancelar_medico', 'citas', 'cancelar_medico', N'Cancelar una cita de la propia agenda con motivo')
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
    ('MEDICO', 'citas.cancelar_medico')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
