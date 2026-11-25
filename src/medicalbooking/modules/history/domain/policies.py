"""Clinical history rules that do not depend on infrastructure.

Every change of state is a transition declared here; anything not declared is refused. A verified
entry names the professional who verified it. Vital ranges and text limits mirror the migration's
CHECK constraints so a bad value is refused before it reaches SQL Server.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

from medicalbooking.shared.domain.errors import Conflict, ValidationFailed

# ---------- entry kinds, states and transitions ----------

ORIGEN_MEDICO = "REGISTRADA_MEDICO"
ORIGEN_PACIENTE = "DECLARADA_PACIENTE"
ORIGENES = (ORIGEN_MEDICO, ORIGEN_PACIENTE)

ALERGIA_CATEGORIAS = ("MEDICAMENTO", "ALIMENTO", "LATEX", "AMBIENTAL", "OTRA")
ALERGIA_SEVERIDADES = ("LEVE", "MODERADA", "GRAVE", "DESCONOCIDA")
ANTECEDENTE_TIPOS = ("PERSONAL", "FAMILIAR", "QUIRURGICO")

MOTIVO_MAX = 500
SUSTANCIA_MAX = 150
REACCION_MAX = 300
DESCRIPCION_ANTECEDENTE_MAX = 500
PARENTESCO_MAX = 50
MEDICAMENTO_MAX = 150
DOSIS_MAX = 80
FRECUENCIA_MAX = 80
PROBLEMA_MAX = 300
VACUNA_MAX = 120
LOTE_MAX = 40
CIE11_MAX = 20

_CIE11 = re.compile(r"^[0-9A-Z]{1,4}(\.[0-9A-Z]{1,4})*$")

# Permissions seeded by migration 0045. Only the treating doctor reads and registers; the patient
# reads and declares their own entries; nobody else receives any of them.
PERM_LEER = "historia.leer"
PERM_REGISTRAR = "historia.registrar"
PERM_DECLARAR = "historia.declarar"
PERM_LEER_PROPIA = "historia.leer_propia"

# The table behind each kind of entry. Only these names reach SQL, never a value from the request.
TABLE_BY_KIND: dict[str, str] = {
    "alergia": "historia_alergias",
    "antecedente": "historia_antecedentes",
    "medicacion": "historia_medicacion",
    "problema": "historia_problemas",
    "vacuna": "historia_vacunas",
}
KIND_BY_PATH: dict[str, str] = {
    "alergias": "alergia",
    "antecedentes": "antecedente",
    "medicacion": "medicacion",
    "problemas": "problema",
    "vacunas": "vacuna",
}


@dataclass(frozen=True)
class EntryKind:
    """One kind of history entry: its states, the initial state and the allowed transitions."""

    name: str
    initial: str
    states: frozenset[str]
    transitions: dict[str, frozenset[str]]
    terminal: frozenset[str]


ALERGIA = EntryKind(
    name="alergia",
    initial="ACTIVA",
    states=frozenset({"ACTIVA", "RESUELTA", "ERRONEA"}),
    transitions={
        "ACTIVA": frozenset({"RESUELTA", "ERRONEA"}),
        "RESUELTA": frozenset({"ACTIVA", "ERRONEA"}),
    },
    terminal=frozenset({"ERRONEA"}),
)
ANTECEDENTE = EntryKind(
    name="antecedente",
    initial="ACTIVO",
    states=frozenset({"ACTIVO", "RESUELTO", "ERRONEO"}),
    transitions={
        "ACTIVO": frozenset({"RESUELTO", "ERRONEO"}),
        "RESUELTO": frozenset({"ACTIVO", "ERRONEO"}),
    },
    terminal=frozenset({"ERRONEO"}),
)
MEDICACION = EntryKind(
    name="medicacion",
    initial="ACTIVA",
    states=frozenset({"ACTIVA", "SUSPENDIDA", "ERRONEA"}),
    transitions={
        "ACTIVA": frozenset({"SUSPENDIDA", "ERRONEA"}),
        "SUSPENDIDA": frozenset({"ACTIVA", "ERRONEA"}),
    },
    terminal=frozenset({"ERRONEA"}),
)
PROBLEMA = EntryKind(
    name="problema",
    initial="ACTIVO",
    states=frozenset({"ACTIVO", "RESUELTO", "ERRONEO"}),
    transitions={
        "ACTIVO": frozenset({"RESUELTO", "ERRONEO"}),
        "RESUELTO": frozenset({"ACTIVO", "ERRONEO"}),
    },
    terminal=frozenset({"ERRONEO"}),
)
VACUNA = EntryKind(
    name="vacuna",
    initial="ACTIVA",
    states=frozenset({"ACTIVA", "ERRONEA"}),
    transitions={"ACTIVA": frozenset({"ERRONEA"})},
    terminal=frozenset({"ERRONEA"}),
)


def validate_transition(kind: EntryKind, current: str, target: str, motivo: str | None) -> None:
    """A change of state needs a motivo and must be declared for that kind of entry."""
    if target not in kind.states:
        raise ValidationFailed(f"Unknown state for {kind.name}")
    if current in kind.terminal:
        raise Conflict(f"This {kind.name} is final and cannot change")
    if target not in kind.transitions.get(current, frozenset()):
        raise Conflict(f"This {kind.name} cannot go from {current} to {target}")
    validate_motivo(motivo)


def validate_motivo(motivo: str | None) -> str:
    text = (motivo or "").strip()
    if not text:
        raise ValidationFailed("'motivo' is required for this change")
    if len(text) > MOTIVO_MAX:
        raise ValidationFailed(f"'motivo' must be at most {MOTIVO_MAX} characters")
    return text


def can_verify(origen: str, verificada: bool) -> None:
    """Only a declared entry needs verifying, and only once."""
    if verificada:
        raise Conflict("This entry is already verified")
    if origen != ORIGEN_PACIENTE:
        raise Conflict("Only a declared entry needs verifying")


def origin_and_verification(is_doctor: bool) -> tuple[str, bool]:
    """A doctor's entry is verified by that doctor at once; a patient's declaration is not."""
    return (ORIGEN_MEDICO, True) if is_doctor else (ORIGEN_PACIENTE, False)


# ---------- text ----------


def _required(value: str | None, field: str, maximum: int) -> str:
    text = (value or "").strip()
    if not text:
        raise ValidationFailed(f"'{field}' is required")
    if len(text) > maximum:
        raise ValidationFailed(f"'{field}' must be at most {maximum} characters")
    return text


def _optional(value: str | None, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if not text:
        return None
    if len(text) > maximum:
        raise ValidationFailed(f"'{field}' must be at most {maximum} characters")
    return text


def validate_cie11(value: str | None) -> str | None:
    """CIE-11 codes by format only (for example BA00 or 5A11.0). The catalog is not consulted."""
    text = _optional(value, "cie11", CIE11_MAX)
    if text is None:
        return None
    if not _CIE11.match(text.upper()):
        raise ValidationFailed("'cie11' must be a CIE-11 code, for example BA00")
    return text.upper()


def validate_alergia(
    *, sustancia: str | None, categoria: str | None, severidad: str | None, reaccion: str | None
) -> dict[str, str | None]:
    if categoria not in ALERGIA_CATEGORIAS:
        raise ValidationFailed("'categoria' is not a known allergy category")
    if severidad not in ALERGIA_SEVERIDADES:
        raise ValidationFailed("'severidad' is not a known severity")
    return {
        "sustancia": _required(sustancia, "sustancia", SUSTANCIA_MAX),
        "categoria": categoria,
        "severidad": severidad,
        "reaccion": _optional(reaccion, "reaccion", REACCION_MAX),
    }


def validate_antecedente(
    *, tipo: str | None, descripcion: str | None, parentesco: str | None, cie11: str | None
) -> dict[str, str | None]:
    if tipo not in ANTECEDENTE_TIPOS:
        raise ValidationFailed("'tipo' is not a known antecedent type")
    parentesco_text = _optional(parentesco, "parentesco", PARENTESCO_MAX)
    if tipo == "FAMILIAR" and parentesco_text is None:
        raise ValidationFailed("'parentesco' is required for a family antecedent")
    if tipo != "FAMILIAR":
        parentesco_text = None
    return {
        "tipo": tipo,
        "descripcion": _required(descripcion, "descripcion", DESCRIPCION_ANTECEDENTE_MAX),
        "parentesco": parentesco_text,
        "cie11": validate_cie11(cie11),
    }


def validate_medicacion(
    *, medicamento: str | None, dosis: str | None, frecuencia: str | None, inicio: date | None
) -> dict[str, object]:
    return {
        "medicamento": _required(medicamento, "medicamento", MEDICAMENTO_MAX),
        "dosis": _optional(dosis, "dosis", DOSIS_MAX),
        "frecuencia": _optional(frecuencia, "frecuencia", FRECUENCIA_MAX),
        "inicio": inicio,
    }


def validate_problema(
    *, descripcion: str | None, cie11: str | None, inicio: date | None
) -> dict[str, object]:
    return {
        "descripcion": _required(descripcion, "descripcion", PROBLEMA_MAX),
        "cie11": validate_cie11(cie11),
        "inicio": inicio,
    }


def validate_vacuna(
    *,
    vacuna: str | None,
    dosis_numero: int | None,
    fecha: date | None,
    lote: str | None,
    today: date,
) -> dict[str, object]:
    # bool is an int in Python: refuse it explicitly so `true` is not dose 1.
    if (
        isinstance(dosis_numero, bool)
        or not isinstance(dosis_numero, int)
        or not 1 <= dosis_numero <= 10
    ):
        raise ValidationFailed("'dosis_numero' must be an integer from 1 to 10")
    if fecha is None:
        raise ValidationFailed("'fecha' is required")
    if fecha > today:
        raise ValidationFailed("'fecha' cannot be in the future")
    return {
        "vacuna": _required(vacuna, "vacuna", VACUNA_MAX),
        "dosis_numero": dosis_numero,
        "fecha": fecha,
        "lote": _optional(lote, "lote", LOTE_MAX),
    }


# ---------- vital signs ----------

VITAL_RANGES: dict[str, tuple[float, float]] = {
    "peso_kg": (0.5, 300.0),
    "talla_cm": (30.0, 250.0),
    "pa_sistolica": (50, 260),
    "pa_diastolica": (30, 160),
    "frecuencia_cardiaca": (20, 250),
    "temperatura_c": (30.0, 45.0),
    "saturacion": (50, 100),
}


def validate_vitales(values: dict[str, float | int | None]) -> dict[str, float | int | None]:
    """Only the measures sent are checked; at least one must be present. Booleans are refused."""
    unknown = set(values) - set(VITAL_RANGES)
    if unknown:
        raise ValidationFailed("Unknown vital sign")
    present = {key: value for key, value in values.items() if value is not None}
    if not present:
        raise ValidationFailed("At least one vital sign is required")
    for key, value in present.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationFailed(f"'{key}' must be a number")
        low, high = VITAL_RANGES[key]
        if not low <= value <= high:
            raise ValidationFailed(f"'{key}' is outside the accepted range")
    return {key: values.get(key) for key in VITAL_RANGES}
