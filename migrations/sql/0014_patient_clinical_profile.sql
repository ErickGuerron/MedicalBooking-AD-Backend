-- =====================================================================
-- Patient clinical profile for the WHO 2024 haemoglobin adjustments (feature doc S12).
-- Self-reported by the patient: altitude of residence (Table 4) and smoking (Table 5).
-- NULL means unknown. The adjustment is never assumed to be zero.
-- A cigarette count needs a declared smoker; an unknown count is NULL.
-- =====================================================================
ALTER TABLE [pacientes] ADD [altitud_residencia_m] int NULL
GO
ALTER TABLE [pacientes] ADD [fumador] bit NULL
GO
ALTER TABLE [pacientes] ADD [cigarrillos_dia] int NULL
GO
ALTER TABLE [pacientes] ADD CONSTRAINT [CK_pacientes_altitud]
  CHECK ([altitud_residencia_m] IS NULL OR ([altitud_residencia_m] >= 0 AND [altitud_residencia_m] <= 8848))
GO
ALTER TABLE [pacientes] ADD CONSTRAINT [CK_pacientes_cigarrillos]
  CHECK ([cigarrillos_dia] IS NULL OR ([cigarrillos_dia] >= 0 AND [cigarrillos_dia] <= 200))
GO
ALTER TABLE [pacientes] ADD CONSTRAINT [CK_pacientes_fumador_cigarrillos]
  CHECK ([cigarrillos_dia] IS NULL OR [fumador] = 1)
GO

-- Only the patient edits their own profile. Ownership is enforced in the application layer.
IF NOT EXISTS (SELECT 1 FROM [permisos] WHERE [codigo] = 'pacientes.editar_propio')
  INSERT INTO [permisos] ([codigo], [recurso], [accion], [descripcion])
  VALUES ('pacientes.editar_propio', 'pacientes', 'editar_propio',
          N'Editar el propio perfil clinico (altitud y tabaquismo)')
GO

INSERT INTO [roles_permisos] ([rol_id], [permiso_id])
SELECT r.[id], p.[id]
FROM [roles] r
JOIN [permisos] p ON p.[codigo] = 'pacientes.editar_propio'
WHERE r.[nombre] = 'PACIENTE'
  AND NOT EXISTS (SELECT 1 FROM [roles_permisos] rp WHERE rp.[rol_id] = r.[id] AND rp.[permiso_id] = p.[id])
GO
