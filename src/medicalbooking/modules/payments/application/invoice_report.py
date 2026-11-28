"""The patient's invoice as a PDF, drawn in the Medilo visual language.

Built at send time from the paid invoice and never stored. The page carries the brand's
orange wave, the navy outline and offset shadow of the emails, and a "Pagada" stamp once the
payment is confirmed. The document number is masked the same way as in the results report:
only the last four digits of the patient's document appear.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from medicalbooking.modules.laboratory.application.results_report import (
    ECUADOR,
    PAGE_H,
    PAGE_W,
    _clip,
    _escape,
    _Page,
    _serialize,
)

NAVY = (0x16, 0x20, 0x6B)
INK = (0x2B, 0x34, 0x6B)
MUTED = (0x4A, 0x52, 0x80)
ORANGE = (0xFF, 0x7A, 0x1A)
TINT = (0xFF, 0xF1, 0xE4)
RULE = (0xC7, 0xCC, 0xE0)
WHITE = (1.0, 1.0, 1.0)

MARGIN = 44.0
CONTENT_W = PAGE_W - 2 * MARGIN
FOOTER_TOP = 58.0
TABLE_FLOOR = 150.0
SHADOW = 5.0

# Where the orange header ends. It is a slim band at the top, kept low for a formal page.
HEADER_EDGE = 708.0

# The issuer's details, the same ones printed in the email footers. Hardcoded because the system
# has no record of the company: change them here and in the email templates together.
ISSUER_NAME = "Medilo Inc."
ISSUER_LINES = (
    "Av. Los Andes y Mera, Ambato, Tungurahua",
    "Teléfono (03) 282-4567 · lunes a viernes, 8:00 a 17:00",
    "Permiso de funcionamiento N.º MED-PRUEBA-0001",
)

# The wave under the header, drawn as gentle curves from the right edge to the left one.
WAVE = (
    ("c", 540, 716, 500, 702, 450, 708),
    ("c", 400, 714, 380, 726, 326, 718),
    ("c", 272, 710, 240, 698, 186, 706),
    ("c", 132, 714, 104, 724, 56, 716),
    ("c", 30, 710, 14, 706, 0, 710),
)


@dataclass(frozen=True)
class InvoiceLine:
    descripcion: str
    cantidad: int
    precio_unitario: Decimal
    subtotal: Decimal


@dataclass(frozen=True)
class InvoiceDocument:
    numero_factura: str
    emitida_en: datetime
    pagada_en: datetime | None
    paciente_nombre: str
    documento: str | None
    moneda: str
    lineas: tuple[InvoiceLine, ...]
    subtotal: Decimal
    impuestos: Decimal
    total: Decimal
    # Contact data of the patient. Each one is printed only when it is known.
    paciente_telefono: str | None = None
    paciente_email: str | None = None
    paciente_direccion: str | None = None


def masked_document(value: str | None) -> str:
    """Document type and the last four digits only; the full number never reaches the PDF."""
    if not value:
        return "No registrado"
    kind, _, number = value.rpartition(" ")
    if not number:
        return "******"
    return f"{kind} ******{number[-4:]}".strip()


def _money(amount: Decimal, currency: str) -> str:
    return f"{amount:.2f} {currency}"


class _Sheet(_Page):
    """A page with the vector shapes the invoice needs on top of the base text operators."""

    def path(
        self,
        segments: tuple[tuple[object, ...], ...],
        *,
        fill: tuple[float, ...] | None = None,
        stroke: tuple[float, ...] | None = None,
        width: float = 1.0,
    ) -> None:
        parts: list[str] = []
        for segment in segments:
            kind, *values = segment
            if kind == "m" or kind == "l":
                parts.append(f"{values[0]:.2f} {values[1]:.2f} {kind}")
            else:
                parts.append(" ".join(f"{float(v):.2f}" for v in values) + " c")
        if fill is not None:
            r, g, b = _rgb_parts(fill)
            self.ops.append(f"{r} {g} {b} rg")
        if stroke is not None:
            r, g, b = _rgb_parts(stroke)
            self.ops.append(f"{r} {g} {b} RG {width:.2f} w")
        self.ops.append(" ".join(parts))
        if fill is not None and stroke is not None:
            self.ops.append("h b")
        elif fill is not None:
            self.ops.append("h f")
        else:
            self.ops.append("S")

    def box(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: tuple[float, ...] | None = None,
        stroke: tuple[float, ...] | None = None,
        width: float = 1.4,
    ) -> None:
        self.path(
            (("m", x, y), ("l", x + w, y), ("l", x + w, y + h), ("l", x, y + h)),
            fill=fill,
            stroke=stroke,
            width=width,
        )

    def rotated_text(
        self,
        x: float,
        y: float,
        angle: float,
        value: str,
        size: float,
        *,
        color: tuple[float, ...],
        bold: bool = False,
    ) -> None:
        radians = math.radians(angle)
        cos, sin = math.cos(radians), math.sin(radians)
        r, g, b = _rgb_parts(color)
        font = "F2" if bold else "F1"
        self.ops.append(
            f"q {cos:.4f} {sin:.4f} {-sin:.4f} {cos:.4f} {x:.2f} {y:.2f} cm "
            f"BT {r} {g} {b} rg /{font} {size:.1f} Tf 0 0 Td ({_escape(value)}) Tj ET Q"
        )


def _rgb_parts(color: tuple[float, ...]) -> tuple[str, str, str]:
    if all(isinstance(c, float) and c <= 1.0 for c in color):
        parts = color
    else:
        parts = tuple(c / 255 for c in color)
    return tuple(f"{p:.3f}" for p in parts)  # type: ignore[return-value]


class _InvoiceDocument:
    def __init__(self) -> None:
        self.pages: list[_Sheet] = [_Sheet()]

    @property
    def page(self) -> _Sheet:
        return self.pages[-1]

    def new_page(self) -> None:
        self.pages.append(_Sheet())
        self.page.box(0, PAGE_H - 14, PAGE_W, 14, fill=ORANGE)

    def render(self) -> bytes:
        total = len(self.pages)
        for number, page in enumerate(self.pages, start=1):
            _footer(page, number, total)
        return _serialize(self.pages)


def _footer(page: _Sheet, number: int, total: int) -> None:
    page.path((("m", MARGIN, FOOTER_TOP), ("l", PAGE_W - MARGIN, FOOTER_TOP)), stroke=RULE)
    page.text(
        MARGIN,
        44,
        f"{ISSUER_NAME} · © 2026 Todos los derechos reservados",
        8,
        color=MUTED,
    )
    page.text(
        MARGIN,
        32,
        "Documento generado por Medilo. Conserva este comprobante.",
        8,
        color=MUTED,
    )
    page.text(PAGE_W - MARGIN - 70, 44, f"Página {number} de {total}", 8, color=MUTED)


def _masthead(page: _Sheet, invoice: InvoiceDocument) -> None:
    """The orange wave with the wordmark, the invoice title and its number."""
    page.path(
        (("m", PAGE_W, PAGE_H), ("l", PAGE_W, HEADER_EDGE), *WAVE, ("l", 0, PAGE_H)),
        fill=ORANGE,
    )
    page.path((("m", PAGE_W, HEADER_EDGE), *WAVE), stroke=NAVY, width=1.2)
    _brand_mark(page, MARGIN, 748, 26)
    page.text(MARGIN + 38, 752, "Medilo", 24, bold=True, color=NAVY)
    page.text(MARGIN + 114, 752, ".", 24, bold=True, color=WHITE)
    page.text(MARGIN + 38, 734, "Plataforma oficial de gestión médica", 9, color=NAVY)

    local = invoice.emitida_en.astimezone(ECUADOR)
    page.text(PAGE_W - MARGIN - 150, 752, f"Emitida {local:%d/%m/%Y %H:%M}", 9, color=NAVY)

    page.text(MARGIN, 640, "Factura", 30, bold=True, color=NAVY)
    page.text(MARGIN, 616, f"Número {invoice.numero_factura}", 12, color=INK)


def _brand_mark(page: _Sheet, x: float, y: float, size: float) -> None:
    """The Medilo mark of the emails: a navy cross in a navy-framed square, with an orange shadow.

    Drawn from the logo's proportions (a 92-unit grid) so it keeps its shape at any size.
    `x, y` is the lower-left corner of the grid; the mark is about 86 units wide.
    """
    unit = size / 92
    frame = 72 * unit
    # On the orange header the mark sits on a white tile, as the logo does on the emails, so its
    # orange shadow stays visible.
    page.box(x, y + 4 * unit, 86 * unit, 84 * unit, fill=WHITE)
    # The orange shadow sits offset to the lower right, behind the framed square.
    page.box(x + 18 * unit, y + 8 * unit, 68 * unit, 68 * unit, fill=ORANGE)
    page.box(x + 4 * unit, y + 16 * unit, frame, frame, fill=NAVY)
    page.box(x + 8.5 * unit, y + 20.5 * unit, 63 * unit, 63 * unit, fill=WHITE)
    cx, cy = x + 40 * unit, y + 52 * unit
    long_arm, half_thick = 20 * unit, 5 * unit
    page.path(
        (
            ("m", cx - half_thick, cy - long_arm),
            ("l", cx + half_thick, cy - long_arm),
            ("l", cx + half_thick, cy - half_thick),
            ("l", cx + long_arm, cy - half_thick),
            ("l", cx + long_arm, cy + half_thick),
            ("l", cx + half_thick, cy + half_thick),
            ("l", cx + half_thick, cy + long_arm),
            ("l", cx - half_thick, cy + long_arm),
            ("l", cx - half_thick, cy + half_thick),
            ("l", cx - long_arm, cy + half_thick),
            ("l", cx - long_arm, cy - half_thick),
            ("l", cx - half_thick, cy - half_thick),
        ),
        fill=NAVY,
    )


def _issuer(page: _Sheet) -> None:
    """The company's name and contact details, top right, above the patient card."""
    x = 330.0
    page.text(x, 640, ISSUER_NAME, 11, bold=True, color=NAVY)
    for index, line in enumerate(ISSUER_LINES):
        page.text(x, 624 - index * 13, line, 8.5, color=INK)


