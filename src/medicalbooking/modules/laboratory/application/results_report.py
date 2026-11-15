"""The results report of a lab order as a PDF, styled after the patient portal.

No external PDF library: drawing operators and the standard Helvetica fonts (WinAnsi, so
Spanish accents render). Pure functions: the caller passes order, results and time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from medicalbooking.modules.laboratory.application.ports import LabOrder, LabResult, ReportParties

# Ecuador has no daylight saving: a fixed UTC-5 offset is the local time of the clinic.
ECUADOR = timezone(timedelta(hours=-5))
PAGE_W = 595.0
PAGE_H = 842.0
MARGIN = 44.0
BOTTOM = 70.0

# Brand tokens of the portal (lib/core/design/tokens.dart).
ORANGE = (0xC2, 0x41, 0x0C)
ORANGE_DEEP = (0x9A, 0x34, 0x12)
SLATE = (0x0F, 0x17, 0x2A)
MUTED = (0x65, 0x6B, 0x78)
LINE = (0xE2, 0xE8, 0xF0)
TINT = (0xFF, 0xF4, 0xED)
SUCCESS = (0x15, 0x80, 0x3D)
DANGER = (0xDC, 0x26, 0x26)
WARNING = (0xB4, 0x53, 0x09)
BLUE = (0x1D, 0x56, 0xA6)
WHITE = (1.0, 1.0, 1.0)

INTERPRETATION = {
    "BAJO": ("Bajo", WARNING),
    "ALTO": ("Alto", DANGER),
    "DENTRO_REFERENCIA": ("Dentro de rango", SUCCESS),
    "NO_EVALUABLE": ("No evaluable", MUTED),
}

SEX = {"F": "Femenino", "M": "Masculino"}
ORDER_LABEL = "Orden"


def _birth(born, generated_at: datetime) -> str:
    if born is None:
        return "—"
    years = (
        generated_at.year
        - born.year
        - ((generated_at.month, generated_at.day) < (born.month, born.day))
    )
    return f"{born:%d/%m/%Y} ({years} años)"


ORDER_STATUS = {
    "SOLICITADA": "Solicitada",
    "EN_PROCESO": "En proceso",
    "COMPLETADA": "Completada",
    "CANCELADA": "Cancelada",
}


def _number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _value(result: LabResult) -> str:
    if result.valor_numerico is not None:
        text = _number(result.valor_numerico)
        return f"{text} {result.unidad}".strip() if result.unidad else text
    return result.valor_texto or "—"


def _reference(result: LabResult) -> str:
    low, high = result.referencia_min, result.referencia_max
    if low is None and high is None:
        return "—"
    unit = f" {result.unidad}" if result.unidad else ""
    if low is not None and high is not None:
        return f"{_number(low)} – {_number(high)}{unit}"
    if low is not None:
        return f"≥ {_number(low)}{unit}"
    return f"≤ {_number(high)}{unit}"


def _escape(text: str) -> str:
    """A PDF literal string: WinAnsi bytes, delimiters escaped, unknown glyphs as '?'."""
    raw = text.encode("cp1252", errors="replace").decode("cp1252")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


OBSERVATION_CHARS = 98


def _wrap(text: str, width: int) -> list[str]:
    """Greedy word wrap at [width] characters; a longer word is broken, never dropped."""
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for word in paragraph.split():
            while len(word) > width:
                if current:
                    lines.append(current)
                    current = ""
                lines.append(word[:width])
                word = word[width:]
            candidate = f"{current} {word}" if current else word
            if len(candidate) <= width:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    return lines


def _clip(text: str, width_chars: int) -> str:
    return text if len(text) <= width_chars else text[: width_chars - 1] + "…"


@dataclass
class _Page:
    ops: list[str] = field(default_factory=list)

    def rect(self, x: float, y: float, w: float, h: float, color: tuple[float, ...]) -> None:
        r, g, b = _rgb(color)
        self.ops.append(f"{r} {g} {b} rg {x:.2f} {y:.2f} {w:.2f} {h:.2f} re f")

    def hline(
        self, x1: float, x2: float, y: float, color: tuple[float, ...], width: float = 0.6
    ) -> None:
        r, g, b = _rgb(color)
        self.ops.append(f"{r} {g} {b} RG {width} w {x1:.2f} {y:.2f} m {x2:.2f} {y:.2f} l S")

    def text(
        self,
        x: float,
        y: float,
        value: str,
        size: float = 10.0,
        *,
        bold: bool = False,
        color: tuple[float, ...] = SLATE,
    ) -> None:
        r, g, b = _rgb(color)
        font = "F2" if bold else "F1"
        self.ops.append(
            f"BT {r} {g} {b} rg /{font} {size:.1f} Tf {x:.2f} {y:.2f} Td ({_escape(value)}) Tj ET"
        )


def _rgb(color: tuple[float, ...]) -> tuple[str, str, str]:
    if all(isinstance(c, float) and c <= 1.0 for c in color):
        parts = color
    else:
        parts = tuple(c / 255 for c in color)
    return tuple(f"{p:.3f}" for p in parts)  # type: ignore[return-value]


class _Document:
    def __init__(self) -> None:
        self.pages: list[_Page] = [_Page()]

    @property
    def page(self) -> _Page:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append(_Page())

    def render(self) -> bytes:
        total = len(self.pages)
        for number, page in enumerate(self.pages, start=1):
            _footer(page, number, total)
        return _serialize(self.pages)


def _footer(page: _Page, number: int, total: int) -> None:
    page.hline(MARGIN, PAGE_W - MARGIN, 52, LINE)
    page.text(
        MARGIN,
        36,
        "Medilo · Laboratorio clínico · Documento generado desde el portal",
        8,
        color=MUTED,
    )
    page.text(PAGE_W - MARGIN - 70, 36, f"Página {number} de {total}", 8, color=MUTED)


def _serialize(pages: list[_Page]) -> bytes:
    """Assemble the PDF: catalog, page tree, two base-14 fonts, one page and stream per page."""
    objects: list[bytes] = []

    def add(body: str | bytes) -> int:
        objects.append(body.encode("latin-1") if isinstance(body, str) else body)
        return len(objects)

    catalog = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add("")  # filled in below, once the page ids are known
    font_regular = add(
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>"
    )
    font_bold = add(
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"
    )
    resources = f"<< /Font << /F1 {font_regular} 0 R /F2 {font_bold} 0 R >> >>"

    page_ids: list[int] = []
    for page in pages:
        content = "\n".join(page.ops).encode("cp1252", errors="replace")
        stream_id = add(
            b"<< /Length "
            + str(len(content)).encode()
            + b" >>\nstream\n"
            + content
            + b"\nendstream"
        )
        page_ids.append(
            add(
                f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_W:.0f} {PAGE_H:.0f}] "
                f"/Resources {resources} /Contents {stream_id} 0 R >>"
            )
        )
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode(
        "latin-1"
    )

    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode("latin-1") + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("latin-1")
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def build_results_report(
    order: LabOrder,
    results: list[LabResult],
    *,
    parties: ReportParties,
    generated_at: datetime,
    status_label: str | None = None,
) -> bytes:
    """One PDF for the order: a branded header, then one table per test with its results."""
    document = _Document()
    page = document.page

    local = generated_at.astimezone(ECUADOR)
    # Header band.
    page.rect(0, PAGE_H - 132, PAGE_W, 132, ORANGE)
    page.rect(0, PAGE_H - 140, PAGE_W, 8, ORANGE_DEEP)
    page.text(MARGIN, PAGE_H - 52, "MEDILO", 11, bold=True, color=WHITE)
    page.text(MARGIN, PAGE_H - 86, "Resultados de laboratorio", 24, bold=True, color=WHITE)
    page.text(
        MARGIN,
        PAGE_H - 108,
        f"Orden {order.id[:8].upper()} · generado el {local:%d/%m/%Y %H:%M} (hora de Ecuador)",
        10,
        color=WHITE,
    )

    # Patient and requesting doctor, as on a real lab report.
    y = PAGE_H - 170
    card_h = 122
    page.rect(MARGIN, y - card_h + 12, PAGE_W - 2 * MARGIN, card_h, TINT)
    left, right = MARGIN + 16, MARGIN + 300
    page.text(left, y - 4, "PACIENTE", 8, bold=True, color=MUTED)
    page.text(left, y - 22, _clip(parties.patient_name, 40), 13, bold=True, color=SLATE)
    rows = [
        ("Documento", parties.patient_document or "—"),
        ("Fecha de nacimiento", _birth(parties.patient_birth_date, generated_at)),
        ("Sexo", SEX.get(parties.patient_sex or "", "—")),
        ("Correo", parties.patient_email or "—"),
        ("Teléfono", parties.patient_phone or "—"),
    ]
    row_y = y - 40
    for label, value in rows:
        page.text(left, row_y, label, 8, bold=True, color=MUTED)
        page.text(left + 110, row_y, _clip(value, 30), 9, color=SLATE)
        row_y -= 15

    page.text(right, y - 4, "MÉDICO SOLICITANTE", 8, bold=True, color=MUTED)
    page.text(right, y - 22, _clip(parties.doctor_name or "—", 30), 13, bold=True, color=SLATE)
    if parties.doctor_registration:
        page.text(
            right, y - 40, f"Registro profesional {parties.doctor_registration}", 9, color=MUTED
        )

    page.text(right, y - 74, "ORDEN", 8, bold=True, color=MUTED)
    status = status_label or ORDER_STATUS.get(order.estado, order.estado)
    page.text(right, y - 90, f"{ORDER_LABEL} {order.id[:8].upper()} · {status}", 9, color=SLATE)
    prioridad = "Urgente" if order.prioridad == "URGENTE" else "Normal"
    page.text(right, y - 104, f"Prioridad {prioridad.lower()}", 9, color=MUTED)
    y -= card_h + 22

    # Observations wrap over as many lines as they need, never cut short.
    if order.observaciones:
        page.text(MARGIN, y, "OBSERVACIONES", 8, bold=True, color=MUTED)
        y -= 14
        for line in _wrap(order.observaciones, OBSERVATION_CHARS):
            if y < BOTTOM:
                document.new_page()
                page = document.page
                y = PAGE_H - 60
            page.text(MARGIN, y, line, 9.5, color=SLATE)
            y -= 13
        y -= 16

    by_detail: dict[str, list[LabResult]] = {}
    for result in results:
        by_detail.setdefault(result.detalle_id, []).append(result)

    columns = (MARGIN, 250.0, 362.0, 470.0)
    if not results:
        page.text(
            MARGIN, y, "Todavía no hay resultados validados para esta orden.", 11, color=MUTED
        )

    for number, detail in enumerate(order.details, start=1):
        rows = by_detail.get(detail.id, [])
        if not rows:
            continue
        needed = 46 + 24 * len(rows)
        if y - needed < BOTTOM:
            document.new_page()
            page = document.page
            y = PAGE_H - 60
        page.text(MARGIN, y, f"Prueba {number}", 13, bold=True, color=ORANGE)
        y -= 16
        page.hline(MARGIN, PAGE_W - MARGIN, y, ORANGE, 1.2)
        y -= 14
        for label, x in zip(("ANALITO", "RESULTADO", "REFERENCIA", "ESTADO"), columns, strict=True):
            page.text(x, y, label, 8, bold=True, color=MUTED)
        y -= 8
        page.hline(MARGIN, PAGE_W - MARGIN, y, LINE)
        y -= 16
        for result in rows:
            page.text(columns[0], y, _clip(result.nombre_analito, 34), 10, color=SLATE)
            page.text(columns[1], y, _clip(_value(result), 22), 10, bold=True, color=SLATE)
            page.text(columns[2], y, _clip(_reference(result), 20), 10, color=MUTED)
            label, tone = INTERPRETATION.get(result.interpretacion or "", ("—", MUTED))
            page.text(columns[3], y, label, 10, bold=True, color=tone)
            y -= 8
            page.hline(MARGIN, PAGE_W - MARGIN, y, LINE, 0.4)
            y -= 16
        y -= 14

    return document.render()
