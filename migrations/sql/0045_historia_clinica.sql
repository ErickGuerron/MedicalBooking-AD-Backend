-- =====================================================================
-- Historia clinica: entries per patient, verified by the treating doctor, never deleted.
-- Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'[historia_alergias]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_alergias] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [sustancia] nvarchar(150) NOT NULL,
    [categoria] varchar(20) NOT NULL,
    [reaccion] nvarchar(300) NULL,
    [severidad] varchar(15) NOT NULL,
    [estado] varchar(10) NOT NULL CONSTRAINT [DF_alergias_estado] DEFAULT 'ACTIVA',
    [origen] varchar(20) NOT NULL,
    [verificada] bit NOT NULL,
    [verificada_por] uniqueidentifier NULL,
    [verificada_en] datetime2 NULL,
    [registrada_por] uniqueidentifier NOT NULL,
    [registrada_en] datetime2 NOT NULL CONSTRAINT [DF_alergias_registrada] DEFAULT SYSUTCDATETIME(),
    [actualizada_en] datetime2 NOT NULL CONSTRAINT [DF_alergias_actualizada] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_alergias_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_alergias_verifico] FOREIGN KEY ([verificada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [FK_alergias_registro] FOREIGN KEY ([registrada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_alergias_sustancia] CHECK (LEN(LTRIM(RTRIM([sustancia]))) > 0),
    CONSTRAINT [CK_alergias_categoria] CHECK ([categoria] IN ('MEDICAMENTO', 'ALIMENTO', 'LATEX', 'AMBIENTAL', 'OTRA')),
    CONSTRAINT [CK_alergias_severidad] CHECK ([severidad] IN ('LEVE', 'MODERADA', 'GRAVE', 'DESCONOCIDA')),
    CONSTRAINT [CK_alergias_estado] CHECK ([estado] IN ('ACTIVA', 'RESUELTA', 'ERRONEA')),
    CONSTRAINT [CK_alergias_origen] CHECK ([origen] IN ('REGISTRADA_MEDICO', 'DECLARADA_PACIENTE')),
    -- A verified entry names who verified it; a declared one is not verified until a doctor does.
    CONSTRAINT [CK_alergias_verificacion] CHECK (
      ([verificada] = 1 AND [verificada_por] IS NOT NULL AND [verificada_en] IS NOT NULL)
      OR ([verificada] = 0 AND [verificada_por] IS NULL AND [verificada_en] IS NULL AND [origen] = 'DECLARADA_PACIENTE')
    )
  );
  CREATE INDEX [IX_alergias_paciente] ON [historia_alergias] ([paciente_id], [estado]);
END
GO

IF OBJECT_ID(N'[historia_antecedentes]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_antecedentes] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [tipo] varchar(12) NOT NULL,
    [descripcion] nvarchar(500) NOT NULL,
    [parentesco] nvarchar(50) NULL,
    [cie11] varchar(20) NULL,
    [estado] varchar(10) NOT NULL CONSTRAINT [DF_antecedentes_estado] DEFAULT 'ACTIVO',
    [origen] varchar(20) NOT NULL,
    [verificada] bit NOT NULL,
    [verificada_por] uniqueidentifier NULL,
    [verificada_en] datetime2 NULL,
    [registrada_por] uniqueidentifier NOT NULL,
    [registrada_en] datetime2 NOT NULL CONSTRAINT [DF_antecedentes_registrada] DEFAULT SYSUTCDATETIME(),
    [actualizada_en] datetime2 NOT NULL CONSTRAINT [DF_antecedentes_actualizada] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_antecedentes_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_antecedentes_verifico] FOREIGN KEY ([verificada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [FK_antecedentes_registro] FOREIGN KEY ([registrada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_antecedentes_tipo] CHECK ([tipo] IN ('PERSONAL', 'FAMILIAR', 'QUIRURGICO')),
    CONSTRAINT [CK_antecedentes_descripcion] CHECK (LEN(LTRIM(RTRIM([descripcion]))) > 0),
    CONSTRAINT [CK_antecedentes_parentesco] CHECK ([tipo] <> 'FAMILIAR' OR LEN(LTRIM(RTRIM([parentesco]))) > 0),
    CONSTRAINT [CK_antecedentes_estado] CHECK ([estado] IN ('ACTIVO', 'RESUELTO', 'ERRONEO')),
    CONSTRAINT [CK_antecedentes_origen] CHECK ([origen] IN ('REGISTRADA_MEDICO', 'DECLARADA_PACIENTE')),
    CONSTRAINT [CK_antecedentes_verificacion] CHECK (
      ([verificada] = 1 AND [verificada_por] IS NOT NULL AND [verificada_en] IS NOT NULL)
      OR ([verificada] = 0 AND [verificada_por] IS NULL AND [verificada_en] IS NULL AND [origen] = 'DECLARADA_PACIENTE')
    )
  );
  CREATE INDEX [IX_antecedentes_paciente] ON [historia_antecedentes] ([paciente_id], [estado]);
END
GO

IF OBJECT_ID(N'[historia_medicacion]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_medicacion] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [medicamento] nvarchar(150) NOT NULL,
    [dosis] nvarchar(80) NULL,
    [frecuencia] nvarchar(80) NULL,
    [inicio] date NULL,
    [estado] varchar(12) NOT NULL CONSTRAINT [DF_medicacion_estado] DEFAULT 'ACTIVA',
    [origen] varchar(20) NOT NULL,
    [verificada] bit NOT NULL,
    [verificada_por] uniqueidentifier NULL,
    [verificada_en] datetime2 NULL,
    [registrada_por] uniqueidentifier NOT NULL,
    [registrada_en] datetime2 NOT NULL CONSTRAINT [DF_medicacion_registrada] DEFAULT SYSUTCDATETIME(),
    [actualizada_en] datetime2 NOT NULL CONSTRAINT [DF_medicacion_actualizada] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_medicacion_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_medicacion_verifico] FOREIGN KEY ([verificada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [FK_medicacion_registro] FOREIGN KEY ([registrada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_medicacion_medicamento] CHECK (LEN(LTRIM(RTRIM([medicamento]))) > 0),
    CONSTRAINT [CK_medicacion_estado] CHECK ([estado] IN ('ACTIVA', 'SUSPENDIDA', 'ERRONEA')),
    CONSTRAINT [CK_medicacion_origen] CHECK ([origen] IN ('REGISTRADA_MEDICO', 'DECLARADA_PACIENTE')),
    CONSTRAINT [CK_medicacion_verificacion] CHECK (
      ([verificada] = 1 AND [verificada_por] IS NOT NULL AND [verificada_en] IS NOT NULL)
      OR ([verificada] = 0 AND [verificada_por] IS NULL AND [verificada_en] IS NULL AND [origen] = 'DECLARADA_PACIENTE')
    )
  );
  CREATE INDEX [IX_medicacion_paciente] ON [historia_medicacion] ([paciente_id], [estado]);
END
GO

IF OBJECT_ID(N'[historia_problemas]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_problemas] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [descripcion] nvarchar(300) NOT NULL,
    [cie11] varchar(20) NULL,
    [inicio] date NULL,
    [estado] varchar(10) NOT NULL CONSTRAINT [DF_problemas_estado] DEFAULT 'ACTIVO',
    [origen] varchar(20) NOT NULL,
    [verificada] bit NOT NULL,
    [verificada_por] uniqueidentifier NULL,
    [verificada_en] datetime2 NULL,
    [registrada_por] uniqueidentifier NOT NULL,
    [registrada_en] datetime2 NOT NULL CONSTRAINT [DF_problemas_registrada] DEFAULT SYSUTCDATETIME(),
    [actualizada_en] datetime2 NOT NULL CONSTRAINT [DF_problemas_actualizada] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_problemas_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_problemas_verifico] FOREIGN KEY ([verificada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [FK_problemas_registro] FOREIGN KEY ([registrada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_problemas_descripcion] CHECK (LEN(LTRIM(RTRIM([descripcion]))) > 0),
    CONSTRAINT [CK_problemas_estado] CHECK ([estado] IN ('ACTIVO', 'RESUELTO', 'ERRONEO')),
    CONSTRAINT [CK_problemas_origen] CHECK ([origen] IN ('REGISTRADA_MEDICO', 'DECLARADA_PACIENTE')),
    CONSTRAINT [CK_problemas_verificacion] CHECK (
      ([verificada] = 1 AND [verificada_por] IS NOT NULL AND [verificada_en] IS NOT NULL)
      OR ([verificada] = 0 AND [verificada_por] IS NULL AND [verificada_en] IS NULL AND [origen] = 'DECLARADA_PACIENTE')
    )
  );
  CREATE INDEX [IX_problemas_paciente] ON [historia_problemas] ([paciente_id], [estado]);
END
GO

IF OBJECT_ID(N'[historia_vacunas]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_vacunas] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [vacuna] nvarchar(120) NOT NULL,
    [dosis_numero] tinyint NOT NULL,
    [fecha] date NOT NULL,
    [lote] nvarchar(40) NULL,
    [estado] varchar(10) NOT NULL CONSTRAINT [DF_vacunas_estado] DEFAULT 'ACTIVA',
    [origen] varchar(20) NOT NULL,
    [verificada] bit NOT NULL,
    [verificada_por] uniqueidentifier NULL,
    [verificada_en] datetime2 NULL,
    [registrada_por] uniqueidentifier NOT NULL,
    [registrada_en] datetime2 NOT NULL CONSTRAINT [DF_vacunas_registrada] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_vacunas_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_vacunas_verifico] FOREIGN KEY ([verificada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [FK_vacunas_registro] FOREIGN KEY ([registrada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_vacunas_vacuna] CHECK (LEN(LTRIM(RTRIM([vacuna]))) > 0),
    CONSTRAINT [CK_vacunas_dosis] CHECK ([dosis_numero] BETWEEN 1 AND 10),
    CONSTRAINT [CK_vacunas_estado] CHECK ([estado] IN ('ACTIVA', 'ERRONEA')),
    CONSTRAINT [CK_vacunas_origen] CHECK ([origen] IN ('REGISTRADA_MEDICO', 'DECLARADA_PACIENTE')),
    CONSTRAINT [CK_vacunas_verificacion] CHECK (
      ([verificada] = 1 AND [verificada_por] IS NOT NULL AND [verificada_en] IS NOT NULL)
      OR ([verificada] = 0 AND [verificada_por] IS NULL AND [verificada_en] IS NULL AND [origen] = 'DECLARADA_PACIENTE')
    )
  );
  CREATE INDEX [IX_vacunas_paciente] ON [historia_vacunas] ([paciente_id], [vacuna]);
END
GO

-- Vital signs are observations: a correction is a new row that points to the one it corrects.
IF OBJECT_ID(N'[historia_signos_vitales]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_signos_vitales] (
    [id] uniqueidentifier NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [cita_id] uniqueidentifier NOT NULL,
    [corrige_a] uniqueidentifier NULL,
    [motivo_correccion] nvarchar(500) NULL,
    [peso_kg] decimal(5, 1) NULL,
    [talla_cm] decimal(5, 1) NULL,
    [pa_sistolica] smallint NULL,
    [pa_diastolica] smallint NULL,
    [frecuencia_cardiaca] smallint NULL,
    [temperatura_c] decimal(4, 1) NULL,
    [saturacion] smallint NULL,
    [registrada_por] uniqueidentifier NOT NULL,
    [registrada_en] datetime2 NOT NULL CONSTRAINT [DF_vitales_registrada] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_vitales_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_vitales_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id]),
    CONSTRAINT [FK_vitales_corrige] FOREIGN KEY ([corrige_a]) REFERENCES [historia_signos_vitales] ([id]),
    CONSTRAINT [FK_vitales_registro] FOREIGN KEY ([registrada_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_vitales_alguna] CHECK (
      [peso_kg] IS NOT NULL OR [talla_cm] IS NOT NULL OR [pa_sistolica] IS NOT NULL
      OR [pa_diastolica] IS NOT NULL OR [frecuencia_cardiaca] IS NOT NULL
      OR [temperatura_c] IS NOT NULL OR [saturacion] IS NOT NULL
    ),
    CONSTRAINT [CK_vitales_peso] CHECK ([peso_kg] IS NULL OR [peso_kg] BETWEEN 0.5 AND 300),
    CONSTRAINT [CK_vitales_talla] CHECK ([talla_cm] IS NULL OR [talla_cm] BETWEEN 30 AND 250),
    CONSTRAINT [CK_vitales_sistolica] CHECK ([pa_sistolica] IS NULL OR [pa_sistolica] BETWEEN 50 AND 260),
    CONSTRAINT [CK_vitales_diastolica] CHECK ([pa_diastolica] IS NULL OR [pa_diastolica] BETWEEN 30 AND 160),
    CONSTRAINT [CK_vitales_fc] CHECK ([frecuencia_cardiaca] IS NULL OR [frecuencia_cardiaca] BETWEEN 20 AND 250),
    CONSTRAINT [CK_vitales_temperatura] CHECK ([temperatura_c] IS NULL OR [temperatura_c] BETWEEN 30 AND 45),
    CONSTRAINT [CK_vitales_saturacion] CHECK ([saturacion] IS NULL OR [saturacion] BETWEEN 50 AND 100),
    CONSTRAINT [CK_vitales_correccion] CHECK ([corrige_a] IS NULL OR LEN(LTRIM(RTRIM([motivo_correccion]))) > 0)
  );
  CREATE INDEX [IX_vitales_paciente] ON [historia_signos_vitales] ([paciente_id], [registrada_en]);
END
GO

-- Append-only log of every change to the history: values before and after, never updated or deleted.
IF OBJECT_ID(N'[historia_eventos]', N'U') IS NULL
BEGIN
  CREATE TABLE [historia_eventos] (
    [id] bigint IDENTITY(1, 1) NOT NULL PRIMARY KEY,
    [paciente_id] uniqueidentifier NOT NULL,
    [entidad] varchar(40) NOT NULL,
    [entidad_id] uniqueidentifier NOT NULL,
    [accion] varchar(20) NOT NULL,
    [actor_usuario_id] uniqueidentifier NOT NULL,
    [motivo] nvarchar(500) NULL,
    [antes] nvarchar(max) NULL,
    [despues] nvarchar(max) NULL,
    [ocurrido_en] datetime2 NOT NULL CONSTRAINT [DF_historia_eventos_ocurrido] DEFAULT SYSUTCDATETIME(),
    CONSTRAINT [FK_historia_eventos_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id]),
    CONSTRAINT [FK_historia_eventos_actor] FOREIGN KEY ([actor_usuario_id]) REFERENCES [usuarios] ([id])
  );
  CREATE INDEX [IX_historia_eventos_paciente] ON [historia_eventos] ([paciente_id], [ocurrido_en]);
END
GO

IF OBJECT_ID(N'[TR_historia_eventos_inmutable]', N'TR') IS NULL
  EXEC (N'CREATE TRIGGER [TR_historia_eventos_inmutable] ON [historia_eventos]
          AFTER UPDATE, DELETE AS
          BEGIN
            RAISERROR (N''historia_eventos is append-only'', 16, 1);
            ROLLBACK TRANSACTION;
          END');
GO

MERGE [permisos] AS target
USING (VALUES
  ('historia.leer',       'historia', 'leer',       N'Leer la historia clínica de los pacientes que atiende'),
  ('historia.registrar',  'historia', 'registrar',  N'Registrar y verificar entradas de la historia clínica de sus pacientes'),
  ('historia.declarar',   'historia', 'declarar',   N'Declarar sus propias alergias, antecedentes, medicación y vacunas'),
  ('historia.leer_propia','historia', 'leer_propia',N'Leer su propia historia clínica')
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
    ('MEDICO',   'historia.leer'),
    ('MEDICO',   'historia.registrar'),
    ('PACIENTE', 'historia.declarar'),
    ('PACIENTE', 'historia.leer_propia')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
