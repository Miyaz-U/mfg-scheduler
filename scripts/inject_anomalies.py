"""Inject labelled edge cases into the clean schedule from run_schedule.py.

Reads   data/<scale>/            (clean baseline)
Writes  data/<scale>_injected/   (all tables, with anomalies)
        data/<scale>_ground_truth/ground_truth.csv and .json (labels, kept separate)

Usage:
    python scripts/inject_anomalies.py --scale full
"""
import argparse
import sys
from pathlib import Path

from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.detection import evaluate_detection
from mfgsched.generators.anomalies import inject_anomalies
from mfgsched.generators.common import read_table, write_tables
from mfgsched.schema import BOUNDARY_ISSUE_TYPES, INJECTED_ISSUE_TYPES, TABLES
from mfgsched.validation.schedule_checks import check_schedule_integrity, find_schedule_violations
from mfgsched.validation.schema_checks import check_all, check_table
from mfgsched.validation.transaction_checks import check_transaction_rules


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()

    try:
        clean = {t.name: read_table(cfg.output_dir, t.name) for t in TABLES}
    except FileNotFoundError as err:
        print(f"Missing file: {err.filename}\nRun: python scripts/run_schedule.py --scale {args.scale}")
        return 1
    if check_schedule_integrity(clean, cfg) or len(find_schedule_violations(clean, cfg)):
        print("The baseline in", cfg.output_dir, "is not clean; re-run scripts/run_schedule.py")
        return 1

    injected, gt = inject_anomalies(clean, cfg)
    problems = (check_all(injected) + check_transaction_rules(injected, cfg)
                + check_schedule_integrity(injected, cfg) + check_table(gt, "GroundTruth"))
    if problems:
        print("Injected dataset FAILED consistency checks:")
        for p in problems[:20]:
            print("  -", p)
        return 1
    ev = evaluate_detection(injected, gt, cfg)

    print(f"Scale: {args.scale} (seed {cfg.seed}), requested {cfg.n_anomalies_per_type} per type\n")
    print(f"{'issue type':<40}{'injected':>9}{'detected':>10}")
    for t in INJECTED_ISSUE_TYPES:
        print(f"  {t:<38}{ev['per_type'][t]['injected']:>9}{ev['per_type'][t]['detected']:>10}")
    print("\nValid boundary cases (must NOT be flagged):")
    for t in BOUNDARY_ISSUE_TYPES:
        print(f"  {t:<38}{int((gt['issue_type'] == t).sum()):>9}")
    print(f"\nDetection: precision {ev['precision']}, recall {ev['recall']}, F1 {ev['f1']}; "
          f"false positives {ev['false_positives']}; boundary false alarms {ev['boundary_false_alarms']}")

    in_dir, label_dir = f"{cfg.output_dir}_injected", Path(f"{cfg.output_dir}_ground_truth")
    write_tables(injected, in_dir)
    label_dir.mkdir(parents=True, exist_ok=True)
    gt.to_csv(label_dir / "ground_truth.csv", index=False, encoding="utf-8", lineterminator="\n")
    gt.to_json(label_dir / "ground_truth.json", orient="records", indent=2)
    print(f"\nWrote {len(TABLES)} input tables to {in_dir}/ and {len(gt)} labels to {label_dir}/")
    return 0 if ev["precision"] == 1.0 and ev["recall"] == 1.0 and ev["boundary_false_alarms"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())