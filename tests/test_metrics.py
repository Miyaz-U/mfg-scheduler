import json

import pandas as pd
import pytest

from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.metrics import compute_all, json_default
from mfgsched.generators.common import read_table, write_tables
from mfgsched.generators.transaction_data import generate_all
from mfgsched.scheduling.scheduler import schedule
from mfgsched.schema import MACHINE_GROUPS, TABLES

CFG = GeneratorConfig.full()


@pytest.fixture(scope="module")
def result():
    return schedule(generate_all(CFG), CFG)


@pytest.fixture(scope="module")
def metrics(result):
    return compute_all(result, CFG)


def _flat(d, prefix=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(v, f"{prefix}{k}."))
        elif isinstance(v, list):
            for i, item in enumerate(v):
                out.update(_flat(item, f"{prefix}{k}[{i}].") if isinstance(item, dict) else {f"{prefix}{k}[{i}]": item})
        else:
            out[f"{prefix}{k}"] = v
    return out


def test_reconciliation_has_no_problems(metrics):
    assert metrics["problems"] == []


def test_order_metrics_shape(result, metrics):
    om = metrics["orders"]
    assert len(om) == 500
    assert om["fully_scheduled"].sum() == (result["ProductionOrder"]["order_status"] == "PLANNED").sum()
    assert om.loc[om["fully_scheduled"], "lateness_hours"].notna().all()
    assert om.loc[~om["fully_scheduled"], "lateness_hours"].isna().all()
    assert (om.loc[om["fully_scheduled"], "flow_time_hours"] > 0).all()


def test_lateness_matches_late_delivery_violations(result, metrics):
    om, ops, vio = metrics["orders"], result["Operation"], result["ConstraintViolation"]
    op_order = dict(zip(ops["operation_id"], ops["order_id"]))
    flagged = set(vio.loc[vio["violation_type"] == "LATE_DELIVERY", "operation_id"].map(op_order))
    assert set(om.loc[om["is_late"], "order_id"]) == flagged


def test_utilization_bounds(metrics):
    mm = metrics["machines"]
    assert mm["utilization"].between(0, 1).all()
    maintenance = mm[mm["lifecycle_state"] == "MAINTENANCE"]
    assert (maintenance["jobs"] == 0).all() and (maintenance["utilization"] == 0).all()
    assert set(metrics["summary"]["utilization"]["by_group_pct"]) == set(MACHINE_GROUPS)


def test_acceptance_criteria_pass(metrics):
    criteria = metrics["summary"]["acceptance_criteria"]
    assert len(criteria) == 4
    assert all(c["passed"] for c in criteria)


def test_summary_numbers_are_consistent(metrics):
    s = metrics["summary"]
    assert s["delivery"]["on_time_orders"] + s["delivery"]["late_orders"] == s["volume"]["orders_planned"]
    assert s["time"]["makespan_hours"] > 0
    assert s["volume"]["operations_scheduled"] + s["volume"]["operations_blocked"] == 2000
    assert json.loads(json.dumps(s, default=json_default))["volume"]["orders"] == 500


def test_wait_attribution_is_non_negative(result, metrics):
    w = metrics["operations"]
    planned = result["OperationSchedule"]
    assert len(w) == (planned["schedule_status"] == "PLANNED").sum()
    assert (w["material_wait_minutes"] >= 0).all() and (w["capacity_wait_minutes"] >= 0).all()


def test_metrics_are_reproducible(result, metrics):
    again = compute_all(result, CFG)
    assert again["summary"] == metrics["summary"]
    pd.testing.assert_frame_equal(again["orders"], metrics["orders"])


def test_metrics_from_csv_files_match_in_memory(result, metrics, tmp_path):
    write_tables(result, tmp_path)
    loaded = {t.name: read_table(tmp_path, t.name) for t in TABLES}
    from_csv = compute_all(loaded, CFG)
    a, b = _flat(metrics["summary"]), _flat(from_csv["summary"])
    assert a.keys() == b.keys()
    for key in a:
        assert b[key] == pytest.approx(a[key]) if isinstance(a[key], (int, float)) and not isinstance(a[key], bool) else b[key] == a[key]
    assert from_csv["problems"] == []