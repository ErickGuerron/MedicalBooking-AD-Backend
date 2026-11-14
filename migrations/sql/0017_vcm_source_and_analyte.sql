-- =====================================================================
-- Source and analyte for the MCV supporting signal (feature doc S20).
-- Source: MedlinePlus (U.S. National Library of Medicine), Spanish article 003642, "Conteo
-- sanguineo completo (CSC) prueba de sangre", reviewed 14/10/2024: VCM 80 a 100 fL.
-- The article states that normal ranges may vary between laboratories: this is a bibliographic
-- reference, not a validation by the laboratory.
-- The reference interval itself is NOT loaded here. It is created as a draft through the knowledge
-- API and only a physician can validate it (conocimiento.validar). Idempotent.
-- =====================================================================
IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes]
               WHERE [codigo] = 'MEDLINEPLUS-CSC-2024'
                  OR [id] = 'f0000000-0000-4000-8000-00000000000b')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_publicacion], [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-00000000000b', 'MEDLINEPLUS-CSC-2024',
          N'Conteo sanguineo completo (CSC) prueba de sangre',
          N'U.S. National Library of Medicine, MedlinePlus (version en espanol)',
          N'https://medlineplus.gov/spanish/ency/article/003642.htm',
          NULL, 'Revision 2024-10-14', '2024-10-14', '2026-11-09T00:00:00', 1);
GO

IF NOT EXISTS (SELECT 1 FROM [conocimiento_analitos]
               WHERE [id] = 'a3000000-0000-4000-8000-000000000001' OR [codigo_loinc] = '787-2')
  INSERT INTO [conocimiento_analitos] ([id], [nombre], [unidad_canonica], [tipo_valor], [codigo_loinc],
                                       [activo], [creado_en], [actualizado_en])
  VALUES ('a3000000-0000-4000-8000-000000000001', N'Volumen corpuscular medio (VCM)', 'fL', 'NUMERICO',
          '787-2', 1, SYSUTCDATETIME(), SYSUTCDATETIME());
GO
