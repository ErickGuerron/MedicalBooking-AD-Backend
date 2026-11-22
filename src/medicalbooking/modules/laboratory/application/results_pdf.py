"""The laboratory results report: a formal clinical document that gives the space to the results.

Only the data needed to identify the report and read the results is printed: the patient's name,
the document number masked to its last four digits, age and sex (which the reference values depend
on), the requesting doctor's name, the observations of the order, and the results themselves.
Contact details, birth date, registration numbers and the order status are not printed (data
minimisation under the Ley Orgánica de Protección de Datos Personales).

No external PDF library; drawing operators and the standard Helvetica fonts (see ``results_report``
for the primitives). Pure functions: the caller passes order, results and time.
"""

from __future__ import annotations

from datetime import datetime

from medicalbooking.modules.laboratory.application.ports import (
    LabDetail,
    LabOrder,
    LabResult,
    ReportParties,
)
from medicalbooking.modules.laboratory.application.results_report import (
    ECUADOR,
    PAGE_H,
    PAGE_W,
    SEX,
    _clip,
    _number,
    _serialize,
    _wrap,
)
from medicalbooking.modules.payments.application.invoice_report import (
    INK,
    ISSUER_NAME,
    MUTED,
    NAVY,
    RULE,
    Sheet,
    masked_document,
)

MARGIN = 44.0
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_TOP = 58.0
# Lowest baseline of the body; the footer sits below it.
BOTTOM = 76.0
ROW_H = 30.0
HEAD_FILL = (0xF1, 0xF3, 0xF8)

NOTE = (
    "Los valores de referencia dependen de la edad, el sexo y el método del laboratorio. "
    "Este informe apoya la consulta con su médico y no constituye un diagnóstico por sí mismo. "
    "Los datos personales y de salud se tratan conforme a la Ley Orgánica de Protección de Datos "
    "Personales."
)
CONFIDENTIAL = (
    "Documento confidencial con datos de salud. Uso limitado al paciente y personal autorizado."
)
OBSERVATION_CHARS = 110
NOTE_CHARS = 120
EM_DASH = "—"

INTERPRETATION = {
    "BAJO": "Bajo",
    "ALTO": "Alto",
    "DENTRO_REFERENCIA": "Dentro de rango",
    "NO_EVALUABLE": "No evaluable",
}
OUT_OF_RANGE = frozenset({"BAJO", "ALTO"})

# Result table columns, measured from the left margin.
COL_EXAM = 6.0
COL_RESULT = 186.0
COL_UNIT = 262.0
COL_REFERENCE = 320.0
COL_TAG = 430.0


def _age(born, generated_at: datetime) -> int | None:
    if born is None:
        return None
    return (
        generated_at.year
        - born.year
        - ((generated_at.month, generated_at.day) < (born.month, born.day))
    )


def _age_and_sex(parties: ReportParties, generated_at: datetime) -> str:
    age = _age(parties.patient_birth_date, generated_at)
    sex = SEX.get(parties.patient_sex or "", "")
    parts = [f"{age} años" if age is not None else "", sex]
    return " · ".join(part for part in parts if part) or EM_DASH


def _result_text(result: LabResult) -> str:
    if result.valor_numerico is not None:
        return _number(result.valor_numerico)
    return result.valor_texto or EM_DASH


def _reference_text(result: LabResult) -> str:
    low, high = result.referencia_min, result.referencia_max
    if low is None and high is None:
        return EM_DASH
    if low is not None and high is not None:
        return f"{_number(low)} – {_number(high)}"
    if low is not None:
        return f">= {_number(low)}"
    return f"<= {_number(high)}"


class _Report:
    def __init__(self, parties: ReportParties, order: LabOrder) -> None:
        self.parties = parties
        self.order_code = order.id[:8].upper()
        self.pages: list[Sheet] = [Sheet()]

    @property
    def page(self) -> Sheet:
        return self.pages[-1]

    def new_page(self) -> float:
        """A continuation page: a running header with who the report is about. Returns y."""
        self.pages.append(Sheet())
        page = self.page
        page.text(MARGIN, PAGE_H - 40, "Medilo", 10, bold=True, color=NAVY)
        page.text(
            MARGIN + 44,
            PAGE_H - 40,
            f"Resultados de laboratorio · {_clip(self.parties.patient_name, 36)}"
            f" · Orden {self.order_code}",
            8.5,
            color=MUTED,
        )
        page.path(
            (("m", MARGIN, PAGE_H - 50), ("l", PAGE_W - MARGIN, PAGE_H - 50)),
            stroke=NAVY,
            width=0.8,
        )
        return PAGE_H - 72

    def render(self) -> bytes:
        total = len(self.pages)
        for number, page in enumerate(self.pages, start=1):
            _footer(page, number, total, self.order_code)
        return _serialize(self.pages)


