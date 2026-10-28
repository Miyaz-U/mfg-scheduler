"""Central configuration: seed, volumes, and planning horizon.

Every generator will read from this one object, so the starter and full
datasets differ only in numbers, never in logic.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date


@dataclass(frozen=True)
class GeneratorConfig:
    # Reproducibility: the same seed always gives the same data.
    seed: int = 20261022

    # Used inside IDs, e.g. PROD-2026-000001
    year: int = 2026

    # Required volumes (full dataset)
    n_skus: int = 100
    n_machines: int = 40
    n_orders: int = 500
    n_operations: int = 2000
    n_materials: int = 200

    # Planning horizon: when the schedule starts and how long it runs
    horizon_start: date = date(2026, 11, 2)
    horizon_days: int = 28
    # Longest allowed standard operation time; keeps every operation inside
    # one shift (decision D2).
    max_operation_minutes: int = 240

    # Edge cases injected per type in Step 7 (and as many valid boundary cases per type)
    n_anomalies_per_type: int = 10

    # Where generated files go
    output_dir: str = "data/full"

    @classmethod
    def full(cls) -> "GeneratorConfig":
        """The required volumes: 100 SKUs, 40 machines, 500 orders, ..."""
        return cls()

    @classmethod
    def starter(cls) -> "GeneratorConfig":
        """A small dataset (about 10%) for quick tests and debugging."""
        return replace(
            cls(),
            n_skus=10,
            n_machines=8,
            n_orders=50,
            n_operations=200,
            n_materials=20,
            horizon_days=14,
            output_dir="data/starter",
            n_anomalies_per_type=2,
        )