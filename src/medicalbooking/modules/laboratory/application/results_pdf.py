"""The laboratory results report, laid out like a clinical lab report in the Medilo style.

Structure of a common lab report: who the result is about and who requested it, then one table
per test (exam, result, unit, reference values, interpretation), a clinical note and the lines
for the signatures. The look is the app's: the orange wave with the brand mark, navy outlines
with the offset shadow, and tinted table headers (the same pieces as the invoice PDF).

No external PDF library; drawing operators and the standard Helvetica fonts (see
``results_report`` for the primitives). Pure functions: the caller passes order, results and time.
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
    ORDER_STATUS,
    PAGE_H,
    PAGE_W,
    SEX,
    _clip,
    _number,
    _serialize,
    _wrap,
)
from medicalbooking.modules.payments.application.invoice_report import (
    HEADER_EDGE,
    INK,
    ISSUER_LINES,
    ISSUER_NAME,
    MARGIN,
    MUTED,
    NAVY,
    ORANGE,
    RULE,
    SHADOW,
    TINT,
    WAVE,
    WHITE,
    Sheet,
    brand_mark,
    masked_document,
)

CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_TOP = 58.0
# Lowest baseline of the body; the footer sits below it.
BOTTOM = 76.0
ROW_H = 24.0

ZEBRA = (0xF8, 0xFA, 0xFC)
HEAD_FILL = (0xEE, 0xF1, 0xF7)
SUCCESS = (0x15, 0x80, 0x3D)
WARNING = (0x92, 0x40, 0x0E)
DANGER = (0xB9, 0x1C, 0x1C)

# Interpretation label, text colour and the soft fill of its tag.
INTERPRETATION = {
    "BAJO": ("Bajo", WARNING, (0xFE, 0xF3, 0xC7)),
    "ALTO": ("Alto", DANGER, (0xFE, 0xE2, 0xE2)),
    "DENTRO_REFERENCIA": ("Dentro de rango", SUCCESS, (0xDC, 0xFC, 0xE7)),
    "NO_EVALUABLE": ("No evaluable", MUTED, (0xEE, 0xF1, 0xF7)),
}
OUT_OF_RANGE = frozenset({"BAJO", "ALTO"})
NOTE = (
    "Los valores de referencia dependen de la edad, el sexo y el método del laboratorio. "
    "Este informe apoya la consulta con su médico y no constituye un diagnóstico por sí mismo."
)
OBSERVATION_CHARS = 98
NOTE_CHARS = 112
EM_DASH = "\u2014"

# Table columns: exam, result, unit, reference values, interpretation tag.
COL_EXAM = MARGIN + 12
COL_RESULT = 238.0
COL_UNIT = 300.0
COL_REFERENCE = 350.0
COL_TAG = 458.0


def _birth(born, generated_at: datetime) -> str:
    if born is None:
        return EM_DASH
    years = (
        generated_at.year
        - born.year
        - ((generated_at.month, generated_at.day) < (born.month, born.day))
    )
    return f"{born:%d/%m/%Y} ({years} años)"


def _result_text(result: LabResult) -> str:
    if result.valor_numerico is not None:
        return _number(result.valor_numerico)
    return result.valor_texto or EM_DASH


def _reference_text(result: LabResult) -> str:
    low, high = result.referencia_min, result.referencia_max
    if low is None and high is None:
        return EM_DASH
    if low is not None and high is not None:
        return f"{_number(low)} \u2013 {_number(high)}"
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
        """A continuation page: the slim brand band and who the report is about. Returns y."""
        self.pages.append(Sheet())
        page = self.page
        page.box(0, PAGE_H - 14, PAGE_W, 14, fill=ORANGE)
        page.text(MARGIN, PAGE_H - 38, "Medilo", 11, bold=True, color=NAVY)
        page.text(
            MARGIN + 52,
            PAGE_H - 38,
            f"Resultados de laboratorio \u00b7 {_clip(self.parties.patient_name, 36)}"
            f" \u00b7 Orden {self.order_code}",
            9,
            color=MUTED,
        )
        page.path(
            (("m", MARGIN, PAGE_H - 48), ("l", PAGE_W - MARGIN, PAGE_H - 48)),
            stroke=RULE,
            width=0.6,
        )
        return PAGE_H - 72

    def render(self) -> bytes:
        total = len(self.pages)
        for number, page in enumerate(self.pages, start=1):
            _footer(page, number, total)
        return _serialize(self.pages)


def _footer(page: Sheet, number: int, total: int) -> None:
    page.path((("m", MARGIN, FOOTER_TOP), ("l", PAGE_W - MARGIN, FOOTER_TOP)), stroke=RULE)
    page.text(
        MARGIN,
        44,
        f"{ISSUER_NAME} \u00b7 {ISSUER_LINES[0]} \u00b7 {ISSUER_LINES[1].split(' \u00b7 ')[0]}",
        8,
        color=MUTED,
    )
    page.text(MARGIN, 32, "Documento generado por Medilo. Conserva este informe.", 8, color=MUTED)
    page.text(PAGE_W - MARGIN - 70, 44, f"Página {number} de {total}", 8, color=MUTED)


def _masthead(page: Sheet, order: LabOrder, generated_at: datetime) -> None:
    """The orange wave with the brand mark, the title and when the report was generated."""
    page.path(
        (("m", PAGE_W, PAGE_H), ("l", PAGE_W, HEADER_EDGE), *WAVE, ("l", 0, PAGE_H)),
        fill=ORANGE,
    )
    page.path((("m", PAGE_W, HEADER_EDGE), *WAVE), stroke=NAVY, width=1.2)
    brand_mark(page, MARGIN, 748, 26)
    page.text(MARGIN + 38, 752, "Medilo", 24, bold=True, color=NAVY)
    page.text(MARGIN + 114, 752, ".", 24, bold=True, color=WHITE)
    page.text(MARGIN + 38, 734, "Laboratorio clínico", 9, color=NAVY)

    local = generated_at.astimezone(ECUADOR)
    page.text(MARGIN, 656, "Resultados de laboratorio", 26, bold=True, color=NAVY)
    page.text(
        MARGIN,
        634,
        f"Orden {order.id[:8].upper()} \u00b7 generado el {local:%d/%m/%Y %H:%M} (hora de Ecuador)",
        10,
        color=INK,
    )


def _label(page: Sheet, x: float, y: float, text: str) -> None:
    page.text(x, y, text, 8.5, color=MUTED)


def _patient_card(
    page: Sheet, order: LabOrder, parties: ReportParties, generated_at: datetime, top: float
) -> float:
    """Patient and requesting doctor in a navy-outlined card with the offset shadow."""
    left, middle, right = MARGIN + 16, MARGIN + 186, MARGIN + 346
    height = 138.0
    bottom = top - height
    page.box(MARGIN + SHADOW, bottom - SHADOW, CONTENT_W, height, fill=NAVY)
    page.box(MARGIN, bottom, CONTENT_W, height, fill=WHITE, stroke=NAVY)

    _label(page, left, top - 22, "Paciente")
    page.text(left, top - 40, _clip(parties.patient_name, 40), 13, bold=True, color=NAVY)
    _label(page, right, top - 22, "Estado de la orden")
    status = ORDER_STATUS.get(order.estado, order.estado)
    page.text(right, top - 40, status, 10, bold=True, color=NAVY)

    _label(page, left, top - 64, "Documento")
    page.text(left, top - 78, masked_document(parties.patient_document), 10, color=INK)
    _label(page, middle, top - 64, "Fecha de nacimiento")
    page.text(middle, top - 78, _birth(parties.patient_birth_date, generated_at), 10, color=INK)
    _label(page, right, top - 64, "Sexo")
    page.text(right, top - 78, SEX.get(parties.patient_sex or "", EM_DASH), 10, color=INK)

    _label(page, left, top - 102, "Médico solicitante")
    page.text(left, top - 116, _clip(parties.doctor_name or EM_DASH, 32), 10, color=INK)
    _label(page, middle, top - 102, "Registro profesional")
    page.text(middle, top - 116, parties.doctor_registration or EM_DASH, 10, color=INK)
    _label(page, right, top - 102, "Prioridad")
    page.text(
        right, top - 116, "Urgente" if order.prioridad == "URGENTE" else "Normal", 10, color=INK
    )
    return bottom


def _section_bar(page: Sheet, y: float, title: str) -> float:
    """The test heading: a tinted bar with an orange stripe. Returns the y below it."""
    page.box(MARGIN, y - ROW_H, CONTENT_W, ROW_H, fill=TINT)
    page.box(MARGIN, y - ROW_H, 4, ROW_H, fill=ORANGE)
    page.text(MARGIN + 14, y - 16, _clip(title, 70), 11, bold=True, color=NAVY)
    return y - ROW_H


def _table_head(page: Sheet, y: float) -> float:
    page.box(MARGIN, y - 20, CONTENT_W, 20, fill=HEAD_FILL)
    for text, x in (
        ("EXAMEN", COL_EXAM),
        ("RESULTADO", COL_RESULT),
        ("UNIDAD", COL_UNIT),
        ("VALORES DE REFERENCIA", COL_REFERENCE),
        ("INTERPRETACIÓN", COL_TAG),
    ):
        page.text(x, y - 14, text, 7.5, bold=True, color=NAVY)
    return y - 20


def _row(page: Sheet, y: float, result: LabResult, *, shaded: bool) -> float:
    if shaded:
        page.box(MARGIN, y - ROW_H, CONTENT_W, ROW_H, fill=ZEBRA)
    label, tone, fill = INTERPRETATION.get(result.interpretacion or "", (EM_DASH, MUTED, None))
    flagged = result.interpretacion in OUT_OF_RANGE
    page.text(COL_EXAM, y - 16, _clip(result.nombre_analito, 34), 10, color=INK)
    page.text(
        COL_RESULT,
        y - 16,
        _clip(_result_text(result), 14),
        10.5,
        bold=True,
        color=tone if flagged else NAVY,
    )
    page.text(COL_UNIT, y - 16, _clip(result.unidad or "", 9), 9, color=MUTED)
    page.text(COL_REFERENCE, y - 16, _clip(_reference_text(result), 15), 10, color=INK)
    if fill is not None:
        page.box(COL_TAG, y - 19, PAGE_W - MARGIN - COL_TAG, 15, fill=fill)
        page.text(COL_TAG + 5, y - 15, _clip(label, 17), 8, bold=True, color=tone)
    else:
        page.text(COL_TAG + 5, y - 15, label, 8, color=MUTED)
    page.path((("m", MARGIN, y - ROW_H), ("l", PAGE_W - MARGIN, y - ROW_H)), stroke=RULE, width=0.4)
    return y - ROW_H


def _heading(detail: LabDetail, number: int) -> str:
    return f"Prueba {number} \u00b7 {detail.nombre}" if detail.nombre else f"Prueba {number}"


def _observations(report: _Report, order: LabOrder, y: float) -> float:
    """Observations wrap over as many lines as they need, never cut short."""
    if not order.observaciones:
        return y
    page = report.page
    page.text(MARGIN, y, "Observaciones", 10, bold=True, color=NAVY)
    y -= 15
    for line in _wrap(order.observaciones, OBSERVATION_CHARS):
        if y < BOTTOM:
            y = report.new_page()
            page = report.page
        page.text(MARGIN, y, line, 9.5, color=INK)
        y -= 13
    return y - 14


def _closing(report: _Report, y: float) -> None:
    """The clinical note and the two signature lines, on a new page when they do not fit."""
    note = _wrap(NOTE, NOTE_CHARS)
    needed = 24 + 12 * len(note) + 70
    if y - needed < BOTTOM:
        y = report.new_page()
    page = report.page
    y -= 8
    page.text(MARGIN, y, "Nota", 9, bold=True, color=NAVY)
    y -= 14
    for line in note:
        page.text(MARGIN, y, line, 8.5, color=MUTED)
        y -= 12
    y -= 42
    half = (CONTENT_W - 40) / 2
    for index, caption in enumerate(("Responsable del laboratorio", "Médico solicitante")):
        x = MARGIN + index * (half + 40)
        page.path((("m", x, y), ("l", x + half, y)), stroke=NAVY, width=0.8)
        page.text(x, y - 13, caption, 8.5, color=MUTED)


def build_results_report(
    order: LabOrder,
    results: list[LabResult],
    *,
    parties: ReportParties,
    generated_at: datetime,
    status_label: str | None = None,
) -> bytes:
    """One PDF for the order: the branded masthead, the patient card, then a table per test."""
    report = _Report(parties, order)
    page = report.page
    _masthead(page, order, generated_at)
    y = _patient_card(page, order, parties, generated_at, 604) - 30
    y = _observations(report, order, y)

    by_detail: dict[str, list[LabResult]] = {}
    for result in results:
        by_detail.setdefault(result.detalle_id, []).append(result)

    if not results:
        report.page.text(
            MARGIN, y, "Todavía no hay resultados validados para esta orden.", 11, color=MUTED
        )
        y -= 20

    for number, detail in enumerate(order.details, start=1):
        rows = by_detail.get(detail.id, [])
        if not rows:
            continue
        # The heading, the column titles and the first row stay together.
        if y - (ROW_H + 20 + ROW_H) < BOTTOM:
            y = report.new_page()
        page = report.page
        y = _table_head(page, _section_bar(page, y, _heading(detail, number)))
        for index, result in enumerate(rows):
            if y - ROW_H < BOTTOM:
                y = report.new_page()
                page = report.page
                y = _table_head(page, y)
            y = _row(page, y, result, shaded=index % 2 == 1)
        y -= 20

    _closing(report, y)
    return report.render()
