-- =====================================================================
-- Doctor work hours, service windows and their permissions.
-- A doctor declares the hours they work; the administrator creates slots from them.
-- Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'[horarios_trabajo_medico]', N'U') IS NULL
BEGIN
  CREATE TABLE [horarios_trabajo_medico] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [medico_id] uniqueidentifier NOT NULL,
    [dia_semana] tinyint NOT NULL,
    [inicio] time(0) NOT NULL,
    [fin] time(0) NOT NULL,
    [actualizado_en] datetime2 NOT NULL CONSTRAINT [DF_horarios_trabajo_actualizado] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_horarios_trabajo_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id]),
    CONSTRAINT [CK_horarios_trabajo_dia] CHECK ([dia_semana] BETWEEN 0 AND 6),
    CONSTRAINT [CK_horarios_trabajo_rango] CHECK ([fin] > [inicio]),
    CONSTRAINT [UQ_horarios_trabajo_inicio] UNIQUE ([medico_id], [dia_semana], [inicio])
  )
END
GO

IF COL_LENGTH(N'servicios_medicos', N'franja') IS NULL
  ALTER TABLE [servicios_medicos] ADD [franja] varchar(10) NOT NULL
    CONSTRAINT [DF_servicios_franja] DEFAULT 'TARDE';
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE [name] = N'CK_servicios_franja')
  ALTER TABLE [servicios_medicos] ADD CONSTRAINT [CK_servicios_franja]
    CHECK ([franja] IN ('MANANA', 'TARDE'));
GO

-- Sample collection is a morning service; every other service is an afternoon consultation.
UPDATE [servicios_medicos] SET [franja] = 'MANANA'
WHERE [nombre] LIKE N'Toma de muestra%' AND [franja] <> 'MANANA';
GO

MERGE [permisos] AS target
USING (VALUES
  ('horario.declarar', 'horario', 'declarar', N'Declarar el horario de trabajo propio'),
  ('horario.generar',  'horario', 'generar',  N'Ver la propuesta y generar horarios de citas de un médico')
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
    ('MEDICO',         'horario.declarar'),
    ('ADMINISTRADOR',  'horario.generar')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
