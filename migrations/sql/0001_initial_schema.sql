-- =====================================================================
-- MedicalBooking - baseline schema (SQL Server / Azure SQL compatible)
-- Source: MedicalBooking_ampliado_conocimiento.dbml (no openFDA scope)
-- Reviewed corrections (see docs/adr and odd/tasks feature document):
--   * filtered unique index: one active appointment per schedule slot
--   * CHECK constraints: date ranges, money, state and enum domains
--   * composite FKs: immutable knowledge versions (id, version)
--   * assumed state domains are marked "ASSUMPTION" and need clinical sign-off
-- Schema: dbo for the MVP (logical areas documented in docs/adr/0002).
-- =====================================================================

-- ---------- 1. Identity and staff ----------

CREATE TABLE [usuarios] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [auth_provider_id] varchar(128) NULL,
  [email] varchar(255) NOT NULL UNIQUE,
  [telefono] varchar(25) NULL,
  [nombre] nvarchar(100) NOT NULL,
  [apellido] nvarchar(100) NOT NULL,
  [estado] varchar(20) NOT NULL CONSTRAINT [DF_usuarios_estado] DEFAULT 'ACTIVO',
  [email_verificado_en] datetime2 NULL,
  [bloqueado_hasta] datetime2 NULL,
  [creado_en] datetime2 NOT NULL,
  [actualizado_en] datetime2 NOT NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_usuarios_estado] CHECK ([estado] IN ('ACTIVO', 'INACTIVO', 'BLOQUEADO')),
  CONSTRAINT [CK_usuarios_email_no_vacio] CHECK (LEN(LTRIM(RTRIM([email]))) > 0)
)
GO

CREATE TABLE [roles] (
  [id] int IDENTITY(1, 1) NOT NULL PRIMARY KEY,
  [nombre] varchar(40) NOT NULL UNIQUE
)
GO

CREATE TABLE [usuarios_roles] (
  [usuario_id] uniqueidentifier NOT NULL,
  [rol_id] int NOT NULL,
  CONSTRAINT [PK_usuarios_roles] PRIMARY KEY ([usuario_id], [rol_id])
)
GO

CREATE TABLE [pacientes] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [usuario_id] uniqueidentifier NOT NULL UNIQUE,
  [tipo_documento] varchar(20) NULL,
  [numero_documento] varchar(30) NULL,
  [fecha_nacimiento] date NULL,
  [sexo_registrado] varchar(30) NULL,
  [contacto_emergencia] nvarchar(150) NULL,
  [telefono_emergencia] varchar(25) NULL,
  [creado_en] datetime2 NOT NULL
)
GO

CREATE UNIQUE INDEX [UX_pacientes_documento] ON [pacientes] ([tipo_documento], [numero_documento])
  WHERE [numero_documento] IS NOT NULL
GO

CREATE TABLE [especialidades] (
  [id] int IDENTITY(1, 1) NOT NULL PRIMARY KEY,
  [nombre] nvarchar(100) NOT NULL UNIQUE,
  [descripcion] nvarchar(500) NULL,
  [activo] bit NOT NULL CONSTRAINT [DF_especialidades_activo] DEFAULT 1
)
GO

CREATE TABLE [medicos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [usuario_id] uniqueidentifier NOT NULL UNIQUE,
  [registro_profesional] varchar(80) NOT NULL UNIQUE,
  [duracion_cita_min] int NOT NULL CONSTRAINT [DF_medicos_duracion] DEFAULT 30,
  [activo] bit NOT NULL CONSTRAINT [DF_medicos_activo] DEFAULT 1,
  CONSTRAINT [CK_medicos_duracion] CHECK ([duracion_cita_min] > 0)
)
GO

CREATE TABLE [medicos_especialidades] (
  [medico_id] uniqueidentifier NOT NULL,
  [especialidad_id] int NOT NULL,
  CONSTRAINT [PK_medicos_especialidades] PRIMARY KEY ([medico_id], [especialidad_id])
)
GO

CREATE TABLE [sedes] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [nombre] nvarchar(150) NOT NULL,
  [direccion] nvarchar(300) NULL,
  [zona_horaria] varchar(80) NOT NULL CONSTRAINT [DF_sedes_zona] DEFAULT 'America/Guayaquil',
  [activo] bit NOT NULL CONSTRAINT [DF_sedes_activo] DEFAULT 1
)
GO

CREATE TABLE [consultorios] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [sede_id] uniqueidentifier NOT NULL,
  [nombre] nvarchar(80) NOT NULL,
  [activo] bit NOT NULL CONSTRAINT [DF_consultorios_activo] DEFAULT 1,
  CONSTRAINT [UQ_consultorios_sede_nombre] UNIQUE ([sede_id], [nombre])
)
GO

-- ---------- 2. Services and availability ----------

CREATE TABLE [servicios_medicos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [especialidad_id] int NULL,
  [nombre] nvarchar(150) NOT NULL,
  [duracion_min] int NOT NULL,
  [precio_base] decimal(18, 2) NOT NULL,
  [moneda] char(3) NOT NULL CONSTRAINT [DF_servicios_moneda] DEFAULT 'USD',
  [activo] bit NOT NULL CONSTRAINT [DF_servicios_activo] DEFAULT 1,
  CONSTRAINT [CK_servicios_duracion] CHECK ([duracion_min] > 0),
  CONSTRAINT [CK_servicios_precio] CHECK ([precio_base] >= 0)
)
GO

CREATE TABLE [agendas] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [medico_id] uniqueidentifier NOT NULL,
  [consultorio_id] uniqueidentifier NOT NULL,
  [fecha_inicio] date NOT NULL,
  [fecha_fin] date NULL,
  [activa] bit NOT NULL CONSTRAINT [DF_agendas_activa] DEFAULT 1,
  CONSTRAINT [CK_agendas_fechas] CHECK ([fecha_fin] IS NULL OR [fecha_fin] >= [fecha_inicio])
)
GO

CREATE TABLE [horarios_disponibles] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [agenda_id] uniqueidentifier NOT NULL,
  [inicio_utc] datetime2 NOT NULL,
  [fin_utc] datetime2 NOT NULL,
  -- ASSUMPTION: DISPONIBLE, RESERVADO, BLOQUEADO (DBML names only the default)
  [estado] varchar(20) NOT NULL CONSTRAINT [DF_horarios_estado] DEFAULT 'DISPONIBLE',
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [UQ_horarios_agenda_inicio] UNIQUE ([agenda_id], [inicio_utc]),
  CONSTRAINT [CK_horarios_rango] CHECK ([fin_utc] > [inicio_utc]),
  CONSTRAINT [CK_horarios_estado] CHECK ([estado] IN ('DISPONIBLE', 'RESERVADO', 'BLOQUEADO'))
)
GO

CREATE INDEX [IX_horarios_agenda_estado_inicio] ON [horarios_disponibles] ([agenda_id], [estado], [inicio_utc])
GO

