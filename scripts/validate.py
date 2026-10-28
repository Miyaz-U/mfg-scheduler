"""Run the validation suite on the clean and/or injected dataset.

Run scripts/run_schedule.py and scripts/inject_anomalies.py first.
Usage:
    python scripts/validate.py --scale full
    python scripts/validate.py --scale full --dataset clean
Reports are written to reports/.
"""
import argparse
import sys
from pathlib import Path

import pandas as pd

from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import read_table
from mfgsched.schema import TABLES
from mfgsched.validation.suite import run_suite, summarize, write_reports


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    parser.add_argument("--dataset", choices=["clean", "injected", "both"], default="both")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()
    datasets = ["clean", "injected"] if args.dataset == "both" else [args.dataset]

    failed = False
    for ds in datasets:
        directory = cfg.output_dir if ds == "clean" else f"{cfg.output_dir}_injected"
        try:
            tables = {t.name: read_table(directory, t.name) for t in TABLES}
            gt = None if ds == "clean" else pd.read_csv(
                Path(f"{cfg.output_dir}_ground_truth") / "ground_truth.csv")
        except FileNotFoundError as err:
            print(f"Missing file: {err.filename}\nRun scripts/run_schedule.py and "
                  f"scripts/inject_anomalies.py with --scale {args.scale} first.")
            return 1

        results = run_suite(tables, cfg, gt)
        s = summarize(results)
        print(f"\n=== {args.scale} / {ds} dataset ({directory}) ===")
        for r in results:
            print(f"  [{r.status:<7}] {r.category}: {r.name}")
            for e in r.examples:
                print(f"            - {e}")
        print(f"  Overall: {s['overall']} ({s['PASS']} passed, {s['FAIL']} failed, {s['SKIPPED']} skipped)")
        paths = write_reports(results, "reports", args.scale, ds, cfg)
        print("  Wrote", ", ".join(str(p) for p in paths))
        failed = failed or s["FAIL"] > 0
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())