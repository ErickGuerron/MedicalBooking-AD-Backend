-- =====================================================================
-- Clinical history catalogs: entries that name a diagnosis, a vaccine or an allergen must use a
-- code from these tables. The seed is a starter set; the full CIE-11 list is loaded separately.
-- Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'[catalogo_cie11]', N'U') IS NULL
BEGIN
  CREATE TABLE [catalogo_cie11] (
    [codigo] varchar(20) NOT NULL PRIMARY KEY,
    [titulo] nvarchar(300) NOT NULL,
    [activo] bit NOT NULL CONSTRAINT [DF_cie11_activo] DEFAULT 1,
    CONSTRAINT [CK_cie11_titulo] CHECK (LEN(LTRIM(RTRIM([titulo]))) > 0)
  );
END
GO

IF OBJECT_ID(N'[catalogo_vacunas]', N'U') IS NULL
BEGIN
  CREATE TABLE [catalogo_vacunas] (
    [codigo] varchar(40) NOT NULL PRIMARY KEY,
    [nombre] nvarchar(120) NOT NULL,
    [activo] bit NOT NULL CONSTRAINT [DF_vacunas_cat_activo] DEFAULT 1,
    CONSTRAINT [CK_vacunas_cat_nombre] CHECK (LEN(LTRIM(RTRIM([nombre]))) > 0)
  );
END
GO

IF OBJECT_ID(N'[catalogo_sustancias_alergia]', N'U') IS NULL
BEGIN
  CREATE TABLE [catalogo_sustancias_alergia] (
    [codigo] varchar(40) NOT NULL PRIMARY KEY,
    [nombre] nvarchar(150) NOT NULL,
    [categoria] varchar(20) NOT NULL,
    [activo] bit NOT NULL CONSTRAINT [DF_sustancias_activo] DEFAULT 1,
    CONSTRAINT [CK_sustancias_nombre] CHECK (LEN(LTRIM(RTRIM([nombre]))) > 0),
    CONSTRAINT [CK_sustancias_categoria] CHECK ([categoria] IN ('MEDICAMENTO', 'ALIMENTO', 'LATEX', 'AMBIENTAL', 'OTRA'))
  );
END
GO

-- Starter seed. Idempotent MERGE by code; never overwrites a name a catalog maintainer changed.
MERGE [catalogo_cie11] AS target
USING (VALUES
  ('BA00', N'Hipertensión esencial (primaria)'),
  ('5A10', N'Diabetes mellitus tipo 1'),
  ('5A11', N'Diabetes mellitus tipo 2'),
  ('CA23', N'Asma')
) AS source ([codigo], [titulo]) ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN INSERT ([codigo], [titulo]) VALUES (source.[codigo], source.[titulo]);
GO

MERGE [catalogo_vacunas] AS target
USING (VALUES
  ('INFLUENZA', N'Influenza estacional'),
  ('COVID19', N'COVID-19'),
  ('HEPATITIS_B', N'Hepatitis B'),
  ('TDAP', N'Tétanos, difteria y tos ferina (Tdap)'),
  ('SRP', N'Sarampión, rubéola y parotiditis (SRP)'),
  ('VARICELA', N'Varicela'),
  ('FIEBRE_AMARILLA', N'Fiebre amarilla'),
  ('NEUMOCOCO', N'Neumococo')
) AS source ([codigo], [nombre]) ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN INSERT ([codigo], [nombre]) VALUES (source.[codigo], source.[nombre]);
GO

MERGE [catalogo_sustancias_alergia] AS target
USING (VALUES
  ('PENICILINA', N'Penicilina', 'MEDICAMENTO'),
  ('AMOXICILINA', N'Amoxicilina', 'MEDICAMENTO'),
  ('AINE', N'Antiinflamatorios no esteroideos (AINE)', 'MEDICAMENTO'),
  ('SULFONAMIDAS', N'Sulfonamidas', 'MEDICAMENTO'),
  ('CACAHUATE', N'Maní (cacahuate)', 'ALIMENTO'),
  ('HUEVO', N'Huevo', 'ALIMENTO'),
  ('LECHE', N'Leche de vaca', 'ALIMENTO'),
  ('MARISCOS', N'Mariscos', 'ALIMENTO'),
  ('LATEX', N'Látex', 'LATEX'),
  ('POLEN', N'Polen', 'AMBIENTAL'),
  ('ACAROS', N'Ácaros del polvo', 'AMBIENTAL'),
  ('PICADURA_INSECTO', N'Picadura de insecto', 'AMBIENTAL')
) AS source ([codigo], [nombre], [categoria]) ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN INSERT ([codigo], [nombre], [categoria]) VALUES (source.[codigo], source.[nombre], source.[categoria]);
GO

-- Entries reference the catalogs. The name stays as a snapshot on the entry, so a later catalog
-- change never rewrites what a doctor recorded.
IF COL_LENGTH(N'historia_alergias', N'sustancia_codigo') IS NULL
  ALTER TABLE [historia_alergias] ADD [sustancia_codigo] varchar(40) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE [name] = N'FK_alergias_sustancia')
  ALTER TABLE [historia_alergias] ADD CONSTRAINT [FK_alergias_sustancia]
    FOREIGN KEY ([sustancia_codigo]) REFERENCES [catalogo_sustancias_alergia] ([codigo]);
GO

IF COL_LENGTH(N'historia_vacunas', N'vacuna_codigo') IS NULL
  ALTER TABLE [historia_vacunas] ADD [vacuna_codigo] varchar(40) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE [name] = N'FK_vacunas_catalogo')
  ALTER TABLE [historia_vacunas] ADD CONSTRAINT [FK_vacunas_catalogo]
    FOREIGN KEY ([vacuna_codigo]) REFERENCES [catalogo_vacunas] ([codigo]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE [name] = N'FK_antecedentes_cie11')
  ALTER TABLE [historia_antecedentes] ADD CONSTRAINT [FK_antecedentes_cie11]
    FOREIGN KEY ([cie11]) REFERENCES [catalogo_cie11] ([codigo]);
GO
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys WHERE [name] = N'FK_problemas_cie11')
  ALTER TABLE [historia_problemas] ADD CONSTRAINT [FK_problemas_cie11]
    FOREIGN KEY ([cie11]) REFERENCES [catalogo_cie11] ([codigo]);
GO