CREATE TABLE [citas] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [paciente_id] uniqueidentifier NOT NULL,
  [horario_id] uniqueidentifier NOT NULL,
  [servicio_id] uniqueidentifier NOT NULL,
  -- ASSUMPTION: active states are PENDIENTE_PAGO and CONFIRMADA (see filtered index)
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_citas_estado] DEFAULT 'PENDIENTE_PAGO',
  [motivo] nvarchar(500) NULL,
  [reservada_en] datetime2 NOT NULL,
  [confirmada_en] datetime2 NULL,
  [cancelada_en] datetime2 NULL,
  [expiracion_reserva] datetime2 NULL,
  [clave_idempotencia] varchar(100) NOT NULL UNIQUE,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_citas_estado] CHECK ([estado] IN ('PENDIENTE_PAGO', 'CONFIRMADA', 'COMPLETADA', 'CANCELADA', 'EXPIRADA', 'NO_ASISTIO'))
)
GO

-- Prevents double booking: at most one active appointment per schedule slot.
-- The conditional UPDATE on horarios_disponibles remains the first guard.
CREATE UNIQUE INDEX [UX_citas_horario_activa] ON [citas] ([horario_id])
  WHERE [estado] IN ('PENDIENTE_PAGO', 'CONFIRMADA')
GO

CREATE INDEX [IX_citas_paciente_reservada] ON [citas] ([paciente_id], [reservada_en])
GO

CREATE INDEX [IX_citas_horario_estado] ON [citas] ([horario_id], [estado])
GO

CREATE TABLE [historial_estados_cita] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [cita_id] uniqueidentifier NOT NULL,
  [usuario_id] uniqueidentifier NULL,
  [estado_anterior] varchar(25) NULL,
  [estado_nuevo] varchar(25) NOT NULL,
  [motivo] nvarchar(500) NULL,
  [cambiado_en] datetime2 NOT NULL,
  CONSTRAINT [CK_historial_citas_estado_nuevo] CHECK ([estado_nuevo] IN ('PENDIENTE_PAGO', 'CONFIRMADA', 'COMPLETADA', 'CANCELADA', 'EXPIRADA', 'NO_ASISTIO'))
)
GO

-- ---------- 3. Consultations and laboratory ----------

CREATE TABLE [consultas_medicas] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [cita_id] uniqueidentifier NOT NULL UNIQUE,
  [inicio_atencion] datetime2 NULL,
  [fin_atencion] datetime2 NULL,
  [motivo_consulta] nvarchar(1000) NULL,
  [observaciones] nvarchar(max) NULL,
  [plan_tratamiento] nvarchar(max) NULL,
  -- ASSUMPTION: ABIERTA, CERRADA
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_consultas_estado] DEFAULT 'ABIERTA',
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_consultas_estado] CHECK ([estado] IN ('ABIERTA', 'CERRADA')),
  CONSTRAINT [CK_consultas_rango] CHECK ([fin_atencion] IS NULL OR [inicio_atencion] IS NULL OR [fin_atencion] >= [inicio_atencion])
)
GO

CREATE TABLE [tipos_prueba_laboratorio] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [codigo] varchar(50) NOT NULL UNIQUE,
  [nombre] nvarchar(150) NOT NULL,
  [descripcion] nvarchar(500) NULL,
  [unidad_predeterminada] varchar(40) NULL,
  [activo] bit NOT NULL CONSTRAINT [DF_tipos_prueba_activo] DEFAULT 1
)
GO

CREATE TABLE [ordenes_laboratorio] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [paciente_id] uniqueidentifier NOT NULL,
  [medico_id] uniqueidentifier NOT NULL,
  [consulta_id] uniqueidentifier NULL,
  [fecha_orden] datetime2 NOT NULL,
  -- ASSUMPTION: NORMAL, URGENTE
  [prioridad] varchar(20) NOT NULL CONSTRAINT [DF_ordenes_prioridad] DEFAULT 'NORMAL',
  -- ASSUMPTION: SOLICITADA, EN_PROCESO, COMPLETADA, CANCELADA
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_ordenes_estado] DEFAULT 'SOLICITADA',
  [observaciones] nvarchar(1000) NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_ordenes_prioridad] CHECK ([prioridad] IN ('NORMAL', 'URGENTE')),
  CONSTRAINT [CK_ordenes_estado] CHECK ([estado] IN ('SOLICITADA', 'EN_PROCESO', 'COMPLETADA', 'CANCELADA'))
)
GO

CREATE TABLE [ordenes_laboratorio_detalle] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [orden_id] uniqueidentifier NOT NULL,
  [tipo_prueba_id] uniqueidentifier NOT NULL,
  -- ASSUMPTION: PENDIENTE, EN_PROCESO, COMPLETADO, CANCELADO
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_orden_detalle_estado] DEFAULT 'PENDIENTE',
  [fecha_toma_muestra] datetime2 NULL,
  [fecha_resultado] datetime2 NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_orden_detalle_estado] CHECK ([estado] IN ('PENDIENTE', 'EN_PROCESO', 'COMPLETADO', 'CANCELADO'))
)
GO

CREATE INDEX [IX_orden_detalle_orden_prueba] ON [ordenes_laboratorio_detalle] ([orden_id], [tipo_prueba_id])
GO

CREATE TABLE [resultados_laboratorio] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [detalle_orden_id] uniqueidentifier NOT NULL,
  [codigo_analito] varchar(60) NULL,
  [analito_id] uniqueidentifier NULL,
  [nombre_analito] nvarchar(150) NOT NULL,
  [valor_texto] nvarchar(300) NULL,
  [valor_numerico] decimal(18, 6) NULL,
  [unidad] varchar(40) NULL,
  [referencia_min] decimal(18, 6) NULL,
  [referencia_max] decimal(18, 6) NULL,
  -- Classification of the value only; never a diagnosis.
  [interpretacion] varchar(25) NULL,
  [referencia_aplicada_id] uniqueidentifier NULL,
  [referencia_version] int NULL,
  [metodo_analitico] nvarchar(150) NULL,
  [tipo_muestra] varchar(80) NULL,
  [validado_por] uniqueidentifier NULL,
  [validado_en] datetime2 NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_resultados_interpretacion] CHECK ([interpretacion] IS NULL OR [interpretacion] IN ('BAJO', 'DENTRO_REFERENCIA', 'ALTO', 'NO_EVALUABLE')),
  CONSTRAINT [CK_resultados_valor_presente] CHECK ([valor_numerico] IS NOT NULL OR [valor_texto] IS NOT NULL OR [interpretacion] = 'NO_EVALUABLE'),
  CONSTRAINT [CK_resultados_rango] CHECK ([referencia_min] IS NULL OR [referencia_max] IS NULL OR [referencia_min] <= [referencia_max]),
  -- A applied reference must always carry the exact version it was evaluated with.
  CONSTRAINT [CK_resultados_referencia_version] CHECK (
    ([referencia_aplicada_id] IS NULL AND [referencia_version] IS NULL)
    OR ([referencia_aplicada_id] IS NOT NULL AND [referencia_version] IS NOT NULL))
)
GO

CREATE INDEX [IX_resultados_detalle] ON [resultados_laboratorio] ([detalle_orden_id])
GO

