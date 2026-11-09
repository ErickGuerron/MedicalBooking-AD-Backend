"""Shadow findings for one order (feature doc S9, S13, S14).

Reading is read-only: nothing is stored or changed. The ordering doctor reads the three findings
next to the normal flow; every read and every denial is audited. Storing a snapshot is an explicit
action of the same doctor: it writes one immutable snapshot with the evidence of each finding, and
never changes the order, the result or the diagnosis record.
Inputs come from the order's results, mapped by verified LOINC code (domain/loinc_roles.py).
Pregnancy is not in the patient profile yet, so women of reproductive age are not evaluable for
anaemia and iron deficiency until that field exists.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal

from medicalbooking.modules.clinical.application.ports import (
    ClinicalStore,
    OrderForEvaluation,
    ShadowEvidenceRecord,
    ShadowItemRecord,
    ShadowSnapshotRecord,
)
from medicalbooking.modules.clinical.domain.hb_adjustments import total_adjustment
from medicalbooking.modules.clinical.domain.loinc_roles import (
    AmbiguousResult,
    evidence_by_role,
    roles_from_loinc,
)
from medicalbooking.modules.clinical.domain.shadow import ShadowInputs, ShadowReport, shadow_report
from medicalbooking.modules.clinical.domain.snapshot import (
    ROLES_BY_FINDING,
    RULE_VERSION,
    snapshot_items,
)
from medicalbooking.modules.identity.application.ports import AccessProfile
from medicalbooking.modules.laboratory.application.ports import AuditEvent
from medicalbooking.shared.domain.errors import Conflict, Forbidden

PERM_READ = "resultados.leer_con_acceso"
_FORBIDDEN = "Access to this resource is not allowed"
_SEXES = {"M", "F"}
_ENTIDAD_LECTURA = "modo_sombra"
_ENTIDAD_SNAPSHOT = "hallazgos_sombra"


class ShadowFindingsService:
    def __init__(self, store: ClinicalStore, clock: Callable[[], datetime]) -> None:
        self._store = store
        self._clock = clock

    def get(self, actor: AccessProfile, order_id: str) -> ShadowReport:
        order = self._authorize(actor, order_id, _ENTIDAD_LECTURA, "consultar")
        try:
            roles = roles_from_loinc(self._store.loinc_results_for_order(order_id))
        except AmbiguousResult as error:
            raise Conflict(str(error)) from error
        report = self._report(order.paciente_id, roles)
        self._audit(actor, order.paciente_id, _ENTIDAD_LECTURA, order_id, "consultar", "OK")
        return report

    def record_snapshot(self, actor: AccessProfile, order_id: str) -> ShadowSnapshotRecord:
        order = self._authorize(actor, order_id, _ENTIDAD_SNAPSHOT, "registrar")
        try:
            evidence = evidence_by_role(self._store.loinc_evidence_for_order(order_id))
        except AmbiguousResult as error:
            raise Conflict(str(error)) from error
        roles = {role: item.valor for role, item in evidence.items()}
        report = self._report(order.paciente_id, roles)

        items = tuple(
            ShadowItemRecord(
                hallazgo=item.hallazgo,
                categoria=item.categoria,
                corte_valor=item.corte_valor,
                corte_unidad=item.corte_unidad,
                fuente=item.fuente,
                motivo=item.motivo,
                evidencia=tuple(
                    ShadowEvidenceRecord(
                        rol=role,
                        resultado_id=evidence[role].resultado_id,
                        valor=evidence[role].valor,
                        unidad=evidence[role].unidad,
                    )
                    for role in ROLES_BY_FINDING[item.hallazgo]
                    if role in evidence
                ),
            )
            for item in snapshot_items(report)
        )
        now = self._clock()
        snapshot_id = self._store.record_shadow_snapshot(
            order_id, RULE_VERSION, items, actor.user_id, now
        )
        self._audit(actor, order.paciente_id, _ENTIDAD_SNAPSHOT, order_id, "registrar", "OK")
        return ShadowSnapshotRecord(
            id=snapshot_id,
            orden_id=order_id,
            version_regla=RULE_VERSION,
            creado_en=now,
            items=items,
        )

    # ---------- helpers ----------

    def _authorize(
        self, actor: AccessProfile, order_id: str, entidad: str, accion: str
    ) -> OrderForEvaluation:
        """Permission first, then ownership by the ordering doctor. Every denial is audited."""
        if PERM_READ not in actor.permissions:
            self._audit(actor, None, entidad, order_id, accion, "DENEGADO")
            raise Forbidden(_FORBIDDEN)
        order = self._store.load_order_for_evaluation(order_id)
        if order is None or order.medico_user_id != actor.user_id:
            self._audit(
                actor,
                order.paciente_id if order else None,
                entidad,
                order_id,
                accion,
                "DENEGADO",
            )
            raise Forbidden(_FORBIDDEN)
        return order

    def _report(self, paciente_id: str, roles: dict[str, Decimal]) -> ShadowReport:
        sex, birth = self._store.patient_sex_birth(paciente_id)
        altitude, smoker, cigarettes = self._store.patient_clinical_profile(paciente_id)
        ajuste = total_adjustment(altitude, smoker, cigarettes)
        today = self._clock().date()
        return shadow_report(_inputs(roles, sex, birth, today, ajuste))

    def _audit(
        self,
        actor: AccessProfile,
        paciente_id: str | None,
        entidad: str,
        order_id: str,
        accion: str,
        resultado: str,
    ) -> None:
        self._store.record_audit(
            AuditEvent(
                actor_user_id=actor.user_id,
                paciente_id=paciente_id,
                entidad=entidad,
                entidad_id=order_id,
                accion=accion,
                resultado=resultado,
                occurred_at=self._clock(),
            )
        )


def _age_months(birth: date | None, today: date) -> int | None:
    if birth is None:
        return None
    months = (today.year - birth.year) * 12 + (today.month - birth.month)
    return months - 1 if today.day < birth.day else months


def _inputs(
    roles: dict[str, Decimal],
    sex: str | None,
    birth: date | None,
    today: date,
    ajuste_g_l: int | None,
) -> ShadowInputs:
    fasting = roles.get("glucosa_ayuno")
    hba1c = roles.get("hba1c")
    return ShadowInputs(
        sex=sex if sex in _SEXES else None,
        age_months=_age_months(birth, today),
        pregnancy=None,
        hemoglobin_g_dl=roles.get("hemoglobina"),
        ferritin_ug_l=roles.get("ferritina"),
        crp_mg_l=roles.get("pcr"),
        fasting_mg_dl=[fasting] if fasting is not None else [],
        hba1c_pct=[hba1c] if hba1c is not None else [],
        ogtt_2h_mg_dl=[],
        random_mg_dl=None,
        symptoms=None,
        ajuste_g_l=ajuste_g_l,
    )
