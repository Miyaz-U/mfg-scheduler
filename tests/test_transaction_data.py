from dataclasses import replace

import pandas as pd
import pytest

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import read_table, write_tables
from mfgsched.generators.transaction_data import generate_all
from mfgsched.validation.master_checks import check_master_rules
from mfgsched.validation.schema_checks import check_all, check_table
from mfgsched.validation.transaction_checks import check_transaction_rules

NEW_TABLES = ["MachineCalendar", "InventoryLot", "ProductionOrder", "Operation",
              "MaterialRequirement", "OrderStatusEvent"]


@pytest.fixture(scope="module")
def full():
    return generate_all(GeneratorConfig.full())


def test_new_id_builders():
    assert ids.calendar_id("MACH-001", "2026-11-02", "A") == "CAL-MACH-001-20261102-A"
    assert ids.requirement_id(5) == "MREQ-2026-000005"
    assert ids.order_event_id(12) == "OEV-2026-000012"


def test_volumes(full):
    assert len(full["ProductionOrder"]) == 500
    assert len(full["Operation"]) == 2000
    assert len(full["Material"]) == 200
    assert len(full["Machine"]) == 40
    assert len(full["OrderStatusEvent"]) == 500
    assert len(full["MachineCalendar"]) > 1500


def test_exact_operation_count_and_duration_cap(full):
    ops = full["Operation"]
    assert len(ops) == 2000
    assert ops["standard_minutes"].max() <= GeneratorConfig.full().max_operation_minutes


def test_calendar_shape(full):
    cal = full["MachineCalendar"].merge(
        full["Machine"][["machine_id", "lifecycle_state", "operators_required"]], on="machine_id")
    assert (pd.to_datetime(cal["calendar_date"]).dt.weekday != 6).all()      # no Sundays
    assert set(cal["shift_code"]) == {"A", "B", "C"}
    assert (cal.loc[cal["lifecycle_state"] == "MAINTENANCE", "available_minutes"] == 0).all()
    normal = cal[cal["lifecycle_state"] == "ACTIVE"]
    assert (normal["downtime_reason"] == "BREAKDOWN").any()
    assert (normal["operators_assigned"] < normal["operators_required"]).any()


def test_order_mix(full):
    orders = full["ProductionOrder"]
    assert set(orders["priority"]) == {1, 2, 3, 4}
    assert 20 <= int(orders["is_urgent"].sum()) <= 70
    obsolete = full["Product"].loc[full["Product"]["lifecycle_state"] == "OBSOLETE", "product_id"]
    assert not orders["product_id"].isin(obsolete).any()


def test_requirements_allocated_and_every_material_has_stock(full):
    assert full["MaterialRequirement"]["allocated_lot_id"].notna().all()
    assert full["InventoryLot"]["material_id"].nunique() == 200


def test_delayed_and_in_transit_lots_exist(full):
    lots = full["InventoryLot"]
    assert lots["is_delayed"].sum() > 0
    assert (lots["lot_status"] == "IN_TRANSIT").sum() > 0


def test_schema_conformance_full(full):
    for name, df in full.items():
        assert check_table(df, name) == [], name


def test_foreign_keys_full(full):
    assert [p for p in check_all(full) if "orphan" in p] == []


def test_business_rules_full(full):
    cfg = GeneratorConfig.full()
    assert check_master_rules(full) == []
    assert check_transaction_rules(full, cfg) == []


def test_reproducible_with_same_seed(full):
    again = generate_all(GeneratorConfig.full())
    for name in full:
        pd.testing.assert_frame_equal(full[name], again[name])


def test_different_seed_changes_orders(full):
    other = generate_all(replace(GeneratorConfig.full(), seed=1))
    assert not full["ProductionOrder"]["quantity"].equals(other["ProductionOrder"]["quantity"])


def test_starter_scale_is_valid():
    cfg = GeneratorConfig.starter()
    tables = generate_all(cfg)
    assert len(tables["ProductionOrder"]) == 50 and len(tables["Operation"]) == 200
    assert check_all(tables) == []
    assert check_master_rules(tables) == []
    assert check_transaction_rules(tables, cfg) == []


def test_csv_roundtrip_new_tables(full, tmp_path):
    write_tables(full, tmp_path)
    for name in NEW_TABLES:
        assert check_table(read_table(tmp_path, name), name) == [], name