CREATE TABLE [documentos_clinicos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [paciente_id] uniqueidentifier NOT NULL,
  [orden_id] uniqueidentifier NULL,
  [subido_por] uniqueidentifier NOT NULL,
  [tipo_documento] varchar(40) NOT NULL,
  [nombre_archivo] nvarchar(255) NOT NULL,
  -- Private storage reference only: no public URL and no binary content in the database.
  [proveedor_storage] varchar(30) NOT NULL,
  [ruta_storage] nvarchar(1000) NOT NULL,
  [tipo_mime] varchar(100) NULL,
  [hash_sha256] char(64) NULL,
  [tamano_bytes] bigint NULL,
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_documentos_estado] DEFAULT 'PENDIENTE',
  [clasificacion] varchar(30) NOT NULL CONSTRAINT [DF_documentos_clasificacion] DEFAULT 'CONFIDENCIAL',
  [version_documento] int NOT NULL CONSTRAINT [DF_documentos_version] DEFAULT 1,
  [creado_en] datetime2 NOT NULL,
  CONSTRAINT [CK_documentos_tamano] CHECK ([tamano_bytes] IS NULL OR [tamano_bytes] >= 0),
  CONSTRAINT [CK_documentos_version] CHECK ([version_documento] >= 1)
)
GO

CREATE TABLE [revisiones_laboratorio] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [orden_id] uniqueidentifier NOT NULL,
  [medico_id] uniqueidentifier NOT NULL,
  [cita_id] uniqueidentifier NULL,
  -- ASSUMPTION: PENDIENTE, COMPLETADA
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_revisiones_estado] DEFAULT 'PENDIENTE',
  [conclusion] nvarchar(max) NULL,
  [recomendaciones] nvarchar(max) NULL,
  [propuesta_id] uniqueidentifier NULL,
  [fecha_revision] datetime2 NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_revisiones_estado] CHECK ([estado] IN ('PENDIENTE', 'COMPLETADA'))
)
GO

-- ---------- 4. Billing and payments ----------

CREATE TABLE [facturas] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [paciente_id] uniqueidentifier NOT NULL,
  [cita_id] uniqueidentifier NULL,
  [numero_factura] varchar(50) NULL,
  [subtotal] decimal(18, 2) NOT NULL,
  [impuestos] decimal(18, 2) NOT NULL CONSTRAINT [DF_facturas_impuestos] DEFAULT 0,
  [total] decimal(18, 2) NOT NULL,
  [moneda] char(3) NOT NULL CONSTRAINT [DF_facturas_moneda] DEFAULT 'USD',
  -- ASSUMPTION: PENDIENTE, PAGADA, ANULADA
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_facturas_estado] DEFAULT 'PENDIENTE',
  [emitida_en] datetime2 NULL,
  [creada_en] datetime2 NOT NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_facturas_montos] CHECK ([subtotal] >= 0 AND [impuestos] >= 0 AND [total] >= 0),
  CONSTRAINT [CK_facturas_total] CHECK ([total] = [subtotal] + [impuestos]),
  CONSTRAINT [CK_facturas_estado] CHECK ([estado] IN ('PENDIENTE', 'PAGADA', 'ANULADA'))
)
GO

CREATE TABLE [facturas_detalle] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [factura_id] uniqueidentifier NOT NULL,
  [servicio_id] uniqueidentifier NULL,
  [descripcion] nvarchar(300) NOT NULL,
  [cantidad] int NOT NULL CONSTRAINT [DF_facturas_detalle_cantidad] DEFAULT 1,
  [precio_unitario] decimal(18, 2) NOT NULL,
  [subtotal] decimal(18, 2) NOT NULL,
  CONSTRAINT [CK_facturas_detalle_cantidad] CHECK ([cantidad] > 0),
  CONSTRAINT [CK_facturas_detalle_montos] CHECK ([precio_unitario] >= 0 AND [subtotal] >= 0),
  CONSTRAINT [CK_facturas_detalle_subtotal] CHECK ([subtotal] = [cantidad] * [precio_unitario])
)
GO

CREATE TABLE [pagos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [factura_id] uniqueidentifier NOT NULL,
  -- Provider-specific columns live behind the payment adapter (spec 5.10).
  [proveedor] varchar(25) NOT NULL CONSTRAINT [DF_pagos_proveedor] DEFAULT 'STRIPE',
  [stripe_payment_intent_id] varchar(255) NULL,
  [stripe_checkout_session_id] varchar(255) NULL,
  [clave_idempotencia] varchar(100) NOT NULL UNIQUE,
  [monto] decimal(18, 2) NOT NULL,
  [moneda] char(3) NOT NULL CONSTRAINT [DF_pagos_moneda] DEFAULT 'USD',
  -- ASSUMPTION: PENDIENTE, CONFIRMADO, RECHAZADO, CANCELADO
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_pagos_estado] DEFAULT 'PENDIENTE',
  [creado_en] datetime2 NOT NULL,
  [pagado_en] datetime2 NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_pagos_monto] CHECK ([monto] > 0),
  CONSTRAINT [CK_pagos_estado] CHECK ([estado] IN ('PENDIENTE', 'CONFIRMADO', 'RECHAZADO', 'CANCELADO'))
)
GO

CREATE TABLE [reembolsos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [pago_id] uniqueidentifier NOT NULL,
  [stripe_refund_id] varchar(255) NULL,
  [monto] decimal(18, 2) NOT NULL,
  -- ASSUMPTION: PENDIENTE, PROCESADO, FALLIDO
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_reembolsos_estado] DEFAULT 'PENDIENTE',
  [motivo] nvarchar(500) NULL,
  [solicitado_en] datetime2 NOT NULL,
  [procesado_en] datetime2 NULL,
  CONSTRAINT [CK_reembolsos_monto] CHECK ([monto] > 0),
  CONSTRAINT [CK_reembolsos_estado] CHECK ([estado] IN ('PENDIENTE', 'PROCESADO', 'FALLIDO'))
)
GO

-- Inbox for webhook deduplication and reprocessing.
CREATE TABLE [stripe_eventos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [stripe_event_id] varchar(255) NOT NULL UNIQUE,
  [tipo_evento] varchar(120) NOT NULL,
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_stripe_eventos_estado] DEFAULT 'PENDIENTE',
  [intentos] int NOT NULL CONSTRAINT [DF_stripe_eventos_intentos] DEFAULT 0,
  [recibido_en] datetime2 NOT NULL,
  [procesado_en] datetime2 NULL,
  [error_detalle] nvarchar(1000) NULL,
  CONSTRAINT [CK_stripe_eventos_intentos] CHECK ([intentos] >= 0),
  CONSTRAINT [CK_stripe_eventos_estado] CHECK ([estado] IN ('PENDIENTE', 'PROCESADO', 'FALLIDO', 'IGNORADO'))
)
GO

-- ---------- 5. Outbox and synchronization ----------

-- Insert in the same transaction as the business change.
CREATE TABLE [eventos_outbox] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [agregado_tipo] varchar(80) NOT NULL,
  [agregado_id] uniqueidentifier NOT NULL,
  [tipo_evento] varchar(120) NOT NULL,
  [payload] nvarchar(max) NOT NULL,
  [version_evento] int NOT NULL CONSTRAINT [DF_outbox_version_evento] DEFAULT 1,
  [version_agregado] bigint NOT NULL,
  [correlation_id] uniqueidentifier NULL,
  [creado_en] datetime2 NOT NULL,
  [expira_en] datetime2 NULL,
  CONSTRAINT [UQ_outbox_agregado_version] UNIQUE ([agregado_tipo], [agregado_id], [version_agregado]),
  CONSTRAINT [CK_outbox_version_agregado] CHECK ([version_agregado] >= 1),
  CONSTRAINT [CK_outbox_payload_json] CHECK (ISJSON([payload]) = 1)
)
GO