def _footer(page: Sheet, number: int, total: int, order_code: str) -> None:
    page.path((("m", MARGIN, FOOTER_TOP), ("l", PAGE_W - MARGIN, FOOTER_TOP)), stroke=RULE)
    page.text(MARGIN, 44, CONFIDENTIAL, 7.5, color=MUTED)
    page.text(
        MARGIN, 32, f"{ISSUER_NAME} · Laboratorio clínico · Orden {order_code}", 7.5, color=MUTED
    )
    page.text(PAGE_W - MARGIN - 60, 32, f"Página {number} de {total}", 7.5, color=MUTED)


def _masthead(page: Sheet, order: LabOrder, generated_at: datetime) -> None:
    """A plain formal heading: the issuer, the report title and when it was generated."""
    page.text(MARGIN, PAGE_H - 46, f"{ISSUER_NAME} · Laboratorio clínico", 9, bold=True, color=NAVY)
    page.path(
        (("m", MARGIN, PAGE_H - 56), ("l", PAGE_W - MARGIN, PAGE_H - 56)),
        stroke=NAVY,
        width=1.2,
    )
    page.text(
        MARGIN, PAGE_H - 84, "Informe de resultados de laboratorio", 17, bold=True, color=NAVY
    )
    local = generated_at.astimezone(ECUADOR)
    page.text(
        MARGIN,
        PAGE_H - 102,
        f"Orden {order.id[:8].upper()} · generado el {local:%d/%m/%Y %H:%M} (hora de Ecuador)",
        9,
        color=INK,
    )


def _pair(page: Sheet, x: float, y: float, label: str, value: str, *, bold: bool = False) -> None:
    page.text(x, y, label, 7.5, color=MUTED)
    page.text(x, y - 14, value, 10.5 if bold else 9.5, bold=bold, color=NAVY if bold else INK)


def _patient_block(
    page: Sheet, order: LabOrder, parties: ReportParties, generated_at: datetime, top: float
) -> float:
    """Who the report is about, as a plain two-by-two grid with thin rules."""
    height = 80.0
    bottom = top - height
    page.box(MARGIN, bottom, CONTENT_W, height, stroke=RULE)
    page.path(
        (("m", MARGIN + CONTENT_W / 2, bottom), ("l", MARGIN + CONTENT_W / 2, top)),
        stroke=RULE,
        width=0.5,
    )
    page.path(
        (("m", MARGIN, top - height / 2), ("l", PAGE_W - MARGIN, top - height / 2)),
        stroke=RULE,
        width=0.5,
    )

    left = MARGIN + 10
    right = MARGIN + CONTENT_W / 2 + 10
    _pair(page, left, top - 14, "Paciente", _clip(parties.patient_name, 42), bold=True)
    _pair(
        page, right, top - 14, "Documento de identidad", masked_document(parties.patient_document)
    )
    _pair(page, left, top - 14 - height / 2, "Edad y sexo", _age_and_sex(parties, generated_at))
    _pair(
        page,
        right,
        top - 14 - height / 2,
        "Médico solicitante",
        _clip(parties.doctor_name or EM_DASH, 36),
    )
    return bottom


def _observations(report: _Report, order: LabOrder, y: float) -> float:
    """Observations wrap over as many lines as they need, never cut short."""
    if not order.observaciones:
        return y
    page = report.page
    page.text(MARGIN, y, "Observaciones", 7.5, color=MUTED)
    y -= 13
    for line in _wrap(order.observaciones, OBSERVATION_CHARS):
        if y < BOTTOM:
            y = report.new_page()
            page = report.page
        page.text(MARGIN, y, line, 9.5, color=INK)
        y -= 13
    return y - 10


def _section_title(page: Sheet, y: float, title: str) -> float:
    """The name of the test, bold, over a navy rule. Returns the y below it."""
    page.text(MARGIN, y - 13, _clip(title, 80), 11, bold=True, color=NAVY)
    page.path((("m", MARGIN, y - 19), ("l", PAGE_W - MARGIN, y - 19)), stroke=NAVY, width=0.8)
    return y - 19


