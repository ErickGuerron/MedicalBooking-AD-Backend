"""Treatments (S4-S6): a structured treatment per appointment and its append-only follow-ups.

Revision ID: 0029_treatments
Revises: 0028_sincronizacion_watermarks

Creates two new tables only. No existing table changes and no permission is seeded: the treating
doctor uses revisiones.registrar and the reads use the existing resultados.* permissions
(see treatments/domain). The script is inline so the whole change stays in this one file.
"""

from __future__ import annotations

from alembic import op

from medicalbooking.shared.infrastructure.tsql import split_batches

revision = "0029_treatments"
down_revision = "0028_sincronizacion_watermarks"
branch_labels = None
depends_on = None

_UPGRADE = """
IF OBJECT_ID(N'dbo.tratamientos', N'U') IS NULL
BEGIN
  CREATE TABLE [tratamientos] (
    [id] uniqueidentifier NOT NULL CONSTRAINT [PK_tratamientos] PRIMARY KEY,
    [cita_id] uniqueidentifier NOT NULL,
    [descripcion] nvarchar(1000) NOT NULL,
    [estado] varchar(20) NOT NULL CONSTRAINT [DF_tratamientos_estado] DEFAULT 'ACTIVO',
    [fecha_inicio] date NOT NULL,
    [fecha_fin] date NULL,
    [creado_en] datetime2 NOT NULL,
    [actualizado_en] datetime2 NOT NULL,
    CONSTRAINT [FK_tratamientos_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id]),
    CONSTRAINT [CK_tratamientos_descripcion] CHECK (LEN(LTRIM(RTRIM([descripcion]))) > 0),
    CONSTRAINT [CK_tratamientos_estado]
      CHECK ([estado] IN ('ACTIVO', 'FINALIZADO', 'SUSPENDIDO')),
    CONSTRAINT [CK_tratamientos_fechas]
      CHECK ([fecha_fin] IS NULL OR [fecha_fin] >= [fecha_inicio]),
    CONSTRAINT [CK_tratamientos_finalizado]
      CHECK ([estado] <> 'FINALIZADO' OR [fecha_fin] IS NOT NULL)
  );
  CREATE INDEX [IX_tratamientos_cita] ON [tratamientos] ([cita_id], [fecha_inicio]);
END
GO

IF OBJECT_ID(N'dbo.tratamientos_seguimientos', N'U') IS NULL
BEGIN
  CREATE TABLE [tratamientos_seguimientos] (
    -- Insertion order: notes can share one registration instant, so reads sort by this sequence.
    [secuencia] bigint IDENTITY(1, 1) NOT NULL,
    [id] uniqueidentifier NOT NULL CONSTRAINT [PK_tratamientos_seguimientos] PRIMARY KEY,
    [tratamiento_id] uniqueidentifier NOT NULL,
    [nota] nvarchar(2000) NOT NULL,
    [registrado_por] uniqueidentifier NOT NULL,
    [registrado_en] datetime2 NOT NULL,
    CONSTRAINT [UX_tratamientos_seguimientos_secuencia] UNIQUE ([secuencia]),
    CONSTRAINT [FK_seguimientos_tratamiento]
      FOREIGN KEY ([tratamiento_id]) REFERENCES [tratamientos] ([id]),
    CONSTRAINT [FK_seguimientos_usuario]
      FOREIGN KEY ([registrado_por]) REFERENCES [usuarios] ([id]),
    CONSTRAINT [CK_seguimientos_nota] CHECK (LEN(LTRIM(RTRIM([nota]))) > 0)
  );
  CREATE INDEX [IX_seguimientos_tratamiento]
    ON [tratamientos_seguimientos] ([tratamiento_id], [secuencia]);
END
GO

-- Follow-up notes are append-only (S5): the database refuses any change to a written note.
IF OBJECT_ID(N'dbo.TR_tratamientos_seguimientos_inmutables', N'TR') IS NULL
  EXEC ('CREATE TRIGGER [TR_tratamientos_seguimientos_inmutables]
         ON [tratamientos_seguimientos] AFTER UPDATE, DELETE AS
         BEGIN
           THROW 51000, ''Follow-up notes are append-only'', 1;
         END');
GO
"""

_DOWNGRADE = """
IF OBJECT_ID(N'dbo.tratamientos_seguimientos', N'U') IS NOT NULL
  DROP TABLE [tratamientos_seguimientos];
GO

IF OBJECT_ID(N'dbo.tratamientos', N'U') IS NOT NULL
  DROP TABLE [tratamientos];
GO
"""


def upgrade() -> None:
    connection = op.get_bind()
    for batch in split_batches(_UPGRADE):
        connection.exec_driver_sql(batch)


def downgrade() -> None:
    # Treatments are clinical records written by doctors. Dropping them is a deliberate, reversible
    # schema rollback: the tables hold only data created by this feature.
    connection = op.get_bind()
    for batch in split_batches(_DOWNGRADE):
        connection.exec_driver_sql(batch)