def _label(page: _Sheet, x: float, y: float, text: str) -> None:
    page.text(x, y, text, 8.5, color=MUTED)


def _details(page: _Sheet, invoice: InvoiceDocument, top: float) -> float:
    """Patient card in three columns: a small label above each value.

    The address gets its own row, and only when the record has one. The stamp shows once the
    payment is confirmed.
    """
    left, middle, right = MARGIN + 16, MARGIN + 186, MARGIN + 346
    has_address = bool(invoice.paciente_direccion)
    height = 136.0 if has_address else 116.0
    bottom = top - height
    page.box(MARGIN + SHADOW, bottom - SHADOW, CONTENT_W, height, fill=NAVY)
    page.box(MARGIN, bottom, CONTENT_W, height, fill=WHITE, stroke=NAVY)

    # First row: the name, and the payment date when it is confirmed.
    _label(page, left, top - 22, "Paciente")
    page.text(left, top - 40, _clip(invoice.paciente_nombre, 40), 13, bold=True, color=NAVY)
    if invoice.pagada_en is not None:
        paid = invoice.pagada_en.astimezone(ECUADOR)
        _label(page, right, top - 22, "Fecha de pago")
        page.text(right, top - 40, f"{paid:%d/%m/%Y %H:%M}", 10, bold=True, color=NAVY)

    # Second row: document, phone and email, each with its label.
    _label(page, left, top - 62, "Documento")
    page.text(left, top - 76, masked_document(invoice.documento), 10, color=INK)
    _label(page, middle, top - 62, "Teléfono")
    page.text(middle, top - 76, invoice.paciente_telefono or "No registrado", 10, color=INK)
    _label(page, right, top - 62, "Correo")
    page.text(right, top - 76, _clip(invoice.paciente_email or "No registrado", 28), 10, color=INK)

    # Third row, only when the address is known.
    if has_address:
        _label(page, left, top - 96, "Dirección")
        page.text(left, top - 110, _clip(invoice.paciente_direccion or "", 60), 10, color=INK)

    if invoice.pagada_en is not None:
        page.rotated_text(420, bottom + 14, -8, "PAGADA", 18, color=ORANGE, bold=True)
    return bottom