CREATE INDEX [IX_outbox_agregado_creado] ON [eventos_outbox] ([agregado_tipo], [agregado_id], [creado_en])
GO

CREATE TABLE [destinos_sincronizacion] (
  [id] int IDENTITY(1, 1) NOT NULL PRIMARY KEY,
  [codigo] varchar(40) NOT NULL UNIQUE,
  [activo] bit NOT NULL CONSTRAINT [DF_destinos_activo] DEFAULT 1
)
GO

CREATE TABLE [entregas_eventos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [evento_id] uniqueidentifier NOT NULL,
  [destino_id] int NOT NULL,
  -- ASSUMPTION: PENDIENTE, ENTREGADO, ERROR_TEMPORAL, ERROR_PERMANENTE
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_entregas_estado] DEFAULT 'PENDIENTE',
  [intentos] int NOT NULL CONSTRAINT [DF_entregas_intentos] DEFAULT 0,
  [proximo_intento] datetime2 NULL,
  [bloqueado_hasta] datetime2 NULL,
  [ultimo_error] nvarchar(2000) NULL,
  [entregado_en] datetime2 NULL,
  CONSTRAINT [UQ_entregas_evento_destino] UNIQUE ([evento_id], [destino_id]),
  CONSTRAINT [CK_entregas_intentos] CHECK ([intentos] >= 0),
  CONSTRAINT [CK_entregas_estado] CHECK ([estado] IN ('PENDIENTE', 'ENTREGADO', 'ERROR_TEMPORAL', 'ERROR_PERMANENTE'))
)
GO

CREATE INDEX [IX_entregas_destino_estado] ON [entregas_eventos] ([destino_id], [estado], [proximo_intento])
GO

-- Inbox for events received from external systems.
CREATE TABLE [sincronizacion_inbox] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [sistema_origen] varchar(40) NOT NULL,
  [evento_externo_id] varchar(150) NOT NULL,
  [tipo_evento] varchar(120) NOT NULL,
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_inbox_estado] DEFAULT 'PENDIENTE',
  [recibido_en] datetime2 NOT NULL,
  [procesado_en] datetime2 NULL,
  [error_detalle] nvarchar(1000) NULL,
  CONSTRAINT [UQ_inbox_origen_evento] UNIQUE ([sistema_origen], [evento_externo_id]),
  CONSTRAINT [CK_inbox_estado] CHECK ([estado] IN ('PENDIENTE', 'PROCESADO', 'FALLIDO', 'IGNORADO'))
)
GO

-- ---------- 6. Clinical audit ----------

CREATE TABLE [auditoria_accesos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [usuario_id] uniqueidentifier NULL,
  [paciente_id] uniqueidentifier NULL,
  [entidad] varchar(80) NOT NULL,
  [entidad_id] uniqueidentifier NULL,
  [accion] varchar(40) NOT NULL,
  [fecha_evento] datetime2 NOT NULL,
  [ip_origen] varchar(45) NULL,
  [resultado] varchar(30) NOT NULL,
  [correlation_id] uniqueidentifier NULL,
  [motivo_acceso] nvarchar(250) NULL
)
GO

CREATE INDEX [IX_auditoria_paciente_fecha] ON [auditoria_accesos] ([paciente_id], [fecha_evento])
GO

CREATE INDEX [IX_auditoria_usuario_fecha] ON [auditoria_accesos] ([usuario_id], [fecha_evento])
GO

-- ---------- 7. Notifications (provider agnostic) ----------

CREATE TABLE [notificaciones] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [usuario_id] uniqueidentifier NULL,
  [evento_origen_id] uniqueidentifier NULL,
  [tipo] varchar(80) NOT NULL,
  [destinatario] nvarchar(255) NOT NULL,
  -- ASSUMPTION: PENDIENTE, ENVIADA, FALLIDA, CANCELADA. ENVIADA = accepted by provider.
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_notificaciones_estado] DEFAULT 'PENDIENTE',
  [clave_idempotencia] varchar(150) NOT NULL UNIQUE,
  [numero_intentos] int NOT NULL CONSTRAINT [DF_notificaciones_intentos] DEFAULT 0,
  [siguiente_intento_en] datetime2 NULL,
  [ultimo_error_codigo] varchar(100) NULL,
  [id_mensaje_externo] varchar(255) NULL,
  [programada_en] datetime2 NULL,
  [enviada_en] datetime2 NULL,
  [creada_en] datetime2 NOT NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_notificaciones_estado] CHECK ([estado] IN ('PENDIENTE', 'ENVIADA', 'FALLIDA', 'CANCELADA')),
  CONSTRAINT [CK_notificaciones_intentos] CHECK ([numero_intentos] >= 0)
)
GO

CREATE INDEX [IX_notificaciones_estado_siguiente] ON [notificaciones] ([estado], [siguiente_intento_en])
GO

CREATE INDEX [IX_notificaciones_usuario_creada] ON [notificaciones] ([usuario_id], [creada_en])
GO

CREATE TABLE [notificacion_intentos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [notificacion_id] uniqueidentifier NOT NULL,
  [numero_intento] int NOT NULL,
  [estado] varchar(25) NOT NULL,
  [codigo_respuesta] varchar(30) NULL,
  [codigo_error] varchar(100) NULL,
  [descripcion_error] nvarchar(500) NULL,
  [id_mensaje_externo] varchar(255) NULL,
  [iniciado_en] datetime2 NOT NULL,
  [finalizado_en] datetime2 NULL,
  CONSTRAINT [UQ_notif_intentos_numero] UNIQUE ([notificacion_id], [numero_intento]),
  CONSTRAINT [CK_notif_intentos_estado] CHECK ([estado] IN ('INICIADO', 'ACEPTADO', 'ERROR_TEMPORAL', 'ERROR_PERMANENTE', 'INCIERTO')),
  CONSTRAINT [CK_notif_intentos_numero] CHECK ([numero_intento] >= 1)
)
GO

CREATE INDEX [IX_notif_intentos_iniciado] ON [notificacion_intentos] ([notificacion_id], [iniciado_en])
GO

CREATE TABLE [notificacion_eventos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [notificacion_id] uniqueidentifier NOT NULL,
  [intento_id] uniqueidentifier NULL,
  [clave_deduplicacion] varchar(150) NOT NULL UNIQUE,
  [id_evento_externo] varchar(255) NULL,
  [tipo_evento] varchar(40) NOT NULL,
  [codigo_error] varchar(100) NULL,
  [descripcion_error] nvarchar(500) NULL,
  [huella_payload] char(64) NULL,
  [ocurrido_en] datetime2 NOT NULL,
  [recibido_en] datetime2 NOT NULL,
  CONSTRAINT [CK_notif_eventos_tipo] CHECK ([tipo_evento] IN ('ACEPTADO', 'ENTREGADO', 'REBOTADO', 'RECHAZADO', 'DIFERIDO'))
)
GO

CREATE INDEX [IX_notif_eventos_notif_ocurrido] ON [notificacion_eventos] ([notificacion_id], [ocurrido_en])
GO

CREATE INDEX [IX_notif_eventos_tipo_recibido] ON [notificacion_eventos] ([tipo_evento], [recibido_en])
GO

-- ---------- 8. Authorization and security audit ----------

