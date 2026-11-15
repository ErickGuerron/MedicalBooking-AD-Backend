-- =====================================================================
-- Patient-requested lab exams: an order with no ordering doctor.
-- origen says who asked for the exam. A patient request has no doctor, so medico_id is NULL,
-- and the check keeps the two shapes apart: a doctor order always has its doctor.
-- Existing rows are doctor orders (the default). Idempotent: safe to run more than once.
-- =====================================================================

IF EXISTS (
  SELECT 1 FROM sys.columns
  WHERE object_id = OBJECT_ID(N'ordenes_laboratorio') AND name = N'medico_id' AND is_nullable = 0
)
BEGIN
  ALTER TABLE [ordenes_laboratorio] ALTER COLUMN [medico_id] uniqueidentifier NULL;
END
GO

IF COL_LENGTH('ordenes_laboratorio', 'origen') IS NULL
BEGIN
  ALTER TABLE [ordenes_laboratorio]
    ADD [origen] varchar(20) NOT NULL
      CONSTRAINT [DF_ordenes_origen] DEFAULT 'MEDICO';
END
GO

IF NOT EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = N'CK_ordenes_origen')
BEGIN
  ALTER TABLE [ordenes_laboratorio] ADD CONSTRAINT [CK_ordenes_origen] CHECK (
    ([origen] = 'MEDICO' AND [medico_id] IS NOT NULL)
    OR ([origen] = 'PACIENTE' AND [medico_id] IS NULL)
  );
END
GO

-- Permission for the patient to request an exam for himself or herself.
IF NOT EXISTS (SELECT 1 FROM [permisos] WHERE [codigo] = 'ordenes.solicitar_propias')
BEGIN
  INSERT INTO [permisos] ([codigo], [recurso], [accion], [descripcion])
  VALUES ('ordenes.solicitar_propias', 'ordenes', 'solicitar_propias',
          N'Solicitar examenes de laboratorio propios');
END
GO

IF NOT EXISTS (
  SELECT 1 FROM [roles_permisos] rp
  JOIN [roles] r ON r.[id] = rp.[rol_id]
  JOIN [permisos] p ON p.[id] = rp.[permiso_id]
  WHERE r.[nombre] = 'PACIENTE' AND p.[codigo] = 'ordenes.solicitar_propias'
)
BEGIN
  INSERT INTO [roles_permisos] ([rol_id], [permiso_id])
  SELECT r.[id], p.[id] FROM [roles] r, [permisos] p
  WHERE r.[nombre] = 'PACIENTE' AND p.[codigo] = 'ordenes.solicitar_propias';
END
GO
