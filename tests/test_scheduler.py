import pandas as pd
import pytest

from mfgsched.config import GeneratorConfig
from mfgsched.generators.transaction_data import generate_all
from mfgsched.scheduling.scheduler import schedule
from mfgsched.schema import INJECTED_ISSUE_TYPES, VIOLATION_TYPES
from mfgsched.validation.schedule_checks import check_schedule_integrity, find_schedule_violations
from mfgsched.validation.schema_checks import check_all
from mfgsched.validation.transaction_checks import check_transaction_rules

CFG = GeneratorConfig.full()
FMT = "%Y-%m-%d %H:%M:%S"


@pytest.fixture(scope="module")
def base():
    return generate_all(CFG)


@pytest.fixture(scope="module")
def result(base):
    return schedule(base, CFG)


def _with(tables, **frames):
    out = dict(tables)
    out.update(frames)
    return out


def test_counts_add_up(result):
    ops = result["Operation"]
    n_sched = int((ops["operation_status"] == "SCHEDULED").sum())
    n_block = int((ops["operation_status"] == "BLOCKED").sum())
    assert n_sched + n_block == 2000
    assert len(result["OperationSchedule"]) == n_sched
    assert n_sched >= 0.9 * 2000
    assert (result["ProductionOrder"]["order_status"] == "PLANNED").sum() >= 0.9 * 500


def test_no_hard_constraint_violations(result):
    assert find_schedule_violations(result, CFG).empty


def test_integrity_checks_pass(result):
    assert check_schedule_integrity(result, CFG) == []
    assert check_transaction_rules(result, CFG) == []


def test_schema_and_foreign_keys(result):
    assert check_all(result) == []


def test_exception_path_is_exercised(result):
    vio = result["ConstraintViolation"]
    assert len(vio) > 0
    assert ((vio["severity"] == "SOFT") == (vio["violation_type"] == "LATE_DELIVERY")).all()


def test_new_violation_types_registered():
    assert INJECTED_ISSUE_TYPES == ("MACHINE_OVERLAP", "PRECEDENCE_VIOLATED", "MATERIAL_NOT_AVAILABLE",
                                    "OUTSIDE_SHIFT_CALENDAR", "CHANGEOVER_OMITTED")
    assert "PREDECESSOR_UNSCHEDULED" in VIOLATION_TYPES and "MACHINE_NOT_ELIGIBLE" in VIOLATION_TYPES


def test_inputs_not_mutated(base):
    snapshot = {k: v.copy() for k, v in base.items()}
    schedule(base, CFG)
    for name, df in snapshot.items():
        pd.testing.assert_frame_equal(df, base[name])


def test_reproducible(base, result):
    again = schedule(base, CFG)
    for name in ["OperationSchedule", "ScheduleEvent", "ConstraintViolation",
                 "ProductionOrder", "Operation", "OrderStatusEvent"]:
        pd.testing.assert_frame_equal(result[name], again[name])


def test_starter_pipeline_valid():
    cfg = GeneratorConfig.starter()
    out = schedule(generate_all(cfg), cfg)
    assert find_schedule_violations(out, cfg).empty
    assert check_schedule_integrity(out, cfg) == []
    assert check_all(out) == []


def test_histories_are_append_only(base, result):
    n0 = len(base["OrderStatusEvent"])
    pd.testing.assert_frame_equal(result["OrderStatusEvent"].iloc[:n0].reset_index(drop=True),
                                  base["OrderStatusEvent"].reset_index(drop=True))
    planned = int((result["ProductionOrder"]["order_status"] == "PLANNED").sum())
    assert len(result["OrderStatusEvent"]) == n0 + planned


def test_checker_catches_tampering(result):
    sched = result["OperationSchedule"]

    # 1. overlap on a machine
    s = sched.copy()
    rows = s[s["machine_id"] == s["machine_id"].value_counts().index[0]].sort_values("planned_start")
    a, b = rows.iloc[0], rows.iloc[1]
    s.loc[b.name, "planned_start"] = a["planned_start"]
    v = find_schedule_violations(_with(result, OperationSchedule=s), CFG)
    assert ((v["violation_type"] == "MACHINE_OVERLAP")
            & v["schedule_id"].isin([a["schedule_id"], b["schedule_id"]])).any()

    # 2. changeover omitted
    s = sched.copy()
    r = s[s["changeover_minutes"] > 0].iloc[0]
    s.loc[r.name, "changeover_minutes"] = 0
    v = find_schedule_violations(_with(result, OperationSchedule=s), CFG)
    assert ((v["violation_type"] == "CHANGEOVER_OMITTED") & (v["schedule_id"] == r["schedule_id"])).any()

    # 3. outside the shift calendar
    s = sched.copy()
    r = s.iloc[0]
    s.loc[r.name, "planned_end"] = (pd.to_datetime(r["planned_end"]) + pd.Timedelta(hours=10)).strftime(FMT)
    v = find_schedule_violations(_with(result, OperationSchedule=s), CFG)
    assert ((v["violation_type"] == "OUTSIDE_SHIFT_CALENDAR") & (v["schedule_id"] == r["schedule_id"])).any()

    # 4. material consumed before it is available
    req = result["MaterialRequirement"]
    req_s = req[req["operation_id"].isin(set(sched["operation_id"]))].iloc[0]
    r = sched[sched["operation_id"] == req_s["operation_id"]].iloc[0]
    lots = result["InventoryLot"].copy()
    late = (pd.to_datetime(r["planned_end"]) + pd.Timedelta(days=1)).strftime(FMT)
    lots.loc[lots["lot_id"] == req_s["allocated_lot_id"], "available_at"] = late
    v = find_schedule_violations(_with(result, InventoryLot=lots), CFG)
    assert ((v["violation_type"] == "MATERIAL_NOT_AVAILABLE") & (v["schedule_id"] == r["schedule_id"])).any()

    # 5. precedence violated
    m = sched.merge(result["Operation"][["operation_id", "predecessor_operation_id"]], on="operation_id")
    m = m[m["predecessor_operation_id"].isin(set(sched["operation_id"]))]
    r = m.iloc[0]
    pred_start = sched.set_index("operation_id").loc[r["predecessor_operation_id"], "planned_start"]
    s = sched.copy()
    s.loc[s["schedule_id"] == r["schedule_id"], "planned_start"] = pred_start
    v = find_schedule_violations(_with(result, OperationSchedule=s), CFG)
    assert ((v["violation_type"] == "PRECEDENCE_VIOLATED") & (v["schedule_id"] == r["schedule_id"])).any()