import pandas as pd
import pytest

from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.detection import evaluate_detection
from mfgsched.generators.anomalies import inject_anomalies
from mfgsched.generators.transaction_data import generate_all
from mfgsched.scheduling.scheduler import schedule
from mfgsched.schema import BOUNDARY_ISSUE_TYPES, INJECTED_ISSUE_TYPES
from mfgsched.validation.schedule_checks import check_schedule_integrity
from mfgsched.validation.schema_checks import check_all, check_table
from mfgsched.validation.transaction_checks import check_transaction_rules

CFG = GeneratorConfig.full()


@pytest.fixture(scope="module")
def clean():
    return schedule(generate_all(CFG), CFG)


@pytest.fixture(scope="module")
def injected(clean):
    return inject_anomalies(clean, CFG)


def test_ground_truth_conforms_and_is_separate(injected):
    tables, gt = injected
    assert check_table(gt, "GroundTruth") == []
    assert "GroundTruth" not in tables


def test_counts_per_type(injected):
    _, gt = injected
    counts = gt["issue_type"].value_counts().to_dict()
    for t in INJECTED_ISSUE_TYPES + BOUNDARY_ISSUE_TYPES:
        assert 5 <= counts.get(t, 0) <= CFG.n_anomalies_per_type, t


def test_detection_is_exact(injected):
    tables, gt = injected
    ev = evaluate_detection(tables, gt, CFG)
    assert ev["precision"] == 1.0 and ev["recall"] == 1.0
    assert ev["false_positives"] == 0 and ev["boundary_false_alarms"] == 0
    assert all(v["injected"] == v["detected"] for v in ev["per_type"].values())


def test_checker_finds_nothing_on_the_clean_baseline(clean, injected):
    _, gt = injected
    ev = evaluate_detection(clean, gt, CFG)
    assert ev["recall"] == 0.0 and ev["false_positives"] == 0


def test_inputs_not_mutated(clean):
    snapshot = {k: v.copy() for k, v in clean.items()}
    inject_anomalies(clean, CFG)
    for name, df in snapshot.items():
        pd.testing.assert_frame_equal(df, clean[name])


def test_only_labelled_rows_changed(clean, injected):
    tables, gt = injected
    before = clean["OperationSchedule"].set_index("schedule_id")
    after = tables["OperationSchedule"].set_index("schedule_id")
    diff = (before != after) & ~(before.isna() & after.isna())
    assert set(diff.index[diff.any(axis=1)]) <= set(gt["entity_id"])

    lots_b = clean["InventoryLot"].set_index("lot_id")
    lots_a = tables["InventoryLot"].set_index("lot_id")
    lot_diff = (lots_b != lots_a) & ~(lots_b.isna() & lots_a.isna())
    n_material = int(gt["issue_type"].isin(["MATERIAL_NOT_AVAILABLE",
                                            "BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE"]).sum())
    assert int(lot_diff.any(axis=1).sum()) <= n_material

    for name in clean:
        if name not in ("OperationSchedule", "InventoryLot"):
            pd.testing.assert_frame_equal(clean[name], tables[name])


def test_injected_data_is_otherwise_consistent(injected):
    tables, _ = injected
    assert check_all(tables) == []
    assert check_transaction_rules(tables, CFG) == []
    assert check_schedule_integrity(tables, CFG) == []


def test_reproducible(clean, injected):
    tables, gt = injected
    tables2, gt2 = inject_anomalies(clean, CFG)
    pd.testing.assert_frame_equal(gt, gt2)
    for name in ("OperationSchedule", "InventoryLot"):
        pd.testing.assert_frame_equal(tables[name], tables2[name])


def test_no_label_leakage_into_inputs(injected):
    tables, _ = injected
    forbidden = {"issue_type", "ground_truth", "expected_decision", "case_id"}
    for name, df in tables.items():
        assert not forbidden & set(df.columns), name


def test_ground_truth_ids_and_references(injected):
    tables, gt = injected
    assert list(gt["case_id"]) == [f"GT-2026-{i:06d}" for i in range(1, len(gt) + 1)]
    assert set(gt["entity_id"]) <= set(tables["OperationSchedule"]["schedule_id"])
    assert gt["entity_id"].is_unique


def test_starter_scale_is_exact():
    cfg = GeneratorConfig.starter()
    tables, gt = inject_anomalies(schedule(generate_all(cfg), cfg), cfg)
    ev = evaluate_detection(tables, gt, cfg)
    assert ev["precision"] == 1.0 and ev["recall"] == 1.0 and ev["boundary_false_alarms"] == 0
    assert check_all(tables) == [] and check_schedule_integrity(tables, cfg) == []