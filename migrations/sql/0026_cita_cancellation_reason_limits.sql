-- Cancellation reason (doctor): the text can justify the cancellation in up to 1500
-- characters, and the code is restricted in SQL, not only in the application.
-- No existing row has a reason yet (0025 added the columns), so both changes are safe.
ALTER TABLE [citas] ALTER COLUMN [motivo_cancelacion_texto] nvarchar(1500) NULL;
GO
ALTER TABLE [citas] ADD CONSTRAINT [CK_citas_motivo_cancelacion_codigo]
  CHECK ([motivo_cancelacion_codigo] IS NULL
         OR [motivo_cancelacion_codigo] IN ('IMPREVISTO_MEDICO', 'OTRO_MEDICO'));
GO