CREATE TABLE [permisos] (
  [id] int IDENTITY(1, 1) NOT NULL PRIMARY KEY,
  [codigo] varchar(120) NOT NULL UNIQUE,
  [recurso] varchar(80) NOT NULL,
  [accion] varchar(40) NOT NULL,
  [descripcion] nvarchar(300) NULL
)
GO

CREATE TABLE [roles_permisos] (
  [rol_id] int NOT NULL,
  [permiso_id] int NOT NULL,
  CONSTRAINT [PK_roles_permisos] PRIMARY KEY ([rol_id], [permiso_id])
)
GO

CREATE TABLE [accesos_paciente] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [paciente_id] uniqueidentifier NOT NULL,
  [medico_id] uniqueidentifier NOT NULL,
  [alcance] varchar(80) NOT NULL,
  [fundamento] varchar(40) NOT NULL,
  [otorgado_en] datetime2 NOT NULL,
  [expira_en] datetime2 NULL,
  [revocado_en] datetime2 NULL,
  [autorizado_por] uniqueidentifier NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_accesos_expira] CHECK ([expira_en] IS NULL OR [expira_en] > [otorgado_en])
)
GO

CREATE INDEX [IX_accesos_medico_paciente_revocado] ON [accesos_paciente] ([medico_id], [paciente_id], [revocado_en])
GO

CREATE TABLE [eventos_seguridad] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [usuario_id] uniqueidentifier NULL,
  [tipo_evento] varchar(100) NOT NULL,
  -- ASSUMPTION: severity domain not defined in the DBML
  [severidad] varchar(20) NOT NULL,
  [resultado] varchar(30) NOT NULL,
  [direccion_ip] varchar(45) NULL,
  [correlation_id] uniqueidentifier NULL,
  [detalle_sanitizado] nvarchar(1000) NULL,
  [ocurrido_en] datetime2 NOT NULL,
  CONSTRAINT [CK_eventos_seguridad_severidad] CHECK ([severidad] IN ('INFO', 'AVISO', 'ALTA', 'CRITICA'))
)
GO

CREATE INDEX [IX_seguridad_tipo_ocurrido] ON [eventos_seguridad] ([tipo_evento], [ocurrido_en])
GO

CREATE INDEX [IX_seguridad_usuario_ocurrido] ON [eventos_seguridad] ([usuario_id], [ocurrido_en])
GO

-- ---------- 9. Medical knowledge (Azure SQL only) ----------

CREATE TABLE [conocimiento_fuentes] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [codigo] varchar(60) NOT NULL UNIQUE,
  [nombre] nvarchar(200) NOT NULL,
  [organizacion] nvarchar(200) NULL,
  [url] nvarchar(1000) NULL,
  [identificador_publicacion] varchar(120) NULL,
  [version_publicacion] varchar(80) NULL,
  [fecha_publicacion] date NULL,
  [fecha_consulta] datetime2 NULL,
  [licencia] nvarchar(300) NULL,
  [vigente] bit NOT NULL CONSTRAINT [DF_fuentes_vigente] DEFAULT 1
)
GO

CREATE TABLE [conocimiento_analitos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [codigo_loinc] varchar(30) NULL,
  [nombre] nvarchar(150) NOT NULL,
  [descripcion] nvarchar(1500) NULL,
  [unidad_canonica] varchar(40) NULL,
  [tipo_valor] varchar(20) NOT NULL,
  [activo] bit NOT NULL CONSTRAINT [DF_analitos_activo] DEFAULT 1,
  [creado_en] datetime2 NOT NULL,
  [actualizado_en] datetime2 NOT NULL,
  CONSTRAINT [CK_analitos_tipo_valor] CHECK ([tipo_valor] IN ('NUMERICO', 'TEXTO', 'CODIFICADO'))
)
GO

CREATE TABLE [conocimiento_pruebas_analitos] (
  [tipo_prueba_id] uniqueidentifier NOT NULL,
  [analito_id] uniqueidentifier NOT NULL,
  [obligatorio] bit NOT NULL CONSTRAINT [DF_pruebas_analitos_obligatorio] DEFAULT 1,
  [orden_presentacion] int NULL,
  CONSTRAINT [PK_conocimiento_pruebas_analitos] PRIMARY KEY ([tipo_prueba_id], [analito_id])
)
GO

-- Published versions are immutable. (id, version) is the target of composite FKs.
CREATE TABLE [conocimiento_referencias] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [analito_id] uniqueidentifier NOT NULL,
  [fuente_id] uniqueidentifier NOT NULL,
  [version] int NOT NULL,
  [unidad] varchar(40) NOT NULL,
  [sexo_aplicable] varchar(30) NULL,
  [edad_min_dias] int NULL,
  [edad_max_dias] int NULL,
  [tipo_muestra] varchar(80) NULL,
  [metodo_analitico] nvarchar(150) NULL,
  [contexto] varchar(100) NULL,
  [referencia_min] decimal(18, 6) NULL,
  [referencia_max] decimal(18, 6) NULL,
  [valor_texto_esperado] nvarchar(150) NULL,
  [fecha_inicio] date NOT NULL,
  [fecha_fin] date NULL,
  [estado] varchar(20) NOT NULL CONSTRAINT [DF_referencias_estado] DEFAULT 'BORRADOR',
  [validado_por] uniqueidentifier NULL,
  [validado_en] datetime2 NULL,
  CONSTRAINT [UQ_referencias_id_version] UNIQUE ([id], [version]),
  CONSTRAINT [CK_referencias_estado] CHECK ([estado] IN ('BORRADOR', 'VALIDADA', 'RETIRADA')),
  CONSTRAINT [CK_referencias_version] CHECK ([version] >= 1),
  CONSTRAINT [CK_referencias_fechas] CHECK ([fecha_fin] IS NULL OR [fecha_fin] >= [fecha_inicio]),
  CONSTRAINT [CK_referencias_rango] CHECK ([referencia_min] IS NULL OR [referencia_max] IS NULL OR [referencia_min] <= [referencia_max]),
  CONSTRAINT [CK_referencias_edad] CHECK ([edad_min_dias] IS NULL OR [edad_max_dias] IS NULL OR [edad_min_dias] <= [edad_max_dias]),
  CONSTRAINT [CK_referencias_validada_completa] CHECK ([estado] <> 'VALIDADA' OR ([validado_por] IS NOT NULL AND [validado_en] IS NOT NULL))
)
GO

CREATE INDEX [IX_referencias_analito_estado_inicio] ON [conocimiento_referencias] ([analito_id], [estado], [fecha_inicio])
GO

CREATE TABLE [conocimiento_contenidos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [analito_id] uniqueidentifier NOT NULL,
  [fuente_id] uniqueidentifier NOT NULL,
  [idioma] varchar(10) NOT NULL CONSTRAINT [DF_contenidos_idioma] DEFAULT 'es',
  [tipo] varchar(40) NOT NULL,
  [texto] nvarchar(max) NULL,
  [url_referencia] nvarchar(1000) NULL,
  [version] int NOT NULL CONSTRAINT [DF_contenidos_version] DEFAULT 1,
  [estado] varchar(20) NOT NULL CONSTRAINT [DF_contenidos_estado] DEFAULT 'BORRADOR',
  [revisado_por] uniqueidentifier NULL,
  [revisado_en] datetime2 NULL,
  CONSTRAINT [CK_contenidos_tipo] CHECK ([tipo] IN ('DESCRIPCION', 'FACTORES', 'LIMITACIONES', 'EDUCACION')),
  CONSTRAINT [CK_contenidos_estado] CHECK ([estado] IN ('BORRADOR', 'VALIDADA', 'RETIRADA'))
)
GO

