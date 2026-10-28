import numpy as np

from mfgsched.config import GeneratorConfig
from mfgsched.ids import (
    machine_id, operation_id, order_id, product_id, route_id, schedule_id,
)


def test_full_volumes_match_requirements():
    c = GeneratorConfig.full()
    assert (c.n_skus, c.n_machines, c.n_orders, c.n_operations, c.n_materials) \
        == (100, 40, 500, 2000, 200)
    assert c.seed == 20261022


def test_starter_is_smaller_but_same_seed():
    s, f = GeneratorConfig.starter(), GeneratorConfig.full()
    assert s.n_orders < f.n_orders
    assert s.seed == f.seed


def test_id_formats():
    assert product_id(1) == "PROD-2026-000001"
    assert machine_id(7) == "MACH-007"
    assert schedule_id(1) == "SCH-2026-000001"
    assert route_id("SKU-0001") == "ROUTE-SKU-0001-01"
    assert operation_id(order_id(5), 2) == "OP-ORD-2026-000005-02"


def test_seeded_rng_is_reproducible():
    seed = GeneratorConfig.full().seed
    a = np.random.default_rng(seed).random(10)
    b = np.random.default_rng(seed).random(10)
    assert (a == b).all()