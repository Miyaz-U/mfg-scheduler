import json
from dataclasses import asdict

import pandas as pd
import pytest

from mfgsched.config import GeneratorConfig
from mfgsched.generators.anomalies import inject_anomalies
from mfgsched.generators.transaction_data import generate_all
from mfgsched.scheduling.scheduler import schedule
from mfgsched.validation.suite import run_suite, write_reports

CFG = GeneratorConfig.full()
CATEGORIES = {
    "Primary-key uniqueness", "Foreign-key integrity", "Allowed enums", "Field validity",
    "Date ordering", "Arithmetic reconciliation", "Lifecycle transitions", "Duplicates",
    "Business rules", "Schedule hard constraints", "Coverage and variation",
    "Ground-truth separation",
}


@pytest.fixture(scope="module")
def clean():
    return schedule(generate_all(CFG), CFG)


@pytest.fixture(scope="module")
def injected(clean):
    return inject_anomalies(clean, CFG)


def _copy(tables):
    return {k: v.copy() for k, v in tables.items()}


def _status(tables, check_id, gt=None):
    return run_suite(tables, CFG, gt, only={check_id})[0].status


def test_clean_dataset_passes_every_check(clean):
    results = run_suite(clean, CFG)
    assert [r for r in results if r.status == "FAIL"] == []
    by_id = {r.check_id: r.status for r in results}
    assert by_id["scenarios"] == "PASS" and by_id["hard_constraints"] == "PASS"
    assert {r.status for r in results if r.check_id.startswith("gt_")} == {"SKIPPED"}


def test_injected_dataset_passes_every_check(injected):
    tables, gt = injected
    results = run_suite(tables, CFG, gt)
    assert [r for r in results if r.status == "FAIL"] == []
    assert all(r.status == "PASS" for r in results)


def test_suite_covers_the_required_categories(clean):
    assert CATEGORIES <= {r.category for r in run_suite(clean, CFG)}


def test_duplicate_primary_key_is_caught(clean):
    assert _status(clean, "keys") == "PASS"
    t = _copy(clean)
    t["Machine"] = pd.concat([t["Machine"], t["Machine"].iloc[[0]]], ignore_index=True)
    assert _status(t, "keys") == "FAIL"


def test_orphan_foreign_key_is_caught(clean):
    t = _copy(clean)
    t["Operation"].loc[0, "order_id"] = "ORD-2026-999999"
    assert _status(t, "foreign_keys") == "FAIL"


def test_invalid_enum_is_caught(clean):
    t = _copy(clean)
    t["Machine"].loc[0, "machine_group"] = "LASER"
    assert _status(t, "enums") == "FAIL"


def test_date_order_violation_is_caught(clean):
    t = _copy(clean)
    t["ProductionOrder"].loc[0, "due_date"] = "2000-01-01"
    assert _status(t, "dates") == "FAIL"


def test_reconciliation_break_is_caught(clean):
    t = _copy(clean)
    t["InventoryLot"].loc[0, "quantity_allocated"] += 5.0
    assert _status(t, "totals") == "FAIL"


def test_invalid_lifecycle_transition_is_caught(clean):
    assert _status(clean, "lifecycle") == "PASS"
    t = _copy(clean)
    row = {"event_id": "OEV-2026-999999", "order_id": t["ProductionOrder"].loc[0, "order_id"],
           "from_status": "NEW", "to_status": "COMPLETED",
           "event_ts": "2027-01-01 00:00:00", "reason": "tampered"}
    t["OrderStatusEvent"] = pd.concat([t["OrderStatusEvent"], pd.DataFrame([row])], ignore_index=True)
    assert _status(t, "lifecycle") == "FAIL"


def test_duplicate_record_is_caught(clean):
    assert _status(clean, "duplicates") == "PASS"
    t = _copy(clean)
    t["Material"] = pd.concat([t["Material"], t["Material"].iloc[[0]]], ignore_index=True)
    assert _status(t, "duplicates") == "FAIL"


def test_hard_constraint_break_is_caught_on_clean_data(clean):
    t = _copy(clean)
    sched = t["OperationSchedule"]
    sched.loc[sched.index[sched["changeover_minutes"] > 0][0], "changeover_minutes"] = 0
    assert _status(t, "hard_constraints") == "FAIL"


def test_missing_label_is_caught_on_injected_data(injected):
    tables, gt = injected
    first_injected = gt.index[gt["ground_truth"] == "INJECTED_VIOLATION"][0]
    assert _status(tables, "hard_constraints", gt) == "PASS"
    assert _status(tables, "hard_constraints", gt.drop(index=first_injected)) == "FAIL"


def test_reports_are_written(clean, tmp_path):
    results = run_suite(clean, CFG)
    md, js = write_reports(results, tmp_path, "full", "clean", CFG)
    assert "Overall: PASS" in md.read_text(encoding="utf-8")
    assert json.loads(js.read_text(encoding="utf-8"))["summary"]["overall"] == "PASS"


def test_suite_is_deterministic(clean):
    assert [asdict(r) for r in run_suite(clean, CFG)] == [asdict(r) for r in run_suite(clean, CFG)]


def test_starter_scale_passes():
    cfg = GeneratorConfig.starter()
    results = run_suite(schedule(generate_all(cfg), cfg), cfg)
    assert [r for r in results if r.status == "FAIL"] == []