"""Generate data, build the schedule, verify it, and write all CSVs.

Usage:
    python scripts/run_schedule.py --scale starter
    python scripts/run_schedule.py --scale full
"""
import argparse
import sys

from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import write_tables
from mfgsched.generators.transaction_data import generate_all
from mfgsched.scheduling.scheduler import schedule
from mfgsched.validation.master_checks import check_master_rules
from mfgsched.validation.schedule_checks import check_schedule_integrity, find_schedule_violations
from mfgsched.validation.schema_checks import check_all
from mfgsched.validation.transaction_checks import check_transaction_rules


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()

    base = generate_all(cfg)
    pre = check_all(base) + check_master_rules(base) + check_transaction_rules(base, cfg)
    if pre:
        print("Input data FAILED validation:")
        for p in pre:
            print("  -", p)
        return 1

    result = schedule(base, cfg)
    problems = (check_all(result) + check_transaction_rules(result, cfg)
                + check_schedule_integrity(result, cfg))
    hard = find_schedule_violations(result, cfg)
    print(f"Scale: {args.scale} (seed {cfg.seed})")
    if problems or len(hard):
        print(f"Schedule verification FAILED: {len(problems)} problem(s), {len(hard)} hard violation(s)")
        for p in problems[:20]:
            print("  -", p)
        print(hard.head(20).to_string())
        return 1
    print("Schedule verification: OK (0 problems, 0 hard-constraint violations)\n")

    ops, orders = result["Operation"], result["ProductionOrder"]
    sched, vio = result["OperationSchedule"], result["ConstraintViolation"]
    n_sched = int((ops["operation_status"] == "SCHEDULED").sum())
    n_planned = int((orders["order_status"] == "PLANNED").sum())
    print(f"Operations scheduled : {n_sched} of {len(ops)} ({len(ops) - n_sched} blocked)")
    print(f"Orders PLANNED       : {n_planned} of {len(orders)} ({len(orders) - n_planned} still NEW)")
    print(f"Schedule spans       : {sched['planned_start'].min()}  ->  {sched['planned_end'].max()}")
    print(f"Changeover minutes   : {int(sched['changeover_minutes'].sum())} "
          f"({int((sched['changeover_minutes'] > 0).sum())} setups)")
    print("\nConstraintViolation rows by type:")
    for vtype, n in vio["violation_type"].value_counts().items():
        print(f"  {vtype:<26}{n:>5}")

    paths = write_tables(result, cfg.output_dir)
    print(f"\nWrote {len(paths)} CSV files to {cfg.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())