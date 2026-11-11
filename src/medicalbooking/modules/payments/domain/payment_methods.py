"""Saved payment method rules: the permission and the shape of a Stripe PaymentMethod id.

Pure functions and constants. Ownership is checked in the application layer.
"""

from __future__ import annotations

import re

PERM_PAYMENT_METHODS = "metodos_pago.gestionar"

# Stripe ids look like pm_1Q0PsIJvEtkwdCNYMSaVuRz6. Anything else is treated as a missing card
# before Stripe is asked, so path characters never reach the provider URL.
_PAYMENT_METHOD_ID = re.compile(r"^pm_[A-Za-z0-9_]{1,100}$")


def is_payment_method_id(value: str) -> bool:
    return _PAYMENT_METHOD_ID.fullmatch(value) is not None
