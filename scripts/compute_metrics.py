"""Compute evaluation metrics from the CSV files in data/<scale>/.

Run scripts/run_schedule.py first.
Usage:
    python scripts/compute_metrics.py --scale full
"""
import argparse
import sys

from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.metrics import compute_all, write_metrics
from mfgsched.generators.common import read_table
from mfgsched.schema import TABLES


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()

    try:
        tables = {t.name: read_table(cfg.output_dir, t.name) for t in TABLES}
    except FileNotFoundError as err:
        print(f"Missing file: {err.filename}\nRun: python scripts/run_schedule.py --scale {args.scale}")
        return 1

    m = compute_all(tables, cfg)
    if m["problems"]:
        print("Metric reconciliation FAILED:")
        for p in m["problems"]:
            print("  -", p)
        return 1
    s = m["summary"]

    print(f"Scale: {args.scale} (seed {s['scale']['seed']})  |  reconciliation: OK\n")
    v, tm, d = s["volume"], s["time"], s["delivery"]
    print(f"Operations scheduled : {v['operations_scheduled']} of {v['operations']} ({v['operations_scheduled_pct']}%)")
    print(f"Orders fully planned : {v['orders_planned']} of {v['orders']}")
    print(f"Makespan             : {tm['makespan_hours']} h ({tm['makespan_days']} days), "
          f"{tm['schedule_start']} -> {tm['schedule_end']}")
    print(f"On-time rate         : {d['on_time_rate_pct']}%  ({d['late_orders']} late of {v['orders_planned']} planned)")
    print(f"Tardiness            : total {d['total_tardiness_hours']} h, mean of late orders "
          f"{d['mean_tardiness_hours_of_late_orders']} h, max {d['max_tardiness_hours']} h")
    print(f"Mean flow / queue    : {d['mean_flow_time_hours']} h / {d['mean_queue_hours']} h")
    print("On-time by priority  : " + ", ".join(
        f"{k} {x['on_time_rate_pct']}%" for k, x in d["by_priority"].items()))
    u, c, mt = s["utilization"], s["changeover"], s["materials"]
    print(f"\nUtilization overall  : {u['overall_pct']}%  (busiest machine {u['busiest_machine_pct']}%)")
    print("Utilization by group : " + ", ".join(f"{g} {x}%" for g, x in u["by_group_pct"].items()))
    print(f"Changeovers          : {c['setups']} setups, {c['total_minutes']} min "
          f"({c['share_of_busy_time_pct']}% of busy time)")
    print(f"Materials            : {mt['delayed_lots']} delayed lots of {mt['lots']}; "
          f"{mt['operations_waiting_for_material']} operations waited "
          f"{mt['material_wait_hours_total']} h in total")
    print(f"Exceptions           : {s['exceptions']['violations_by_type']}")

    print("\nAcceptance criteria:")
    for crit in s["acceptance_criteria"]:
        print(f"  [{'PASS' if crit['passed'] else 'FAIL'}] {crit['criterion']}: {crit['measure']}")

    paths = write_metrics(m, f"{cfg.output_dir}/metrics")
    print(f"\nWrote {len(paths)} files to {cfg.output_dir}/metrics/")
    return 0 if all(c["passed"] for c in s["acceptance_criteria"]) else 1


if __name__ == "__main__":
    sys.exit(main())