def _table_header(page: _Sheet, top: float) -> None:
    page.box(MARGIN, top - 24, CONTENT_W, 24, fill=TINT)
    page.text(MARGIN + 12, top - 16, "Descripción", 9, bold=True, color=NAVY)
    page.text(330, top - 16, "Cant.", 9, bold=True, color=NAVY)
    page.text(392, top - 16, "Precio", 9, bold=True, color=NAVY)
    page.text(470, top - 16, "Subtotal", 9, bold=True, color=NAVY)


def _lines(document: _InvoiceDocument, invoice: InvoiceDocument, top: float) -> float:
    """The detail lines. A longer invoice continues on a new page with the header repeated."""
    page = document.page
    _table_header(page, top)
    y = top - 24
    for line in invoice.lineas:
        if y - 24 < TABLE_FLOOR:
            document.new_page()
            page = document.page
            y = PAGE_H - 60
            _table_header(page, y)
            y -= 24
        page.text(MARGIN + 12, y - 16, _clip(line.descripcion, 52), 10, color=NAVY)
        page.text(330, y - 16, str(line.cantidad), 10, color=NAVY)
        page.text(392, y - 16, _money(line.precio_unitario, invoice.moneda), 10, color=NAVY)
        page.text(470, y - 16, _money(line.subtotal, invoice.moneda), 10, color=NAVY)
        page.path((("m", MARGIN, y - 24), ("l", PAGE_W - MARGIN, y - 24)), stroke=RULE, width=0.6)
        y -= 24
    return y