-- Target of composite FKs from propuestas_hallazgos (regla_id, regla_version).
CREATE TABLE [conocimiento_reglas] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [codigo] varchar(80) NOT NULL,
  [version] int NOT NULL,
  [nombre] nvarchar(200) NOT NULL,
  [descripcion] nvarchar(1500) NULL,
  -- ASSUMPTION: domain from the DBML note (HALLAZGO, HIPOTESIS, ALERTA)
  [tipo] varchar(30) NOT NULL,
  [codigo_diagnostico] varchar(40) NULL,
  [expresion_logica] varchar(20) NOT NULL CONSTRAINT [DF_reglas_expresion] DEFAULT 'TODOS',
  [mensaje_propuesto] nvarchar(2000) NOT NULL,
  [prioridad] varchar(25) NOT NULL CONSTRAINT [DF_reglas_prioridad] DEFAULT 'INFORMATIVA',
  [estado] varchar(20) NOT NULL CONSTRAINT [DF_reglas_estado] DEFAULT 'BORRADOR',
  [vigente_desde] datetime2 NULL,
  [vigente_hasta] datetime2 NULL,
  [aprobado_por] uniqueidentifier NULL,
  [aprobado_en] datetime2 NULL,
  [creado_en] datetime2 NOT NULL,
  CONSTRAINT [UQ_reglas_codigo_version] UNIQUE ([codigo], [version]),
  CONSTRAINT [UQ_reglas_id_version] UNIQUE ([id], [version]),
  CONSTRAINT [CK_reglas_version] CHECK ([version] >= 1),
  CONSTRAINT [CK_reglas_tipo] CHECK ([tipo] IN ('HALLAZGO', 'HIPOTESIS', 'ALERTA')),
  CONSTRAINT [CK_reglas_expresion] CHECK ([expresion_logica] IN ('TODOS', 'CUALQUIERA')),
  CONSTRAINT [CK_reglas_estado] CHECK ([estado] IN ('BORRADOR', 'VALIDADA', 'RETIRADA')),
  CONSTRAINT [CK_reglas_vigencia] CHECK ([vigente_hasta] IS NULL OR [vigente_desde] IS NULL OR [vigente_hasta] > [vigente_desde])
)
GO

CREATE INDEX [IX_reglas_estado_vigencia] ON [conocimiento_reglas] ([estado], [vigente_desde])
GO

CREATE TABLE [conocimiento_reglas_criterios] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [regla_id] uniqueidentifier NOT NULL,
  [analito_id] uniqueidentifier NOT NULL,
  [operador] varchar(20) NOT NULL,
  [umbral_numerico] decimal(18, 6) NULL,
  [valor_codificado] nvarchar(100) NULL,
  [unidad] varchar(40) NULL,
  [contexto] varchar(100) NULL,
  [orden_criterio] int NOT NULL,
  CONSTRAINT [UQ_criterios_regla_orden] UNIQUE ([regla_id], [orden_criterio]),
  CONSTRAINT [CK_criterios_operador] CHECK ([operador] IN ('LT', 'LE', 'EQ', 'GE', 'GT', 'FUERA_REFERENCIA', 'DENTRO_REFERENCIA')),
  CONSTRAINT [CK_criterios_orden] CHECK ([orden_criterio] >= 1)
)
GO

CREATE TABLE [conocimiento_reglas_fuentes] (
  [regla_id] uniqueidentifier NOT NULL,
  [fuente_id] uniqueidentifier NOT NULL,
  CONSTRAINT [PK_conocimiento_reglas_fuentes] PRIMARY KEY ([regla_id], [fuente_id])
)
GO

-- ---------- 10. Clinical proposals and human review ----------

CREATE TABLE [propuestas_clinicas] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [orden_id] uniqueidentifier NOT NULL,
  [version_motor] varchar(60) NOT NULL,
  [estado] varchar(25) NOT NULL CONSTRAINT [DF_propuestas_estado] DEFAULT 'PENDIENTE',
  [resumen_propuesto] nvarchar(max) NULL,
  [creado_en] datetime2 NOT NULL,
  [revisado_por] uniqueidentifier NULL,
  [revisado_en] datetime2 NULL,
  [conclusion_medica] nvarchar(max) NULL,
  [version_fila] rowversion NOT NULL,
  CONSTRAINT [CK_propuestas_estado] CHECK ([estado] IN ('PENDIENTE', 'ACEPTADA', 'CORREGIDA', 'RECHAZADA', 'SIN_EVIDENCIA'))
)
GO

CREATE INDEX [IX_propuestas_orden_creado] ON [propuestas_clinicas] ([orden_id], [creado_en])
GO

CREATE TABLE [propuestas_hallazgos] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [propuesta_id] uniqueidentifier NOT NULL,
  [regla_id] uniqueidentifier NOT NULL,
  [regla_version] int NOT NULL,
  [tipo] varchar(30) NOT NULL,
  [explicacion] nvarchar(max) NULL,
  -- ASSUMPTION: review states for a finding; decision history lives in propuestas_revisiones
  [estado_revision] varchar(25) NOT NULL CONSTRAINT [DF_hallazgos_estado] DEFAULT 'PENDIENTE',
  [observacion_medica] nvarchar(max) NULL,
  CONSTRAINT [UQ_hallazgos_propuesta_regla] UNIQUE ([propuesta_id], [regla_id]),
  CONSTRAINT [CK_hallazgos_tipo] CHECK ([tipo] IN ('HALLAZGO', 'HIPOTESIS', 'ALERTA')),
  CONSTRAINT [CK_hallazgos_estado] CHECK ([estado_revision] IN ('PENDIENTE', 'ACEPTADA', 'CORREGIDA', 'RECHAZADA'))
)
GO

CREATE TABLE [propuestas_hallazgos_evidencias] (
  [hallazgo_id] uniqueidentifier NOT NULL,
  [resultado_id] uniqueidentifier NOT NULL,
  [valor_observado] decimal(18, 6) NULL,
  [unidad_observada] varchar(40) NULL,
  CONSTRAINT [PK_hallazgos_evidencias] PRIMARY KEY ([hallazgo_id], [resultado_id])
)
GO

-- Immutable history of human decisions over a proposal.
CREATE TABLE [propuestas_revisiones] (
  [id] uniqueidentifier NOT NULL PRIMARY KEY,
  [propuesta_id] uniqueidentifier NOT NULL,
  [medico_id] uniqueidentifier NOT NULL,
  [decision] varchar(25) NOT NULL,
  [motivo] nvarchar(2000) NULL,
  [conclusion_corregida] nvarchar(max) NULL,
  [revisada_en] datetime2 NOT NULL,
  CONSTRAINT [CK_revisiones_decision] CHECK ([decision] IN ('ACEPTAR', 'CORREGIR', 'RECHAZAR'))
)
GO

CREATE INDEX [IX_revisiones_propuesta_fecha] ON [propuestas_revisiones] ([propuesta_id], [revisada_en])
GO

