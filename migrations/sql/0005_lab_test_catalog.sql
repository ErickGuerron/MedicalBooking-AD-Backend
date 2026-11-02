-- =====================================================================
-- E3 laboratory: synthetic test catalog (spec 8.1: synthetic seed data).
-- Idempotent: MERGE by codigo. Every row is SINTETICO and not a clinical reference.
-- =====================================================================

MERGE [tipos_prueba_laboratorio] AS target
USING (VALUES
  ('HEMO-SINT', N'Hemograma completo (SINTETICO)', N'Prueba sintetica para pruebas del sistema', N'10^3/uL'),
  ('GLUC-SINT', N'Glucosa en ayunas (SINTETICO)', N'Prueba sintetica para pruebas del sistema', N'mg/dL'),
  ('LIPI-SINT', N'Perfil lipidico (SINTETICO)', N'Prueba sintetica para pruebas del sistema', N'mg/dL')
) AS source ([codigo], [nombre], [descripcion], [unidad_predeterminada])
  ON target.[codigo] = source.[codigo]
WHEN NOT MATCHED THEN
  INSERT ([id], [codigo], [nombre], [descripcion], [unidad_predeterminada], [activo])
  VALUES (NEWID(), source.[codigo], source.[nombre], source.[descripcion],
          source.[unidad_predeterminada], 1);
GO
