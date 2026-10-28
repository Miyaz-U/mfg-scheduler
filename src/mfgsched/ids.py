"""Deterministic ID builders.

Rules: counters are zero-padded, IDs never change once created, and every
file refers to the exact same string. Patterns from the problem statement:
  PROD-2026-000001, ROUTE-<sku>-01, OP-<order-id>-01, MACH-001, SCH-2026-000001
"""


def product_id(n: int, year: int = 2026) -> str:
    return f"PROD-{year}-{n:06d}"


def route_id(sku: str, version: int = 1) -> str:
    return f"ROUTE-{sku}-{version:02d}"


def operation_id(order_id: str, seq: int) -> str:
    return f"OP-{order_id}-{seq:02d}"


def machine_id(n: int) -> str:
    return f"MACH-{n:03d}"


def schedule_id(n: int, year: int = 2026) -> str:
    return f"SCH-{year}-{n:06d}"


# Not specified in the images; we follow the same style.
def order_id(n: int, year: int = 2026) -> str:
    return f"ORD-{year}-{n:06d}"


def material_id(n: int) -> str:
    return f"MAT-{n:04d}"


def lot_id(n: int, year: int = 2026) -> str:
    return f"LOT-{year}-{n:06d}"

def sku_code(n: int) -> str:
    return f"SKU-{n:04d}"

def calendar_id(machine_id: str, day, shift: str) -> str:
    """CAL-MACH-001-20261102-A. `day` may be a date or 'YYYY-MM-DD'."""
    return f"CAL-{machine_id}-{str(day).replace('-', '')}-{shift}"


def requirement_id(n: int, year: int = 2026) -> str:
    return f"MREQ-{year}-{n:06d}"


def order_event_id(n: int, year: int = 2026) -> str:
    return f"OEV-{year}-{n:06d}"

def schedule_event_id(n: int, year: int = 2026) -> str:
    return f"SEV-{year}-{n:06d}"


def violation_id(n: int, year: int = 2026) -> str:
    return f"VIO-{year}-{n:06d}"

def ground_truth_id(n: int, year: int = 2026) -> str:
    return f"GT-{year}-{n:06d}"