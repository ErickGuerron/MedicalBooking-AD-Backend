"""SQL Server adapter for clinical knowledge and proposals (E5).

Every validation, proposal and review locks the row that decides its outcome (analyte,
order or proposal) first, so two concurrent writers cannot both validate an overlapping
reference, both propose for one order, or both review one proposal. Parameterized SQL only.
Validated rows are never updated in place: a change is a new draft.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import Connection, Engine, text

from medicalbooking.modules.clinical.application.ports import (
    AnalyteView,
    FindingView,
    NewReference,
    NewRule,
    OrderForEvaluation,
    ProposalView,
)
from medicalbooking.modules.clinical.domain import (
    CriterionSpec,
    Evaluation,
    Reference,
    ResultInput,
    RuleSpec,
    overlaps,
)
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.shared.infrastructure.sql_errors import retry_on_deadlock

_DECISION_STATE = {"ACEPTAR": "ACEPTADA", "CORREGIR": "CORREGIDA", "RECHAZAR": "RECHAZADA"}

# Column list of a reference, as a literal so the SQL below stays constant.
_REF_SELECT = (
    "SELECT id, version, unidad, referencia_min, referencia_max, sexo_aplicable, "
    "edad_min_dias, edad_max_dias, metodo_analitico, tipo_muestra, contexto, "
    "fecha_inicio, fecha_fin FROM conocimiento_referencias "
)
_REF_SELECT_WITH_STATE = (
    "SELECT id, version, unidad, referencia_min, referencia_max, sexo_aplicable, "
    "edad_min_dias, edad_max_dias, metodo_analitico, tipo_muestra, contexto, "
    "fecha_inicio, fecha_fin, estado FROM conocimiento_referencias "
)


def _canonical(value: object) -> str:
    return str(uuid.UUID(str(value)))


def _as_guid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except ValueError:
        return None


def _naive(moment: datetime) -> datetime:
    return moment.astimezone(UTC).replace(tzinfo=None)


def _reference(row) -> Reference:
    return Reference(
        id=_canonical(row.id),
        version=row.version,
        unidad=row.unidad,
        minimo=row.referencia_min,
        maximo=row.referencia_max,
        sexo=row.sexo_aplicable,
        edad_min_dias=row.edad_min_dias,
        edad_max_dias=row.edad_max_dias,
        metodo=row.metodo_analitico,
        tipo_muestra=row.tipo_muestra,
        contexto=row.contexto,
        fecha_inicio=row.fecha_inicio,
        fecha_fin=row.fecha_fin,
    )


class SqlClinicalStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ---------- knowledge ----------

    def list_analytes(self) -> list[AnalyteView]:
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, nombre, unidad_canonica, tipo_valor "
                    "FROM conocimiento_analitos WHERE activo = 1 ORDER BY nombre"
                )
            ).all()
        return [
            AnalyteView(
                id=_canonical(row.id),
                nombre=row.nombre,
                unidad=row.unidad_canonica,
                tipo_valor=row.tipo_valor,
            )
            for row in rows
        ]

    def analyte_exists(self, analito_id: str) -> bool:
        guid = _as_guid(analito_id)
        if guid is None:
            return False
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM conocimiento_analitos WHERE id = :a AND activo = 1"),
                {"a": guid},
            ).first()
        return found is not None

    def source_exists(self, fuente_id: str) -> bool:
        guid = _as_guid(fuente_id)
        if guid is None:
            return False
        with self._engine.connect() as conn:
            found = conn.execute(
                text("SELECT 1 FROM conocimiento_fuentes WHERE id = :f AND vigente = 1"),
                {"f": guid},
            ).first()
        return found is not None

    def create_analyte(
        self, analito_id: str, nombre: str, unidad: str | None, tipo_valor: str, now: datetime
    ) -> None:
        moment = _naive(now)
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO conocimiento_analitos (id, nombre, unidad_canonica, tipo_valor, "
                    "activo, creado_en, actualizado_en) "
                    "VALUES (:id, :nombre, :unidad, :tipo, 1, :ahora, :ahora)"
                ),
                {
                    "id": analito_id,
                    "nombre": nombre,
                    "unidad": unidad,
                    "tipo": tipo_valor,
                    "ahora": moment,
                },
            )

    def list_validated_references(self, analito_id: str, on: date) -> list[Reference]:
        guid = _as_guid(analito_id)
        if guid is None:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    _REF_SELECT + "WHERE analito_id = :a AND estado = 'VALIDADA' "
                    "AND fecha_inicio <= :hoy AND (fecha_fin IS NULL OR fecha_fin >= :hoy) "
                    "ORDER BY fecha_inicio"
                ),
                {"a": guid, "hoy": on},
            ).all()
        return [_reference(row) for row in rows]

    def create_reference(self, command: NewReference, now: datetime) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO conocimiento_referencias (id, analito_id, fuente_id, version, "
                    "unidad, sexo_aplicable, edad_min_dias, edad_max_dias, tipo_muestra, "
                    "metodo_analitico, contexto, referencia_min, referencia_max, fecha_inicio, "
                    "fecha_fin, estado) "
                    "VALUES (:id, :analito, :fuente, 1, :unidad, :sexo, :edad_min, :edad_max, "
                    ":muestra, :metodo, :contexto, :minimo, :maximo, :inicio, :fin, 'BORRADOR')"
                ),
                {
                    "id": command.id,
                    "analito": command.analito_id,
                    "fuente": command.fuente_id,
                    "unidad": command.unidad,
                    "sexo": command.sexo,
                    "edad_min": command.edad_min_dias,
                    "edad_max": command.edad_max_dias,
                    "muestra": command.tipo_muestra,
                    "metodo": command.metodo,
                    "contexto": command.contexto,
                    "minimo": command.minimo,
                    "maximo": command.maximo,
                    "inicio": command.fecha_inicio,
                    "fin": command.fecha_fin,
                },
            )

    def validate_reference(self, reference_id: str, actor_user_id: str, now: datetime) -> str:
        return retry_on_deadlock(
            lambda: self._validate_reference_once(reference_id, actor_user_id, now)
        )

    def _validate_reference_once(self, reference_id: str, actor_user_id: str, now: datetime) -> str:
        guid = _as_guid(reference_id)
        if guid is None:
            return "NOT_FOUND"
        with self._engine.begin() as conn:
            analyte = conn.execute(
                text("SELECT analito_id FROM conocimiento_referencias WHERE id = :r"),
                {"r": guid},
            ).scalar()
            if analyte is None:
                return "NOT_FOUND"
            # One validation per analyte at a time: each overlap check must see the others' commits.
            conn.execute(
                text("SELECT id FROM conocimiento_analitos WITH (UPDLOCK, ROWLOCK) WHERE id = :a"),
                {"a": analyte},
            )
            draft = conn.execute(
                text(_REF_SELECT_WITH_STATE + "WITH (UPDLOCK, ROWLOCK) WHERE id = :r"),
                {"r": guid},
            ).first()
            if draft is None:
                return "NOT_FOUND"
            if draft.estado != "BORRADOR":
                return "NOT_DRAFT"
            validated = conn.execute(
                text(_REF_SELECT + "WHERE analito_id = :a AND estado = 'VALIDADA'"),
                {"a": analyte},
            ).all()
            candidate = _reference(draft)
            if any(overlaps(candidate, _reference(row)) for row in validated):
                return "OVERLAP"
            conn.execute(
                text(
                    "UPDATE conocimiento_referencias SET estado = 'VALIDADA', validado_por = :u, "
                    "validado_en = :ahora WHERE id = :r"
                ),
                {"u": actor_user_id, "ahora": _naive(now), "r": guid},
            )
        return "CREATED"

    def create_rule(self, command: NewRule, now: datetime) -> int:
        moment = _naive(now)
        with self._engine.begin() as conn:
            # HOLDLOCK on the code range: two drafts of one code get distinct versions.
            version = conn.execute(
                text(
                    "SELECT ISNULL(MAX(version), 0) + 1 FROM conocimiento_reglas "
                    "WITH (UPDLOCK, HOLDLOCK) WHERE codigo = :c"
                ),
                {"c": command.codigo},
            ).scalar_one()
            conn.execute(
                text(
                    "INSERT INTO conocimiento_reglas (id, codigo, version, nombre, descripcion, "
                    "tipo, expresion_logica, mensaje_propuesto, prioridad, estado, creado_en) "
                    "VALUES (:id, :codigo, :version, :nombre, :descripcion, :tipo, :logica, "
                    ":mensaje, :prioridad, 'BORRADOR', :ahora)"
                ),
                {
                    "id": command.id,
                    "codigo": command.codigo,
                    "version": version,
                    "nombre": command.nombre,
                    "descripcion": command.descripcion,
                    "tipo": command.tipo,
                    "logica": command.expresion_logica,
                    "mensaje": command.mensaje,
                    "prioridad": command.prioridad,
                    "ahora": moment,
                },
            )
            for position, criterion in enumerate(command.criteria, start=1):
                conn.execute(
                    text(
                        "INSERT INTO conocimiento_reglas_criterios (id, regla_id, analito_id, "
                        "operador, umbral_numerico, unidad, orden_criterio) "
                        "VALUES (:id, :regla, :analito, :operador, :umbral, :unidad, :orden)"
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "regla": command.id,
                        "analito": criterion.analito_id,
                        "operador": criterion.operador,
                        "umbral": criterion.umbral,
                        "unidad": criterion.unidad,
                        "orden": position,
                    },
                )
        return int(version)

    def validate_rule(self, rule_id: str, actor_user_id: str, now: datetime) -> str:
        guid = _as_guid(rule_id)
        if guid is None:
            return "NOT_FOUND"
        moment = _naive(now)
        with self._engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT estado FROM conocimiento_reglas WITH (UPDLOCK, ROWLOCK) WHERE id = :r"
                ),
                {"r": guid},
            ).scalar()
            if state is None:
                return "NOT_FOUND"
            if state != "BORRADOR":
                return "NOT_DRAFT"
            conn.execute(
                text(
                    "UPDATE conocimiento_reglas SET estado = 'VALIDADA', aprobado_por = :u, "
                    "aprobado_en = :ahora, vigente_desde = :ahora WHERE id = :r"
                ),
                {"u": actor_user_id, "ahora": moment, "r": guid},
            )
        return "CREATED"

    # ---------- proposals ----------

    def load_order_for_evaluation(self, order_id: str) -> OrderForEvaluation | None:
        guid = _as_guid(order_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            header = conn.execute(
                text(
                    "SELECT o.id, o.estado, o.paciente_id, pa.usuario_id AS paciente_user_id, "
                    "me.usuario_id AS medico_user_id FROM ordenes_laboratorio o "
                    "JOIN pacientes pa ON pa.id = o.paciente_id "
                    "JOIN medicos me ON me.id = o.medico_id WHERE o.id = :o"
                ),
                {"o": guid},
            ).first()
            if header is None:
                return None
            rows = conn.execute(
                text(
                    "SELECT r.id, r.analito_id, r.valor_numerico, r.unidad, r.interpretacion, "
                    "r.contexto_muestra "
                    "FROM resultados_laboratorio r "
                    "JOIN ordenes_laboratorio_detalle d ON d.id = r.detalle_orden_id "
                    "WHERE d.orden_id = :o ORDER BY r.id"
                ),
                {"o": guid},
            ).all()
        return OrderForEvaluation(
            order_id=_canonical(header.id),
            estado=header.estado,
            paciente_id=_canonical(header.paciente_id),
            paciente_user_id=_canonical(header.paciente_user_id),
            medico_user_id=_canonical(header.medico_user_id),
            results=tuple(
                ResultInput(
                    resultado_id=_canonical(row.id),
                    analito_id=None if row.analito_id is None else _canonical(row.analito_id),
                    valor=None if row.valor_numerico is None else Decimal(row.valor_numerico),
                    unidad=row.unidad,
                    interpretacion=row.interpretacion,
                    contexto_muestra=row.contexto_muestra,
                )
                for row in rows
            ),
        )

    def loinc_results_for_order(self, order_id: str) -> list[tuple[str, str | None, Decimal]]:
        """Numeric results of one order whose analyte carries a LOINC code (shadow mode, S13)."""
        guid = _as_guid(order_id)
        if guid is None:
            return []
        with self._engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT a.codigo_loinc, r.unidad, r.valor_numerico "
                    "FROM resultados_laboratorio r "
                    "JOIN ordenes_laboratorio_detalle d ON d.id = r.detalle_orden_id "
                    "JOIN conocimiento_analitos a ON a.id = r.analito_id "
                    "WHERE d.orden_id = :o AND a.codigo_loinc IS NOT NULL "
                    "AND r.valor_numerico IS NOT NULL ORDER BY r.id"
                ),
                {"o": guid},
            ).all()
        return [(row.codigo_loinc, row.unidad, Decimal(row.valor_numerico)) for row in rows]

    def patient_sex_birth(self, paciente_id: str) -> tuple[str | None, date | None]:
        guid = _as_guid(paciente_id)
        if guid is None:
            return None, None
        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT sexo_registrado, fecha_nacimiento FROM pacientes WHERE id = :p"),
                {"p": guid},
            ).first()
        return (None, None) if row is None else (row.sexo_registrado, row.fecha_nacimiento)

    def validated_rules(self, now: datetime) -> list[RuleSpec]:
        moment = _naive(now)
        with self._engine.connect() as conn:
            rules = conn.execute(
                text(
                    "SELECT id, codigo, version, tipo, expresion_logica, mensaje_propuesto "
                    "FROM conocimiento_reglas WHERE estado = 'VALIDADA' "
                    "AND (vigente_desde IS NULL OR vigente_desde <= :ahora) "
                    "AND (vigente_hasta IS NULL OR vigente_hasta > :ahora)"
                ),
                {"ahora": moment},
            ).all()
            specs = []
            for rule in rules:
                criteria = conn.execute(
                    text(
                        "SELECT analito_id, operador, umbral_numerico, unidad, contexto "
                        "FROM conocimiento_reglas_criterios WHERE regla_id = :r "
                        "ORDER BY orden_criterio"
                    ),
                    {"r": rule.id},
                ).all()
                specs.append(
                    RuleSpec(
                        rule_id=_canonical(rule.id),
                        codigo=rule.codigo,
                        version=rule.version,
                        tipo=rule.tipo,
                        expresion_logica=rule.expresion_logica,
                        mensaje=rule.mensaje_propuesto,
                        criteria=tuple(
                            CriterionSpec(
                                analito_id=_canonical(c.analito_id),
                                operador=c.operador,
                                umbral=c.umbral_numerico,
                                unidad=c.unidad,
                                contexto=c.contexto,
                            )
                            for c in criteria
                        ),
                    )
                )
        return specs

    def record_proposal(
        self, order_id: str, evaluation: Evaluation, version_motor: str, now: datetime
    ) -> tuple[str, str]:
        return retry_on_deadlock(
            lambda: self._record_proposal_once(order_id, evaluation, version_motor, now)
        )

    def _record_proposal_once(
        self, order_id: str, evaluation: Evaluation, version_motor: str, now: datetime
    ) -> tuple[str, str]:
        guid = _as_guid(order_id)
        moment = _naive(now)
        with self._engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT estado FROM ordenes_laboratorio WITH (UPDLOCK, ROWLOCK) WHERE id = :o"
                ),
                {"o": guid},
            ).scalar()
            if state != "COMPLETADA":
                return "NOT_COMPLETED", ""
            existing = conn.execute(
                text("SELECT id FROM propuestas_clinicas WHERE orden_id = :o"), {"o": guid}
            ).scalar()
            if existing is not None:
                return "ALREADY_EXISTS", _canonical(existing)
            proposal_id = str(uuid.uuid4())
            conn.execute(
                text(
                    "INSERT INTO propuestas_clinicas (id, orden_id, version_motor, estado, "
                    "resumen_propuesto, creado_en) "
                    "VALUES (:id, :o, :motor, :estado, :resumen, :ahora)"
                ),
                {
                    "id": proposal_id,
                    "o": guid,
                    "motor": version_motor,
                    "estado": evaluation.estado,
                    "resumen": evaluation.resumen,
                    "ahora": moment,
                },
            )
            for finding in evaluation.findings:
                hallazgo_id = str(uuid.uuid4())
                conn.execute(
                    text(
                        "INSERT INTO propuestas_hallazgos (id, propuesta_id, regla_id, "
                        "regla_version, tipo, explicacion, estado_revision) "
                        "VALUES (:id, :p, :regla, :version, :tipo, :explicacion, 'PENDIENTE')"
                    ),
                    {
                        "id": hallazgo_id,
                        "p": proposal_id,
                        "regla": finding.rule_id,
                        "version": finding.rule_version,
                        "tipo": finding.tipo,
                        "explicacion": finding.explicacion,
                    },
                )
                for item in finding.evidence:
                    conn.execute(
                        text(
                            "INSERT INTO propuestas_hallazgos_evidencias (hallazgo_id, "
                            "resultado_id, valor_observado, unidad_observada) "
                            "VALUES (:h, :r, :valor, :unidad)"
                        ),
                        {
                            "h": hallazgo_id,
                            "r": item.resultado_id,
                            "valor": item.valor,
                            "unidad": item.unidad,
                        },
                    )
            _outbox(conn, proposal_id, "propuesta.creada", {"estado": evaluation.estado}, moment, 1)
        return "CREATED", proposal_id

    def get_proposal(self, order_id: str) -> ProposalView | None:
        guid = _as_guid(order_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            proposal = conn.execute(
                text(
                    "SELECT p.id, p.orden_id, p.estado, p.version_motor, p.resumen_propuesto, "
                    "p.creado_en, p.conclusion_medica FROM propuestas_clinicas p "
                    "WHERE p.orden_id = :o"
                ),
                {"o": guid},
            ).first()
            if proposal is None:
                return None
            decision = conn.execute(
                text(
                    "SELECT TOP 1 decision FROM propuestas_revisiones WHERE propuesta_id = :p "
                    "ORDER BY revisada_en DESC"
                ),
                {"p": proposal.id},
            ).scalar()
            hallazgos = conn.execute(
                text(
                    "SELECT h.id, r.codigo, h.regla_version, h.tipo, h.explicacion, "
                    "h.estado_revision FROM propuestas_hallazgos h "
                    "JOIN conocimiento_reglas r ON r.id = h.regla_id "
                    "WHERE h.propuesta_id = :p ORDER BY r.codigo, h.regla_version"
                ),
                {"p": proposal.id},
            ).all()
            findings = []
            for hallazgo in hallazgos:
                evidence = conn.execute(
                    text(
                        "SELECT resultado_id, valor_observado, unidad_observada "
                        "FROM propuestas_hallazgos_evidencias WHERE hallazgo_id = :h "
                        "ORDER BY resultado_id"
                    ),
                    {"h": hallazgo.id},
                ).all()
                findings.append(
                    FindingView(
                        id=_canonical(hallazgo.id),
                        rule_code=hallazgo.codigo,
                        rule_version=hallazgo.regla_version,
                        tipo=hallazgo.tipo,
                        explicacion=hallazgo.explicacion,
                        estado_revision=hallazgo.estado_revision,
                        evidence=tuple(
                            (
                                _canonical(item.resultado_id),
                                None
                                if item.valor_observado is None
                                else Decimal(item.valor_observado),
                                item.unidad_observada,
                            )
                            for item in evidence
                        ),
                    )
                )
        return ProposalView(
            id=_canonical(proposal.id),
            order_id=_canonical(proposal.orden_id),
            estado=proposal.estado,
            version_motor=proposal.version_motor,
            resumen=proposal.resumen_propuesto or "",
            creado_en=proposal.creado_en,
            findings=tuple(findings),
            decision=decision,
            conclusion_medica=proposal.conclusion_medica,
        )

    def proposal_owner(self, proposal_id: str) -> OrderForEvaluation | None:
        guid = _as_guid(proposal_id)
        if guid is None:
            return None
        with self._engine.connect() as conn:
            order_id = conn.execute(
                text("SELECT orden_id FROM propuestas_clinicas WHERE id = :p"), {"p": guid}
            ).scalar()
        if order_id is None:
            return None
        return self.load_order_for_evaluation(_canonical(order_id))

    def review_proposal(
        self,
        proposal_id: str,
        medico_user_id: str,
        decision: str,
        motivo: str | None,
        conclusion_corregida: str | None,
        now: datetime,
    ) -> str:
        return retry_on_deadlock(
            lambda: self._review_proposal_once(
                proposal_id, medico_user_id, decision, motivo, conclusion_corregida, now
            )
        )

    def _review_proposal_once(
        self,
        proposal_id: str,
        medico_user_id: str,
        decision: str,
        motivo: str | None,
        conclusion_corregida: str | None,
        now: datetime,
    ) -> str:
        guid = _as_guid(proposal_id)
        if guid is None:
            return "NOT_FOUND"
        moment = _naive(now)
        with self._engine.begin() as conn:
            state = conn.execute(
                text(
                    "SELECT estado FROM propuestas_clinicas WITH (UPDLOCK, ROWLOCK) WHERE id = :p"
                ),
                {"p": guid},
            ).scalar()
            if state is None:
                return "NOT_FOUND"
            if state != "PENDIENTE":
                return "NOT_PENDING"
            medico = conn.execute(
                text("SELECT id FROM medicos WHERE usuario_id = :u"), {"u": medico_user_id}
            ).scalar()
            conn.execute(
                text(
                    "INSERT INTO propuestas_revisiones (id, propuesta_id, medico_id, decision, "
                    "motivo, conclusion_corregida, revisada_en) "
                    "VALUES (:id, :p, :m, :decision, :motivo, :corregida, :ahora)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "p": guid,
                    "m": medico,
                    "decision": decision,
                    "motivo": motivo,
                    "corregida": conclusion_corregida,
                    "ahora": moment,
                },
            )
            new_state = _DECISION_STATE[decision]
            conn.execute(
                text(
                    "UPDATE propuestas_clinicas SET estado = :estado, revisado_por = :m, "
                    "revisado_en = :ahora, conclusion_medica = :conclusion WHERE id = :p"
                ),
                {
                    "estado": new_state,
                    "m": medico,
                    "ahora": moment,
                    "conclusion": conclusion_corregida or motivo,
                    "p": guid,
                },
            )
            conn.execute(
                text(
                    "UPDATE propuestas_hallazgos SET estado_revision = :e WHERE propuesta_id = :p"
                ),
                {"e": new_state, "p": guid},
            )
            _outbox(conn, guid, "propuesta.revisada", {"decision": decision}, moment, 2)
        return "CREATED"

    def record_audit(self, event: AuditEvent) -> None:
        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO auditoria_accesos (id, usuario_id, paciente_id, entidad, "
                    "entidad_id, accion, fecha_evento, resultado) "
                    "VALUES (:id, :u, :p, :entidad, :entidad_id, :accion, :ahora, :resultado)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "u": event.actor_user_id,
                    "p": event.paciente_id,
                    "entidad": event.entidad,
                    "entidad_id": _as_guid(event.entidad_id),
                    "accion": event.accion,
                    "ahora": _naive(event.occurred_at),
                    "resultado": event.resultado,
                },
            )


def _outbox(
    conn: Connection,
    aggregate_id: str,
    event: str,
    payload: dict,
    now: datetime,
    version: int,
) -> None:
    conn.execute(
        text(
            "INSERT INTO eventos_outbox (id, agregado_tipo, agregado_id, tipo_evento, payload, "
            "version_agregado, creado_en) "
            "VALUES (:id, 'propuesta_clinica', :a, :tipo, :payload, :version, :ahora)"
        ),
        {
            "id": str(uuid.uuid4()),
            "a": aggregate_id,
            "tipo": event,
            "payload": json.dumps(payload),
            "version": version,
            "ahora": now,
        },
    )
