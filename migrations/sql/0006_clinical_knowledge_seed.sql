-- =====================================================================
-- E5 clinical knowledge: permissions and SYNTHETIC approved seed (spec 8.1).
-- Every knowledge row here is SINTETICO: it is a test fixture, not a clinical reference.
-- Idempotent: MERGE/IF NOT EXISTS by fixed identifiers.
-- =====================================================================

MERGE [permisos] AS target
USING (VALUES
  ('conocimiento.leer',        'conocimiento', 'leer',        N'Leer conocimiento clinico validado'),
  ('conocimiento.administrar', 'conocimiento', 'administrar', N'Crear analitos y borradores de referencias y reglas'),
  ('conocimiento.validar',     'conocimiento', 'validar',     N'Validar referencias y reglas clinicas')
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
    ('MEDICO',        'conocimiento.leer'),
    ('MEDICO',        'conocimiento.validar'),
    ('LABORATORISTA', 'conocimiento.leer'),
    ('ADMINISTRADOR', 'conocimiento.leer'),
    ('ADMINISTRADOR', 'conocimiento.administrar')
  ) AS v ([rol], [permiso])
  JOIN [roles] r ON r.[nombre] = v.[rol]
  JOIN [permisos] p ON p.[codigo] = v.[permiso]
) AS source
  ON target.[rol_id] = source.[rol_id] AND target.[permiso_id] = source.[permiso_id]
WHEN NOT MATCHED THEN
  INSERT ([rol_id], [permiso_id]) VALUES (source.[rol_id], source.[permiso_id]);
GO

-- Synthetic validator: a sign-off placeholder without credentials, so nobody can log in as it.
IF NOT EXISTS (SELECT 1 FROM [usuarios] WHERE [id] = '5e5e5e5e-0000-4000-8000-000000000005')
  INSERT INTO [usuarios] ([id], [email], [nombre], [apellido], [estado], [creado_en], [actualizado_en])
  VALUES ('5e5e5e5e-0000-4000-8000-000000000005', N'validador.sintetico@synthetic.test',
          N'Validador', N'SINTETICO', 'INACTIVO', SYSUTCDATETIME(), SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes] WHERE [id] = 'f0000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion],
                                      [identificador_publicacion], [vigente])
  VALUES ('f0000000-0000-4000-8000-000000000001', 'FUENTE-SINTETICA-E5',
          N'Fuente sintetica de prueba (SINTETICO)', N'MedicalBooking tests',
          N'SINTETICO-E5', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = 'a0000000-0000-4000-8000-00000000b001')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [descripcion], [unidad_canonica],
                                       [tipo_valor], [activo], [creado_en], [actualizado_en])
  VALUES ('a0000000-0000-4000-8000-00000000b001', N'Hemoglobina (SINTETICO)',
          N'Analito sintetico para pruebas del sistema', 'g/dL', 'NUMERICO', 1,
          SYSUTCDATETIME(), SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = 'a0000000-0000-4000-8000-00000000b002')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [descripcion], [unidad_canonica],
                                       [tipo_valor], [activo], [creado_en], [actualizado_en])
  VALUES ('a0000000-0000-4000-8000-00000000b002', N'Glucosa (SINTETICO)',
          N'Analito sintetico para pruebas del sistema', 'mg/dL', 'NUMERICO', 1,
          SYSUTCDATETIME(), SYSUTCDATETIME());
GO

-- Link the synthetic analytes to the E3 synthetic test types.
INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id], [analito_id], [obligatorio], [orden_presentacion])
SELECT t.[id], 'a0000000-0000-4000-8000-00000000b001', 1, 1
FROM [tipos_prueba_laboratorio] t
WHERE t.[codigo] = 'HEMO-SINT'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] p
                  WHERE p.[tipo_prueba_id] = t.[id] AND p.[analito_id] = 'a0000000-0000-4000-8000-00000000b001');
INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id], [analito_id], [obligatorio], [orden_presentacion])
SELECT t.[id], 'a0000000-0000-4000-8000-00000000b002', 1, 1
FROM [tipos_prueba_laboratorio] t
WHERE t.[codigo] = 'GLUC-SINT'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] p
                  WHERE p.[tipo_prueba_id] = t.[id] AND p.[analito_id] = 'a0000000-0000-4000-8000-00000000b002');
GO

