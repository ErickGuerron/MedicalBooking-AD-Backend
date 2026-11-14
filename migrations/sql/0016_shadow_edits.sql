-- =====================================================================
-- Physician edits of a shadow finding (clinical findings S7, S14, T6).
-- Append-only: each row is one action of one physician on one finding of one immutable snapshot.
-- The snapshot and its items are never changed; the suggestion stays visible.
-- A trigger rejects UPDATE and DELETE, so immutability does not depend on the application code.
-- =====================================================================
CREATE TABLE [hallazgos_sombra_ediciones] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [snapshot_id] uniqueidentifier NOT NULL,
  [hallazgo] varchar(20) NOT NULL,
  [accion] varchar(20) NOT NULL,
  [categoria_anterior] varchar(60) NOT NULL,
  [categoria_nueva] varchar(60) NOT NULL,
  [motivo] nvarchar(500) NOT NULL,
  [medico_user_id] uniqueidentifier NOT NULL,
  [creado_en] datetime2 NOT NULL,
  CONSTRAINT [CK_ediciones_hallazgo] CHECK ([hallazgo] IN ('ANEMIA', 'HIERRO', 'DIABETES')),
  CONSTRAINT [CK_ediciones_accion] CHECK ([accion] IN ('CONFIRMAR', 'DESCARTAR', 'MODIFICAR'))
)
GO

CREATE INDEX [IX_ediciones_snapshot_hallazgo] ON [hallazgos_sombra_ediciones] ([snapshot_id], [hallazgo], [creado_en])
GO

ALTER TABLE [hallazgos_sombra_ediciones] ADD CONSTRAINT [FK_ediciones_snapshot] FOREIGN KEY ([snapshot_id]) REFERENCES [hallazgos_sombra_snapshots] ([id])
GO
ALTER TABLE [hallazgos_sombra_ediciones] ADD CONSTRAINT [FK_ediciones_medico] FOREIGN KEY ([medico_user_id]) REFERENCES [usuarios] ([id])
GO

CREATE TRIGGER [TR_ediciones_inmutables] ON [hallazgos_sombra_ediciones] AFTER UPDATE, DELETE AS
BEGIN
  SET NOCOUNT ON;
  THROW 51000, 'Shadow snapshots are immutable', 1;
END
GO