-- =====================================================================
-- Filtered unique indexes for nullable identifiers.
-- A plain UNIQUE constraint in SQL Server admits only one NULL, so these
-- columns use filtered indexes that enforce uniqueness only when present.
-- =====================================================================

CREATE UNIQUE INDEX [UX_usuarios_auth_provider] ON [usuarios] ([auth_provider_id])
  WHERE [auth_provider_id] IS NOT NULL
GO

CREATE UNIQUE INDEX [UX_facturas_numero] ON [facturas] ([numero_factura])
  WHERE [numero_factura] IS NOT NULL
GO

CREATE UNIQUE INDEX [UX_pagos_payment_intent] ON [pagos] ([stripe_payment_intent_id])
  WHERE [stripe_payment_intent_id] IS NOT NULL
GO

CREATE UNIQUE INDEX [UX_pagos_checkout_session] ON [pagos] ([stripe_checkout_session_id])
  WHERE [stripe_checkout_session_id] IS NOT NULL
GO

CREATE UNIQUE INDEX [UX_reembolsos_stripe_refund] ON [reembolsos] ([stripe_refund_id])
  WHERE [stripe_refund_id] IS NOT NULL
GO

CREATE UNIQUE INDEX [UX_analitos_loinc] ON [conocimiento_analitos] ([codigo_loinc])
  WHERE [codigo_loinc] IS NOT NULL
GO

-- =====================================================================
-- Foreign keys (added after all tables exist)
-- =====================================================================

