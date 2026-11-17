-- Lipid profile: total cholesterol and triglycerides, linked to the LIPI-SINT test type.
-- Idempotent: safe to re-run; existing links are left as they are.

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = 'a0000000-0000-4000-8000-00000000b003')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [descripcion], [unidad_canonica],
                                       [tipo_valor], [activo], [creado_en], [actualizado_en])
  VALUES ('a0000000-0000-4000-8000-00000000b003', N'Colesterol total',
          N'Colesterol total en suero.', 'mg/dL', 'NUMERICO', 1,
          SYSUTCDATETIME(), SYSUTCDATETIME());
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos] WHERE [id] = 'a0000000-0000-4000-8000-00000000b004')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [descripcion], [unidad_canonica],
                                       [tipo_valor], [activo], [creado_en], [actualizado_en])
  VALUES ('a0000000-0000-4000-8000-00000000b004', N'Triglicéridos',
          N'Triglicéridos en suero.', 'mg/dL', 'NUMERICO', 1,
          SYSUTCDATETIME(), SYSUTCDATETIME());
GO

INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id], [analito_id], [obligatorio], [orden_presentacion])
SELECT t.[id], 'a0000000-0000-4000-8000-00000000b003', 1, 1
FROM [tipos_prueba_laboratorio] t
WHERE t.[codigo] = 'LIPI-SINT'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] p
                  WHERE p.[tipo_prueba_id] = t.[id] AND p.[analito_id] = 'a0000000-0000-4000-8000-00000000b003');
INSERT INTO [conocimiento_pruebas_analitos] ([tipo_prueba_id], [analito_id], [obligatorio], [orden_presentacion])
SELECT t.[id], 'a0000000-0000-4000-8000-00000000b004', 1, 2
FROM [tipos_prueba_laboratorio] t
WHERE t.[codigo] = 'LIPI-SINT'
  AND NOT EXISTS (SELECT 1 FROM [conocimiento_pruebas_analitos] p
                  WHERE p.[tipo_prueba_id] = t.[id] AND p.[analito_id] = 'a0000000-0000-4000-8000-00000000b004');
GO