-- One validated reference per analyte, valid for everyone (no sex, age, method or context).
IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c0000000-0000-4000-8000-00000000d001')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [referencia_min], [referencia_max], [fecha_inicio],
                                          [estado], [validado_por], [validado_en])
  VALUES ('c0000000-0000-4000-8000-00000000d001', 'a0000000-0000-4000-8000-00000000b001',
          'f0000000-0000-4000-8000-000000000001', 1, 'g/dL', 12.0, 17.5, '2020-01-01',
          'VALIDADA', '5e5e5e5e-0000-4000-8000-000000000005', '2026-01-01T00:00:00');
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_referencias] WHERE [id] = 'c0000000-0000-4000-8000-00000000d002')
  INSERT INTO [conocimiento_referencias] ([id], [analito_id], [fuente_id], [version], [unidad],
                                          [referencia_min], [referencia_max], [fecha_inicio],
                                          [estado], [validado_por], [validado_en])
  VALUES ('c0000000-0000-4000-8000-00000000d002', 'a0000000-0000-4000-8000-00000000b002',
          'f0000000-0000-4000-8000-000000000001', 1, 'mg/dL', 70.0, 99.0, '2020-01-01',
          'VALIDADA', '5e5e5e5e-0000-4000-8000-000000000005', '2026-01-01T00:00:00');
GO

-- Two validated declarative rules: one numeric criterion, one criterion on the classification.
IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas] WHERE [id] = 'e0000000-0000-4000-8000-00000000f001')
  INSERT INTO [conocimiento_reglas] ([id], [codigo], [version], [nombre], [descripcion], [tipo],
                                     [expresion_logica], [mensaje_propuesto], [prioridad], [estado],
                                     [vigente_desde], [aprobado_por], [aprobado_en], [creado_en])
  VALUES ('e0000000-0000-4000-8000-00000000f001', 'HB-BAJA-SINT', 1,
          N'Hemoglobina baja (SINTETICO)', N'Regla sintetica de prueba', 'HALLAZGO', 'TODOS',
          N'Hemoglobina por debajo del rango de referencia sintetico. Requiere revision medica; no es un diagnostico.',
          'INFORMATIVA', 'VALIDADA', '2020-01-01', '5e5e5e5e-0000-4000-8000-000000000005',
          '2026-01-01T00:00:00', SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas] WHERE [id] = 'e0000000-0000-4000-8000-00000000f002')
  INSERT INTO [conocimiento_reglas] ([id], [codigo], [version], [nombre], [descripcion], [tipo],
                                     [expresion_logica], [mensaje_propuesto], [prioridad], [estado],
                                     [vigente_desde], [aprobado_por], [aprobado_en], [creado_en])
  VALUES ('e0000000-0000-4000-8000-00000000f002', 'GLU-FUERA-SINT', 1,
          N'Glucosa fuera de referencia (SINTETICO)', N'Regla sintetica de prueba', 'ALERTA', 'TODOS',
          N'Glucosa fuera del rango de referencia sintetico. Requiere revision medica; no es un diagnostico.',
          'INFORMATIVA', 'VALIDADA', '2020-01-01', '5e5e5e5e-0000-4000-8000-000000000005',
          '2026-01-01T00:00:00', SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] WHERE [id] = 'b0000000-0000-4000-8000-000000000001')
  INSERT INTO [conocimiento_reglas_criterios] ([id], [regla_id], [analito_id], [operador],
                                               [umbral_numerico], [unidad], [orden_criterio])
  VALUES ('b0000000-0000-4000-8000-000000000001', 'e0000000-0000-4000-8000-00000000f001',
          'a0000000-0000-4000-8000-00000000b001', 'LT', 12.0, 'g/dL', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_criterios] WHERE [id] = 'b0000000-0000-4000-8000-000000000002')
  INSERT INTO [conocimiento_reglas_criterios] ([id], [regla_id], [analito_id], [operador],
                                               [unidad], [orden_criterio])
  VALUES ('b0000000-0000-4000-8000-000000000002', 'e0000000-0000-4000-8000-00000000f002',
          'a0000000-0000-4000-8000-00000000b002', 'FUERA_REFERENCIA', 'mg/dL', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] WHERE [regla_id] = 'e0000000-0000-4000-8000-00000000f001')
  INSERT INTO [conocimiento_reglas_fuentes] ([regla_id], [fuente_id])
  VALUES ('e0000000-0000-4000-8000-00000000f001', 'f0000000-0000-4000-8000-000000000001');
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_reglas_fuentes] WHERE [regla_id] = 'e0000000-0000-4000-8000-00000000f002')
  INSERT INTO [conocimiento_reglas_fuentes] ([regla_id], [fuente_id])
  VALUES ('e0000000-0000-4000-8000-00000000f002', 'f0000000-0000-4000-8000-000000000001');
GO

-- One proposal per order (spec S14). The application locks the order; this index is the
-- database's own guarantee, so a race that bypasses the lock still cannot create two.
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name = N'UX_propuestas_orden')
  CREATE UNIQUE INDEX [UX_propuestas_orden] ON [propuestas_clinicas] ([orden_id]);
GO