ALTER TABLE [usuarios_roles] ADD CONSTRAINT [FK_usuarios_roles_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [usuarios_roles] ADD CONSTRAINT [FK_usuarios_roles_rol] FOREIGN KEY ([rol_id]) REFERENCES [roles] ([id])
GO
ALTER TABLE [pacientes] ADD CONSTRAINT [FK_pacientes_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [medicos] ADD CONSTRAINT [FK_medicos_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [medicos_especialidades] ADD CONSTRAINT [FK_medesp_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id])
GO
ALTER TABLE [medicos_especialidades] ADD CONSTRAINT [FK_medesp_especialidad] FOREIGN KEY ([especialidad_id]) REFERENCES [especialidades] ([id])
GO
ALTER TABLE [consultorios] ADD CONSTRAINT [FK_consultorios_sede] FOREIGN KEY ([sede_id]) REFERENCES [sedes] ([id])
GO
ALTER TABLE [servicios_medicos] ADD CONSTRAINT [FK_servicios_especialidad] FOREIGN KEY ([especialidad_id]) REFERENCES [especialidades] ([id])
GO
ALTER TABLE [agendas] ADD CONSTRAINT [FK_agendas_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id])
GO
ALTER TABLE [agendas] ADD CONSTRAINT [FK_agendas_consultorio] FOREIGN KEY ([consultorio_id]) REFERENCES [consultorios] ([id])
GO
ALTER TABLE [horarios_disponibles] ADD CONSTRAINT [FK_horarios_agenda] FOREIGN KEY ([agenda_id]) REFERENCES [agendas] ([id])
GO
ALTER TABLE [citas] ADD CONSTRAINT [FK_citas_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id])
GO
ALTER TABLE [citas] ADD CONSTRAINT [FK_citas_horario] FOREIGN KEY ([horario_id]) REFERENCES [horarios_disponibles] ([id])
GO
ALTER TABLE [citas] ADD CONSTRAINT [FK_citas_servicio] FOREIGN KEY ([servicio_id]) REFERENCES [servicios_medicos] ([id])
GO
ALTER TABLE [historial_estados_cita] ADD CONSTRAINT [FK_historial_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id])
GO
ALTER TABLE [historial_estados_cita] ADD CONSTRAINT [FK_historial_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [consultas_medicas] ADD CONSTRAINT [FK_consultas_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id])
GO
ALTER TABLE [ordenes_laboratorio] ADD CONSTRAINT [FK_ordenes_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id])
GO
ALTER TABLE [ordenes_laboratorio] ADD CONSTRAINT [FK_ordenes_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id])
GO
ALTER TABLE [ordenes_laboratorio] ADD CONSTRAINT [FK_ordenes_consulta] FOREIGN KEY ([consulta_id]) REFERENCES [consultas_medicas] ([id])
GO
ALTER TABLE [ordenes_laboratorio_detalle] ADD CONSTRAINT [FK_orden_detalle_orden] FOREIGN KEY ([orden_id]) REFERENCES [ordenes_laboratorio] ([id])
GO
ALTER TABLE [ordenes_laboratorio_detalle] ADD CONSTRAINT [FK_orden_detalle_prueba] FOREIGN KEY ([tipo_prueba_id]) REFERENCES [tipos_prueba_laboratorio] ([id])
GO
ALTER TABLE [resultados_laboratorio] ADD CONSTRAINT [FK_resultados_detalle] FOREIGN KEY ([detalle_orden_id]) REFERENCES [ordenes_laboratorio_detalle] ([id])
GO
ALTER TABLE [resultados_laboratorio] ADD CONSTRAINT [FK_resultados_analito] FOREIGN KEY ([analito_id]) REFERENCES [conocimiento_analitos] ([id])
GO
-- Composite FK: the applied reference and its version must match one published row.
ALTER TABLE [resultados_laboratorio] ADD CONSTRAINT [FK_resultados_referencia_version] FOREIGN KEY ([referencia_aplicada_id], [referencia_version]) REFERENCES [conocimiento_referencias] ([id], [version])
GO
ALTER TABLE [resultados_laboratorio] ADD CONSTRAINT [FK_resultados_validado_por] FOREIGN KEY ([validado_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [documentos_clinicos] ADD CONSTRAINT [FK_documentos_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id])
GO
ALTER TABLE [documentos_clinicos] ADD CONSTRAINT [FK_documentos_orden] FOREIGN KEY ([orden_id]) REFERENCES [ordenes_laboratorio] ([id])
GO
ALTER TABLE [documentos_clinicos] ADD CONSTRAINT [FK_documentos_subido_por] FOREIGN KEY ([subido_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [revisiones_laboratorio] ADD CONSTRAINT [FK_revisiones_orden] FOREIGN KEY ([orden_id]) REFERENCES [ordenes_laboratorio] ([id])
GO
ALTER TABLE [revisiones_laboratorio] ADD CONSTRAINT [FK_revisiones_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id])
GO
ALTER TABLE [revisiones_laboratorio] ADD CONSTRAINT [FK_revisiones_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id])
GO
ALTER TABLE [revisiones_laboratorio] ADD CONSTRAINT [FK_revisiones_propuesta] FOREIGN KEY ([propuesta_id]) REFERENCES [propuestas_clinicas] ([id])
GO
ALTER TABLE [facturas] ADD CONSTRAINT [FK_facturas_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id])
GO
ALTER TABLE [facturas] ADD CONSTRAINT [FK_facturas_cita] FOREIGN KEY ([cita_id]) REFERENCES [citas] ([id])
GO
ALTER TABLE [facturas_detalle] ADD CONSTRAINT [FK_facturas_detalle_factura] FOREIGN KEY ([factura_id]) REFERENCES [facturas] ([id])
GO
ALTER TABLE [facturas_detalle] ADD CONSTRAINT [FK_facturas_detalle_servicio] FOREIGN KEY ([servicio_id]) REFERENCES [servicios_medicos] ([id])
GO
ALTER TABLE [pagos] ADD CONSTRAINT [FK_pagos_factura] FOREIGN KEY ([factura_id]) REFERENCES [facturas] ([id])
GO
ALTER TABLE [reembolsos] ADD CONSTRAINT [FK_reembolsos_pago] FOREIGN KEY ([pago_id]) REFERENCES [pagos] ([id])
GO
ALTER TABLE [entregas_eventos] ADD CONSTRAINT [FK_entregas_evento] FOREIGN KEY ([evento_id]) REFERENCES [eventos_outbox] ([id])
GO
ALTER TABLE [entregas_eventos] ADD CONSTRAINT [FK_entregas_destino] FOREIGN KEY ([destino_id]) REFERENCES [destinos_sincronizacion] ([id])
GO
ALTER TABLE [auditoria_accesos] ADD CONSTRAINT [FK_auditoria_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [auditoria_accesos] ADD CONSTRAINT [FK_auditoria_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id])
GO
ALTER TABLE [notificaciones] ADD CONSTRAINT [FK_notificaciones_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [notificaciones] ADD CONSTRAINT [FK_notificaciones_evento] FOREIGN KEY ([evento_origen_id]) REFERENCES [eventos_outbox] ([id])
GO
ALTER TABLE [notificacion_intentos] ADD CONSTRAINT [FK_notif_intentos_notif] FOREIGN KEY ([notificacion_id]) REFERENCES [notificaciones] ([id])
GO
ALTER TABLE [notificacion_eventos] ADD CONSTRAINT [FK_notif_eventos_notif] FOREIGN KEY ([notificacion_id]) REFERENCES [notificaciones] ([id])
GO
ALTER TABLE [notificacion_eventos] ADD CONSTRAINT [FK_notif_eventos_intento] FOREIGN KEY ([intento_id]) REFERENCES [notificacion_intentos] ([id])
GO
ALTER TABLE [roles_permisos] ADD CONSTRAINT [FK_roles_permisos_rol] FOREIGN KEY ([rol_id]) REFERENCES [roles] ([id])
GO
ALTER TABLE [roles_permisos] ADD CONSTRAINT [FK_roles_permisos_permiso] FOREIGN KEY ([permiso_id]) REFERENCES [permisos] ([id])
GO
ALTER TABLE [accesos_paciente] ADD CONSTRAINT [FK_accesos_paciente] FOREIGN KEY ([paciente_id]) REFERENCES [pacientes] ([id])
GO
ALTER TABLE [accesos_paciente] ADD CONSTRAINT [FK_accesos_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id])
GO
ALTER TABLE [accesos_paciente] ADD CONSTRAINT [FK_accesos_autorizado_por] FOREIGN KEY ([autorizado_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [eventos_seguridad] ADD CONSTRAINT [FK_seguridad_usuario] FOREIGN KEY ([usuario_id]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [conocimiento_pruebas_analitos] ADD CONSTRAINT [FK_pruebas_analitos_prueba] FOREIGN KEY ([tipo_prueba_id]) REFERENCES [tipos_prueba_laboratorio] ([id])
GO
ALTER TABLE [conocimiento_pruebas_analitos] ADD CONSTRAINT [FK_pruebas_analitos_analito] FOREIGN KEY ([analito_id]) REFERENCES [conocimiento_analitos] ([id])
GO
ALTER TABLE [conocimiento_referencias] ADD CONSTRAINT [FK_referencias_analito] FOREIGN KEY ([analito_id]) REFERENCES [conocimiento_analitos] ([id])
GO
ALTER TABLE [conocimiento_referencias] ADD CONSTRAINT [FK_referencias_fuente] FOREIGN KEY ([fuente_id]) REFERENCES [conocimiento_fuentes] ([id])
GO
ALTER TABLE [conocimiento_referencias] ADD CONSTRAINT [FK_referencias_validado_por] FOREIGN KEY ([validado_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [conocimiento_contenidos] ADD CONSTRAINT [FK_contenidos_analito] FOREIGN KEY ([analito_id]) REFERENCES [conocimiento_analitos] ([id])
GO
ALTER TABLE [conocimiento_contenidos] ADD CONSTRAINT [FK_contenidos_fuente] FOREIGN KEY ([fuente_id]) REFERENCES [conocimiento_fuentes] ([id])
GO
ALTER TABLE [conocimiento_contenidos] ADD CONSTRAINT [FK_contenidos_revisado_por] FOREIGN KEY ([revisado_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [conocimiento_reglas] ADD CONSTRAINT [FK_reglas_aprobado_por] FOREIGN KEY ([aprobado_por]) REFERENCES [usuarios] ([id])
GO
ALTER TABLE [conocimiento_reglas_criterios] ADD CONSTRAINT [FK_criterios_regla] FOREIGN KEY ([regla_id]) REFERENCES [conocimiento_reglas] ([id])
GO
ALTER TABLE [conocimiento_reglas_criterios] ADD CONSTRAINT [FK_criterios_analito] FOREIGN KEY ([analito_id]) REFERENCES [conocimiento_analitos] ([id])
GO
ALTER TABLE [conocimiento_reglas_fuentes] ADD CONSTRAINT [FK_reglas_fuentes_regla] FOREIGN KEY ([regla_id]) REFERENCES [conocimiento_reglas] ([id])
GO
ALTER TABLE [conocimiento_reglas_fuentes] ADD CONSTRAINT [FK_reglas_fuentes_fuente] FOREIGN KEY ([fuente_id]) REFERENCES [conocimiento_fuentes] ([id])
GO
ALTER TABLE [propuestas_clinicas] ADD CONSTRAINT [FK_propuestas_orden] FOREIGN KEY ([orden_id]) REFERENCES [ordenes_laboratorio] ([id])
GO
ALTER TABLE [propuestas_clinicas] ADD CONSTRAINT [FK_propuestas_revisado_por] FOREIGN KEY ([revisado_por]) REFERENCES [medicos] ([id])
GO
ALTER TABLE [propuestas_hallazgos] ADD CONSTRAINT [FK_hallazgos_propuesta] FOREIGN KEY ([propuesta_id]) REFERENCES [propuestas_clinicas] ([id])
GO
-- Composite FK: a finding always points to the exact published rule version it used.
ALTER TABLE [propuestas_hallazgos] ADD CONSTRAINT [FK_hallazgos_regla_version] FOREIGN KEY ([regla_id], [regla_version]) REFERENCES [conocimiento_reglas] ([id], [version])
GO
ALTER TABLE [propuestas_hallazgos_evidencias] ADD CONSTRAINT [FK_evidencias_hallazgo] FOREIGN KEY ([hallazgo_id]) REFERENCES [propuestas_hallazgos] ([id])
GO
ALTER TABLE [propuestas_hallazgos_evidencias] ADD CONSTRAINT [FK_evidencias_resultado] FOREIGN KEY ([resultado_id]) REFERENCES [resultados_laboratorio] ([id])
GO
ALTER TABLE [propuestas_revisiones] ADD CONSTRAINT [FK_revisiones_propuesta_clinica] FOREIGN KEY ([propuesta_id]) REFERENCES [propuestas_clinicas] ([id])
GO
ALTER TABLE [propuestas_revisiones] ADD CONSTRAINT [FK_propuestas_revisiones_medico] FOREIGN KEY ([medico_id]) REFERENCES [medicos] ([id])
GO
