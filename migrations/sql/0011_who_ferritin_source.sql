-- =====================================================================
-- Source row: WHO guideline on use of ferritin concentrations to assess iron status (2020).
-- Cited by the iron deficiency cut-offs (Table 1). Licence CC BY-NC-SA 3.0 IGO.
-- Idempotent: inserted once, by code.
-- =====================================================================
IF NOT EXISTS (SELECT 1 FROM [conocimiento_fuentes]
               WHERE [codigo] = 'WHO-FERRITIN-2020'
                  OR [id] = 'f0000000-0000-4000-8000-00000000000a')
  INSERT INTO [conocimiento_fuentes] ([id], [codigo], [nombre], [organizacion], [url],
                                      [identificador_publicacion], [version_publicacion],
                                      [fecha_consulta], [vigente])
  VALUES ('f0000000-0000-4000-8000-00000000000a', 'WHO-FERRITIN-2020',
          N'WHO guideline on use of ferritin concentrations to assess iron status in individuals and populations',
          N'World Health Organization',
          N'https://www.who.int/publications/i/item/9789240000124',
          N'ISBN 978-92-4-000012-4', N'2020',
          '2026-11-07T00:00:00', 1);
GO
