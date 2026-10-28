from dataclasses import replace

import pandas as pd
import pytest

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import read_table, snake, write_tables
from mfgsched.generators.master_data import generate_master_data
from mfgsched.validation.master_checks import check_master_rules
from mfgsched.validation.schema_checks import check_all, check_table


@pytest.fixture(scope="module")
def full():
    return generate_master_data(GeneratorConfig.full())


def test_sku_code():
    assert ids.sku_code(7) == "SKU-0007"


def test_full_volumes(full):
    assert len(full["Product"]) == 100
    assert len(full["Routing"]) == 100
    assert len(full["Machine"]) == 40
    assert len(full["Material"]) == 200
    assert len(full["ChangeoverMatrix"]) == 7 * 6 * 6
    assert len(full["RoutingStepMachine"]) >= len(full["RoutingStep"])
    assert len(full["RoutingStepMaterial"]) > 0


def test_machine_groups_and_states(full):
    counts = full["Machine"]["machine_group"].value_counts().to_dict()
    assert counts == {"CUT": 6, "MILL": 7, "DRILL": 6, "WELD": 5,
                      "PAINT": 4, "ASSEMBLE": 8, "TEST": 4}
    assert (full["Machine"]["lifecycle_state"] == "MAINTENANCE").sum() == 2


def test_product_mix(full):
    p = full["Product"]
    assert (p["lifecycle_state"] == "OBSOLETE").sum() == 4
    assert p["product_family"].nunique() == 6
    assert (full["Routing"]["lifecycle_state"] == "RETIRED").sum() == 4


def test_route_lengths(full):
    lengths = full["RoutingStep"].groupby("route_id").size()
    assert lengths.value_counts().to_dict() == {4: 50, 3: 25, 5: 25}
    assert lengths.mean() == 4.0


def test_schema_conformance_full(full):
    for name, df in full.items():
        assert check_table(df, name) == [], name


def test_foreign_keys_full(full):
    assert [p for p in check_all(full) if "orphan" in p] == []


def test_master_rules_full(full):
    assert check_master_rules(full) == []


def test_changeover_is_asymmetric(full):
    co = full["ChangeoverMatrix"]
    rev = co.rename(columns={"from_family": "to_family", "to_family": "from_family"})
    both = co.merge(rev, on=["machine_group", "from_family", "to_family"])
    assert (both["changeover_minutes_x"] != both["changeover_minutes_y"]).any()


def test_starter_scale_is_valid():
    tables = generate_master_data(GeneratorConfig.starter())
    assert len(tables["Product"]) == 10 and len(tables["Machine"]) == 8
    assert check_all(tables) == []
    assert check_master_rules(tables) == []


def test_reproducible_with_same_seed(full):
    again = generate_master_data(GeneratorConfig.full())
    for name in full:
        pd.testing.assert_frame_equal(full[name], again[name])


def test_different_seed_changes_data(full):
    other = generate_master_data(replace(GeneratorConfig.full(), seed=1))
    assert not full["Machine"]["speed_factor"].equals(other["Machine"]["speed_factor"])


def test_csv_roundtrip(full, tmp_path):
    write_tables(full, tmp_path)
    assert (tmp_path / "routing_step_machine.csv").exists()
    assert snake("MachineCalendar") == "machine_calendar"
    for name in full:
        assert check_table(read_table(tmp_path, name), name) == [], name