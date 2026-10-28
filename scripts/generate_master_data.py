"""Generate and validate master data, then write CSVs.

Usage:
    python scripts/generate_master_data.py --scale starter
    python scripts/generate_master_data.py --scale full
"""
import argparse
import sys

from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import write_tables
from mfgsched.generators.master_data import generate_master_data
from mfgsched.validation.master_checks import check_master_rules
from mfgsched.validation.schema_checks import check_all


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()

    tables = generate_master_data(cfg)
    problems = check_all(tables) + check_master_rules(tables)
    print(f"Scale: {args.scale} (seed {cfg.seed})")
    if problems:
        print(f"Validation FAILED ({len(problems)} problem(s)):")
        for p in problems:
            print("  -", p)
        return 1
    print("Validation: OK (0 problems)")

    print("\nRows per table:")
    for name, df in tables.items():
        print(f"  {name:<22}{len(df):>6}")

    machines = tables["Machine"]
    print("\nMachines by group:", ", ".join(
        f"{g} {n}" for g, n in machines["machine_group"].value_counts().sort_index().items()))
    print("Machines in MAINTENANCE:", int((machines["lifecycle_state"] == "MAINTENANCE").sum()))
    lengths = tables["RoutingStep"].groupby("route_id").size().value_counts().sort_index()
    print("Steps per routing:", ", ".join(f"{k} steps x{v}" for k, v in lengths.items()))

    paths = write_tables(tables, cfg.output_dir)
    print(f"\nWrote {len(paths)} CSV files to {cfg.output_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())