def _table_head(page: Sheet, y: float) -> float:
    page.box(MARGIN, y - 20, CONTENT_W, 20, fill=HEAD_FILL)
    for text, x in (
        ("EXAMEN", COL_EXAM),
        ("RESULTADO", COL_RESULT),
        ("UNIDAD", COL_UNIT),
        ("VALORES DE REFERENCIA", COL_REFERENCE),
        ("INTERPRETACIÓN", COL_TAG),
    ):
        page.text(MARGIN + x, y - 13, text, 7, bold=True, color=NAVY)
    return y - 20


def _row(page: Sheet, y: float, result: LabResult) -> float:
    flagged = result.interpretacion in OUT_OF_RANGE
    label = INTERPRETATION.get(result.interpretacion or "", EM_DASH)
    page.text(MARGIN + COL_EXAM, y - 19, _clip(result.nombre_analito, 34), 10.5, color=INK)
    page.text(
        MARGIN + COL_RESULT,
        y - 19,
        _clip(_result_text(result), 14),
        10,
        bold=True,
        color=NAVY,
    )
    page.text(MARGIN + COL_UNIT, y - 19, _clip(result.unidad or "", 10), 9, color=MUTED)
    page.text(MARGIN + COL_REFERENCE, y - 19, _clip(_reference_text(result), 16), 10, color=INK)
    page.text(MARGIN + COL_TAG, y - 19, label, 9, bold=flagged, color=NAVY if flagged else MUTED)
    page.path(
        (("m", MARGIN, y - ROW_H), ("l", PAGE_W - MARGIN, y - ROW_H)),
        stroke=RULE,
        width=0.4,
    )
    return y - ROW_H


def _heading(detail: LabDetail, number: int) -> str:
    return detail.nombre or f"Examen {number}"


def _closing(report: _Report, y: float, corrected: int) -> None:
    """The clinical note, the laboratory's corrections if any, and the signature lines."""
    note = _wrap(NOTE, NOTE_CHARS)
    if corrected:
        note = note + _wrap(
            f"El laboratorio revis? la interpretaci?n de {corrected} resultado(s) "
            "y registr? el motivo de cada correcci?n.",
            NOTE_CHARS,
        )
    needed = 24 + 12 * len(note) + 80
    if y - needed < BOTTOM:
        y = report.new_page()
    page = report.page
    y -= 10
    page.text(MARGIN, y, "Nota", 8.5, bold=True, color=NAVY)
    y -= 13
    for line in note:
        page.text(MARGIN, y, line, 8.5, color=MUTED)
        y -= 12
    y -= 40
    half = (CONTENT_W - 40) / 2
    for index, caption in enumerate(("Responsable del laboratorio", "Médico solicitante")):
        x = MARGIN + index * (half + 40)
        page.path((("m", x, y), ("l", x + half, y)), stroke=NAVY, width=0.8)
        page.text(x, y - 13, caption, 8, color=MUTED)


def build_results_report(
    order: LabOrder,
    results: list[LabResult],
    *,
    parties: ReportParties,
    generated_at: datetime,
    status_label: str | None = None,
) -> bytes:
    """One PDF for the order: the heading, the patient grid, then a table of results per test."""
    report = _Report(parties, order)
    page = report.page
    _masthead(page, order, generated_at)
    y = _patient_block(page, order, parties, generated_at, PAGE_H - 122) - 22
    y = _observations(report, order, y)

    by_detail: dict[str, list[LabResult]] = {}
    for result in results:
        by_detail.setdefault(result.detalle_id, []).append(result)

    if not results:
        report.page.text(
            MARGIN, y, "Todavía no hay resultados validados para esta orden.", 10.5, color=MUTED
        )
        y -= 20

    for number, detail in enumerate(order.details, start=1):
        rows = by_detail.get(detail.id, [])
        if not rows:
            continue
        # The title, the column titles and the first row stay together.
        if y - (19 + 20 + ROW_H) < BOTTOM:
            y = report.new_page()
        page = report.page
        y = _table_head(page, _section_title(page, y, _heading(detail, number)))
        for result in rows:
            if y - ROW_H < BOTTOM:
                y = report.new_page()
                page = report.page
                y = _table_head(page, y)
            y = _row(page, y, result)
        y -= 22

    corrected = sum(1 for result in results if result.corregido)
    _closing(report, y, corrected)
    return report.render()
