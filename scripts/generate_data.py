"""Generate, validate and write all data generated so far.

Usage:
    python scripts/generate_data.py --scale starter
    python scripts/generate_data.py --scale full
"""
import argparse
import sys

from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import write_tables
from mfgsched.generators.transaction_data import generate_all
from mfgsched.schema import MACHINE_GROUPS
from mfgsched.validation.master_checks import check_master_rules
from mfgsched.validation.schema_checks import check_all
from mfgsched.validation.transaction_checks import check_transaction_rules


def load_vs_capacity(t):
    """Rough demand / capacity per machine group (ignores speed and changeovers)."""
    cal = t["MachineCalendar"].merge(
        t["Machine"][["machine_id", "machine_group", "operators_required"]], on="machine_id")
    usable = cal[cal["operators_assigned"] >= cal["operators_required"]]
    capacity = usable.groupby("machine_group")["available_minutes"].sum()
    ops = t["Operation"].merge(t["RoutingStep"][["route_id", "step_seq", "op_type"]],
                               on=["route_id", "step_seq"])
    demand = ops.groupby("op_type")["standard_minutes"].sum()
    return (demand / capacity).round(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()

    tables = generate_all(cfg)
    problems = check_all(tables) + check_master_rules(tables) + check_transaction_rules(tables, cfg)
    print(f"Scale: {args.scale} (seed {cfg.seed})")
    if problems:
        print(f"Validation FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print("  -", p)
        return 1
    print("Validation: OK (0 problems)\n")

    print("Rows per table:")
    for name, df in tables.items():
        print(f"  {name:<22}{len(df):>6}")

    orders, lots, cal = tables["ProductionOrder"], tables["InventoryLot"], tables["MachineCalendar"]
    print("\nOrders by priority:", ", ".join(
        f"P{k} x{v}" for k, v in orders["priority"].value_counts().sort_index().items()))
    print(f"Delayed lots: {int(lots['is_delayed'].sum())} of {len(lots)}; "
          f"in transit at horizon start: {int((lots['lot_status'] == 'IN_TRANSIT').sum())}")
    print(f"Calendar slots with downtime: {int((cal['downtime_minutes'] > 0).sum())} of {len(cal)}")
    print("\nDemand / capacity by machine group (should be well below 1.0):")
    ratios = load_vs_capacity(tables)
    print("  " + ", ".join(f"{g} {ratios[g]}" for g in MACHINE_GROUPS))

    paths = write_tables(tables, cfg.output_dir)
    print(f"\nWrote {len(paths)} CSV files to {cfg.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())