def _totals(document: _InvoiceDocument, invoice: InvoiceDocument, top: float) -> None:
    height = 84.0
    if top - height - 16 < TABLE_FLOOR:
        document.new_page()
        top = PAGE_H - 60
    page = document.page
    x, width = 330.0, PAGE_W - MARGIN - 330.0
    top -= 16
    bottom = top - height
    page.box(x + SHADOW, bottom - SHADOW, width, height, fill=NAVY)
    page.box(x, bottom, width, height, fill=WHITE, stroke=NAVY)
    rows = (
        ("Subtotal", invoice.subtotal),
        ("Impuestos", invoice.impuestos),
    )
    for index, (label, amount) in enumerate(rows):
        baseline = top - 24 - index * 20
        page.text(x + 12, baseline, label, 10, color=INK)
        page.text(x + 120, baseline, _money(amount, invoice.moneda), 10, color=INK)
    page.box(x + 1, bottom + 1, width - 2, 26, fill=ORANGE)
    page.text(x + 12, bottom + 9, "Total", 12, bold=True, color=NAVY)
    page.text(x + 120, bottom + 9, _money(invoice.total, invoice.moneda), 12, bold=True, color=NAVY)


def build_invoice_pdf(invoice: InvoiceDocument) -> bytes:
    document = _InvoiceDocument()
    page = document.page
    page.box(0, 0, 12, HEADER_EDGE, fill=ORANGE)
    page.path((("m", 12, 0), ("l", 12, HEADER_EDGE)), stroke=NAVY, width=1.0)
    _masthead(page, invoice)
    _issuer(page)
    top = _details(page, invoice, 580.0)
    end = _lines(document, invoice, top - 20)
    _totals(document, invoice, end)
    return document.render()


# Public names of the brand drawing pieces, shared with the laboratory results report.
Sheet = _Sheet
brand_mark = _brand_mark
