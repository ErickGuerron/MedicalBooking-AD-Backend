-- =====================================================================
-- Immutable snapshots of the shadow findings (feature doc S9, decision L28 / explicit action).
-- A snapshot is written only by an explicit action of the ordering doctor. Each call creates a new
-- snapshot; earlier snapshots are never changed. Triggers reject UPDATE and DELETE on every table,
-- so immutability does not depend on the application code.
-- The findings never change a decision: modo is always SOMBRA and modifica_decision is always 0.
-- =====================================================================
CREATE TABLE [hallazgos_sombra_snapshots] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [orden_id] uniqueidentifier NOT NULL,
  [version_regla] varchar(60) NOT NULL,
  [modo] varchar(10) NOT NULL CONSTRAINT [DF_snapshots_modo] DEFAULT 'SOMBRA',
  [modifica_decision] bit NOT NULL CONSTRAINT [DF_snapshots_modifica] DEFAULT 0,
  [creado_por] uniqueidentifier NOT NULL,
  [creado_en] datetime2 NOT NULL,
  CONSTRAINT [CK_snapshots_modo] CHECK ([modo] = 'SOMBRA'),
  CONSTRAINT [CK_snapshots_no_modifica] CHECK ([modifica_decision] = 0)
)
GO

CREATE INDEX [IX_snapshots_orden_creado] ON [hallazgos_sombra_snapshots] ([orden_id], [creado_en])
GO

CREATE TABLE [hallazgos_sombra_items] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [snapshot_id] uniqueidentifier NOT NULL,
  [hallazgo] varchar(20) NOT NULL,
  [categoria] varchar(60) NOT NULL,
  [corte_valor] decimal(18, 6) NULL,
  [corte_unidad] varchar(20) NULL,
  [fuente] varchar(120) NOT NULL,
  [motivo] nvarchar(500) NULL,
  CONSTRAINT [UQ_items_snapshot_hallazgo] UNIQUE ([snapshot_id], [hallazgo]),
  CONSTRAINT [CK_items_hallazgo] CHECK ([hallazgo] IN ('ANEMIA', 'HIERRO', 'DIABETES'))
)
GO

-- The evidence of a finding: the result id, value and unit it was read from.
CREATE TABLE [hallazgos_sombra_evidencias] (
  [item_id] uniqueidentifier NOT NULL,
  [resultado_id] uniqueidentifier NOT NULL,
  [rol] varchar(40) NOT NULL,
  [valor_observado] decimal(18, 6) NOT NULL,
  [unidad_observada] varchar(40) NOT NULL,
  CONSTRAINT [PK_sombra_evidencias] PRIMARY KEY ([item_id], [resultado_id])
)
GO

ALTER TABLE [hallazgos_sombra_snapshots] ADD CONSTRAINT [FK_snapshots_orden] FOREIGN KEY ([orden_id]) REFERENCES [ordenes_laboratorio] ([id])
GO
ALTER TABLE [hallazgos_sombra_snapshots] ADD CONSTRAINT [FK_snapshots_usuario] FOREIGN KEY ([creado_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [hallazgos_sombra_items] ADD CONSTRAINT [FK_items_snapshot] FOREIGN KEY ([snapshot_id]) REFERENCES [hallazgos_sombra_snapshots] ([id])
GO
ALTER TABLE [hallazgos_sombra_evidencias] ADD CONSTRAINT [FK_sombra_evidencias_item] FOREIGN KEY ([item_id]) REFERENCES [hallazgos_sombra_items] ([id])
GO
ALTER TABLE [hallazgos_sombra_evidencias] ADD CONSTRAINT [FK_sombra_evidencias_resultado] FOREIGN KEY ([resultado_id]) REFERENCES [resultados_laboratorio] ([id])
GO

CREATE TRIGGER [TR_snapshots_inmutables] ON [hallazgos_sombra_snapshots] AFTER UPDATE, DELETE AS
BEGIN
  SET NOCOUNT ON;
  THROW 51000, 'Shadow snapshots are immutable', 1;
END
GO

CREATE TRIGGER [TR_items_inmutables] ON [hallazgos_sombra_items] AFTER UPDATE, DELETE AS
BEGIN
  SET NOCOUNT ON;
  THROW 51000, 'Shadow snapshots are immutable', 1;
END
GO

CREATE TRIGGER [TR_evidencias_inmutables] ON [hallazgos_sombra_evidencias] AFTER UPDATE, DELETE AS
BEGIN
  SET NOCOUNT ON;
  THROW 51000, 'Shadow snapshots are immutable', 1;
END
GO
