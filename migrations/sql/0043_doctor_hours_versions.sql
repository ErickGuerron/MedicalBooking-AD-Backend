-- =====================================================================
-- Doctor work hours become versions with an effective date: a change applies only from a future
-- week on, so the weeks already planned keep the hours they were planned with.
-- Existing rows become the version effective since 1900-01-01 (they always applied).
-- Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'[horarios_trabajo_version]', N'U') IS NULL
BEGIN
  CREATE TABLE [horarios_trabajo_version] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [medico_id] uniqueidentifier NOT NULL,
    [vigente_desde] date NOT NULL,
    [creado_en] datetime2 NOT NULL CONSTRAINT [DF_horarios_version_creado] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_horarios_version_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id]),
    CONSTRAINT [UQ_horarios_version_medico_desde] UNIQUE ([medico_id], [vigente_desde])
  )
END
GO

-- One version per doctor that already has rows, effective from the beginning of time.
INSERT INTO [horarios_trabajo_version] ([id], [medico_id], [vigente_desde])
SELECT DISTINCT NEWID(), h.[medico_id], '1900-01-01'
FROM [horarios_trabajo_medico] h
WHERE NOT EXISTS (
  SELECT 1 FROM [horarios_trabajo_version] v WHERE v.[medico_id] = h.[medico_id]
);
GO

IF COL_LENGTH(N'horarios_trabajo_medico', N'version_id') IS NULL
  ALTER TABLE [horarios_trabajo_medico] ADD [version_id] uniqueidentifier NULL;
GO

UPDATE h
SET h.[version_id] = v.[id]
FROM [horarios_trabajo_medico] h
JOIN [horarios_trabajo_version] v ON v.[medico_id] = h.[medico_id] AND v.[vigente_desde] = '1900-01-01'
WHERE h.[version_id] IS NULL;
GO

IF EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID(N'horarios_trabajo_medico') AND name = N'version_id' AND is_nullable = 1)
  ALTER TABLE [horarios_trabajo_medico] ALTER COLUMN [version_id] uniqueidentifier NOT NULL;
GO

IF EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_horarios_trabajo_medico')
  ALTER TABLE [horarios_trabajo_medico] DROP CONSTRAINT [FK_horarios_trabajo_medico];
GO

IF EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UQ_horarios_trabajo_inicio' AND object_id = OBJECT_ID(N'horarios_trabajo_medico'))
  ALTER TABLE [horarios_trabajo_medico] DROP CONSTRAINT [UQ_horarios_trabajo_inicio];
GO

IF COL_LENGTH(N'horarios_trabajo_medico', N'medico_id') IS NOT NULL
  ALTER TABLE [horarios_trabajo_medico] DROP COLUMN [medico_id];
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE name = N'FK_horarios_trabajo_version')
  ALTER TABLE [horarios_trabajo_medico] ADD CONSTRAINT [FK_horarios_trabajo_version]
    FOREIGN KEY ([version_id]) REFERENCES [horarios_trabajo_version] ([id]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UQ_horarios_trabajo_version_inicio' AND object_id = OBJECT_ID(N'horarios_trabajo_medico'))
  CREATE UNIQUE INDEX [UQ_horarios_trabajo_version_inicio]
    ON [horarios_trabajo_medico] ([version_id], [dia_semana], [inicio]);
GO
