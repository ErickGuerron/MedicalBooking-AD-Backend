from __future__ import annotations

from medicalbooking.modules.notifications.infrastructure.email_render import payment_content


def test_payment_email_greets_the_patient_and_names_the_invoice() -> None:
    content = payment_content("Ana", "FAC-000123", "25.00 USD", "https://assets.example")

    assert content.subject == "Recibimos tu pago en Medilo"
    assert "Hola Ana" in content.text
    assert "FAC-000123" in content.text
    assert "25.00 USD" in content.text
    assert content.html is not None
    assert "FAC-000123" in content.html
    assert "25.00 USD" in content.html


def test_payment_email_escapes_the_patient_name_in_html() -> None:
    content = payment_content("<script>x</script>", "FAC-1", "1.00 USD", "https://assets.example")

    assert content.html is not None
    assert "<script>x</script>" not in content.html
    assert "&lt;script&gt;" in content.html
