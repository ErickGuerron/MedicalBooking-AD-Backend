"""Laboratory business rules (E3): pure functions, no I/O.

Results are only what was entered by hand. Clinical classification (ranges, categories)
belongs to E5 and is never produced here.
"""

from __future__ import annotations

import math
import re
from decimal import Decimal

from medicalbooking.shared.domain.errors import ValidationFailed

PERM_ORDER_CREATE = "ordenes.crear"
PERM_RESULT_REGISTER = "resultados.registrar"
PERM_RESULT_READ_OWN = "resultados.leer_propios"
PERM_RESULT_READ_CONTEXT = "resultados.leer_con_acceso"
PERM_REVIEW = "revisiones.registrar"

PRIORITIES = frozenset({"NORMAL", "URGENTE"})
DOCUMENT_TYPES = frozenset({"INFORME_LABORATORIO"})
PDF_MIME = "application/pdf"
PDF_SIGNATURE = b"%PDF-"
MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_TESTS_PER_ORDER = 20
MAX_TEXT = 300
MAX_NAME = 150
MAX_UNIT = 40
MAX_CONCLUSION = 4000
MAX_FILENAME = 255
_NUMERIC_LIMIT = Decimal("999999999999.999999")
_UUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def validate_priority(priority: str) -> None:
    if priority not in PRIORITIES:
        raise ValidationFailed("Unknown order priority")


def validate_test_ids(test_ids: list[str]) -> list[str]:
    if not test_ids:
        raise ValidationFailed("An order needs at least one test type")
    if len(test_ids) > MAX_TESTS_PER_ORDER:
        raise ValidationFailed("Too many test types in one order")
    if len(set(test_ids)) != len(test_ids):
        raise ValidationFailed("A test type can appear only once per order")
    for test_id in test_ids:
        if not _UUID.match(str(test_id)):
            raise ValidationFailed("Test type identifiers must be UUIDs")
    return [str(test_id).lower() for test_id in test_ids]


def validate_observations(text: str | None) -> None:
    if text is not None and len(text) > 1000:
        raise ValidationFailed("Observations are too long")


def validate_result_values(
    *, nombre: str, valor_texto: str | None, valor_numerico: float | None, unidad: str | None
) -> None:
    if not nombre or len(nombre) > MAX_NAME:
        raise ValidationFailed("Analyte name is required and must be short")
    if valor_texto is None and valor_numerico is None:
        raise ValidationFailed("A result needs a text or a numeric value")
    if valor_texto is not None and len(valor_texto) > MAX_TEXT:
        raise ValidationFailed("Text value is too long")
    if valor_numerico is not None:
        if not math.isfinite(valor_numerico):
            raise ValidationFailed("Numeric value must be finite")
        if abs(Decimal(str(valor_numerico))) > _NUMERIC_LIMIT:
            raise ValidationFailed("Numeric value is out of range")
    if unidad is not None and len(unidad) > MAX_UNIT:
        raise ValidationFailed("Unit is too long")


def validate_conclusion(conclusion: str, recommendations: str | None) -> None:
    if not conclusion or not conclusion.strip():
        raise ValidationFailed("A medical review needs a conclusion")
    if len(conclusion) > MAX_CONCLUSION:
        raise ValidationFailed("Conclusion is too long")
    if recommendations is not None and len(recommendations) > MAX_CONCLUSION:
        raise ValidationFailed("Recommendations are too long")


def validate_pdf(content: bytes, mime: str) -> None:
    if mime != PDF_MIME:
        raise ValidationFailed("Only PDF documents are accepted")
    if not content:
        raise ValidationFailed("The document is empty")
    if len(content) > MAX_PDF_BYTES:
        raise ValidationFailed("The document is larger than 10 MB")
    if not content.startswith(PDF_SIGNATURE):
        raise ValidationFailed("The content is not a PDF document")


def validate_document_type(document_type: str) -> None:
    if document_type not in DOCUMENT_TYPES:
        raise ValidationFailed("Unknown document type")


def safe_filename(name: str) -> str:
    """Keep only the final component; the stored name is generated, never this value."""
    base = re.split(r"[\\/]", name)[-1].strip()
    base = re.sub(r"[\x00-\x1f]", "", base)[:MAX_FILENAME]
    if not base:
        raise ValidationFailed("File name is required")
    return base
