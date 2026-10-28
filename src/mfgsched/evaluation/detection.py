"""Compare what the independent checker finds with the ground-truth labels."""
from __future__ import annotations

import pandas as pd

from mfgsched.config import GeneratorConfig
from mfgsched.schema import INJECTED_ISSUE_TYPES
from mfgsched.validation.schedule_checks import find_schedule_violations


def evaluate_detection(tables: dict[str, pd.DataFrame], ground_truth: pd.DataFrame,
                       cfg: GeneratorConfig) -> dict:
    found = find_schedule_violations(tables, cfg)
    detected = set(zip(found["schedule_id"], found["violation_type"]))
    injected = ground_truth[ground_truth["ground_truth"] == "INJECTED_VIOLATION"]
    expected = set(zip(injected["entity_id"], injected["issue_type"]))
    hits, missed, false_alarms = expected & detected, expected - detected, detected - expected

    boundary = ground_truth[ground_truth["ground_truth"] == "VALID_BOUNDARY"]
    flagged_rows = set(found["schedule_id"])
    precision = len(hits) / len(detected) if detected else 1.0
    recall = len(hits) / len(expected) if expected else 1.0
    return {
        "injected_cases": len(expected), "detected_cases": len(hits),
        "missed": sorted(missed)[:10], "false_positives": len(false_alarms),
        "false_alarm_examples": sorted(false_alarms)[:10],
        "precision": round(precision, 4), "recall": round(recall, 4),
        "f1": round(2 * precision * recall / (precision + recall), 4) if precision + recall else 0.0,
        "boundary_cases": len(boundary),
        "boundary_false_alarms": int(boundary["entity_id"].isin(flagged_rows).sum()),
        "per_type": {t: {"injected": sum(1 for e in expected if e[1] == t),
                         "detected": sum(1 for e in hits if e[1] == t)}
                     for t in INJECTED_ISSUE_TYPES},
    }