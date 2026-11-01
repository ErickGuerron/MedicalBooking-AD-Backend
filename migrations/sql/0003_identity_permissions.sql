-- =====================================================================
-- E1 identity: credentials and the permission matrix.
-- ADDITION (not in the DBML): the DBML has no column for credentials.
-- Passwords are stored only as Argon2id hashes in this separate table, so
-- usuarios stays provider-agnostic (auth_provider_id may be used later).
-- Idempotent: safe to run more than once.
-- =====================================================================

IF OBJECT_ID(N'dbo.credenciales_usuario', N'U') IS NULL
BEGIN
  CREATE TABLE [credenciales_usuario] (
    [usuario_id] uniqueidentifier NOT NULL PRIMARY KEY,
    -- Argon2id encoded hash; never the plain password.
    [password_hash] varchar(255) NOT NULL,
    [actualizado_en] datetime2 NOT NULL,
    CONSTRAINT [FK_credenciales_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_credenciales_hash] CHECK (LEN([password_hash]) > 0)
  );
END
GO

MERGE [permisos] AS target
USING (VALUES
  ('pacientes.leer_propio',      'pacientes',  'leer_propio',      N'Leer el propio perfil de paciente'),
  ('pacientes.leer_con_acceso',  'pacientes',  'leer_con_acceso',  N'Leer pacientes con acceso contextual vigente'),
  ('pacientes.leer_cualquiera',  'pacientes',  'leer_cualquiera',  N'Leer cualquier paciente (administracion)'),
  ('citas.reservar',             'citas',      'reservar',         N'Reservar una cita propia'),
  ('citas.leer_propias',         'citas',      'leer_propias',     N'Consultar las citas propias'),
  ('ordenes.crear',              'ordenes',    'crear',            N'Crear ordenes de laboratorio'),
  ('resultados.registrar',       'resultados', 'registrar',        N'Registrar resultados de laboratorio'),
  ('resultados.leer_propios',    'resultados', 'leer_propios',     N'Leer los resultados propios'),
  ('resultados.leer_con_acceso', 'resultados', 'leer_con_acceso',  N'Leer resultados con acceso contextual vigente'),
  ('revisiones.registrar',       'revisiones', 'registrar',        N'Registrar la revision medica'),
  ('usuarios.administrar',       'usuarios',   'administrar',      N'Crear y administrar usuarios y roles')
) AS source ([codigo], [recurso], [accion], [descripcion])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([codigo], [recurso], [accion], [descripcion])
  VALUES (source.[codigo], source.[recurso], source.[accion], source.[descripcion]);
GO

-- Role-to-permission matrix. Business rules (ownership, consent) are enforced in the
-- application layer; this table only grants capabilities.
MERGE [roles_permisos] AS target
USING (
  SELECT r.[id] AS [rol_id], p.[id] AS [permiso_id]
  FROM (VALUES
    ('PACIENTE',      'pacientes.leer_propio'),
    ('PACIENTE',      'citas.reservar'),
    ('PACIENTE',      'citas.leer_propias'),
    ('PACIENTE',      'resultados.leer_propios'),
    ('LABORATORISTA', 'resultados.registrar'),
    ('MEDICO',        'pacientes.leer_con_acceso'),
    ('MEDICO',        'ordenes.crear'),
    ('MEDICO',        'resultados.leer_con_acceso'),
    ('MEDICO',        'revisiones.registrar'),
    ('ADMINISTRADOR', 'pacientes.leer_cualquiera'),
    ('ADMINISTRADOR', 'usuarios.administrar')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO
