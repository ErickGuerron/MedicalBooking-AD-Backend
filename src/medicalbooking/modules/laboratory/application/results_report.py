"""The results report of a lab order as a PDF, styled after the patient portal.

No external PDF library: drawing operators and the standard Helvetica fonts (WinAnsi, so
Spanish accents render). Pure functions: the caller passes order, results and time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta, timezone

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


ORDER_STATUS = {
    "SOLICITADA": "Solicitada",
    "EN_PROCESO": "En proceso",
    "COMPLETADA": "Completada",
    "CANCELADA": "Cancelada",
}


def _number(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _escape(text: str) -> str:
    """A PDF literal string: WinAnsi bytes, delimiters escaped, unknown glyphs as '?'."""
    raw = text.encode("cp1252", errors="replace").decode("cp1252")
    return raw.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


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
