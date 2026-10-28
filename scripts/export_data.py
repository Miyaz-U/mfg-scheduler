"""Step 9: package the deliverables into exports/<scale>/.

Run scripts/run_schedule.py, inject_anomalies.py and validate.py first
(or just run scripts/build_all.py, which does everything in order).

Usage:
    python scripts/export_data.py --scale full
"""
import argparse
import json
import shutil
import sys
from pathlib import Path

import pandas as pd

from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.detection import evaluate_detection
from mfgsched.exporting import (
    build_manifest, ground_truth_sql, load_into_sqlite, schema_sql, seed_sql,
    verify_checksums, write_checksums, write_json_tables, write_text,
)
from mfgsched.generators.common import read_table, snake
from mfgsched.schema import GROUND_TRUTH, TABLES
from mfgsched.validation.suite import run_suite, summarize

OUT_ROOT = Path("exports")


def load(cfg):
    clean = {t.name: read_table(cfg.output_dir, t.name) for t in TABLES}
    injected = {t.name: read_table(f"{cfg.output_dir}_injected", t.name) for t in TABLES}
    gt = pd.read_csv(Path(f"{cfg.output_dir}_ground_truth") / "ground_truth.csv")
    return clean, injected, gt


def summary_markdown(scale, cfg, ms, vals, det, n_files) -> str:
    v, t, d = ms["volume"], ms["time"], ms["delivery"]
    u, c, m = ms["utilization"], ms["changeover"], ms["materials"]
    lines = [
        f"# Results summary ({scale} scale)", "",
        f"Seed {cfg.seed}; horizon starts {cfg.horizon_start.isoformat()} for {cfg.horizon_days} days. "
        "All data is synthetic.", "",
        "## Schedule", "",
        f"- Operations scheduled: {v['operations_scheduled']} of {v['operations']}",
        f"- Orders planned: {v['orders_planned']} of {v['orders']}",
        f"- Makespan: {t['makespan_hours']} h ({t['makespan_days']} days), "
        f"{t['schedule_start']} to {t['schedule_end']}",
        f"- On-time rate: {d['on_time_rate_pct']}% ({d['late_orders']} late orders); "
        f"total tardiness {d['total_tardiness_hours']} h, max {d['max_tardiness_hours']} h",
        f"- Utilization: overall {u['overall_pct']}%, busiest machine {u['busiest_machine_pct']}%",
        f"- Changeovers: {c['setups']} setups, {c['total_minutes']} min "
        f"({c['share_of_busy_time_pct']}% of busy time)",
        f"- Materials: {m['delayed_lots']} of {m['lots']} lots delayed; "
        f"{m['operations_waiting_for_material']} operations waited for material", "",
        "## Validation", "",
        "| Dataset | Overall | Passed | Failed | Skipped |", "|---|---|---|---|---|"]
    for ds, s in vals.items():
        lines.append(f"| {ds} | {s['overall']} | {s['PASS']} | {s['FAIL']} | {s['SKIPPED']} |")
    lines += ["", "Skipped checks on the clean dataset are the ground-truth checks "
              "(clean data has no labels).", "",
              "## Edge-case detection (injected dataset)", "",
              f"- Precision {det['precision']}, recall {det['recall']}, F1 {det['f1']}",
              f"- False positives: {det['false_positives']}; "
              f"boundary-case false alarms: {det['boundary_false_alarms']}",
              "", "| Issue type | Injected | Detected |", "|---|---|---|"]
    for k, x in det["per_type"].items():
        lines.append(f"| {k} | {x['injected']} | {x['detected']} |")
    lines += ["", f"Package contains {n_files} files; see `source_manifest.json` and "
              "`checksums.sha256`.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    scale = args.scale
    cfg = GeneratorConfig.full() if scale == "full" else GeneratorConfig.starter()

    try:
        clean, injected, gt = load(cfg)
        metrics = json.loads((Path(cfg.output_dir) / "metrics" / "summary.json").read_text("utf-8"))
    except FileNotFoundError as err:
        print(f"Missing file: {err.filename}\nRun scripts/build_all.py --scale {scale} first.")
        return 1

    # Refuse to package data that does not validate.
    vals = {"clean": summarize(run_suite(clean, cfg)),
            "injected": summarize(run_suite(injected, cfg, gt))}
    if any(s["FAIL"] for s in vals.values()):
        print("Validation FAILED; nothing exported:", vals)
        return 1
    det = evaluate_detection(injected, gt, cfg)

    out = OUT_ROOT / scale
    if out.exists():
        shutil.rmtree(out)          # always rebuild from scratch: no stale files
    out.mkdir(parents=True)

    # 1. CSV (copied byte-for-byte from the generator output)
    for label, src in (("clean", Path(cfg.output_dir)), ("injected", Path(f"{cfg.output_dir}_injected"))):
        for t in TABLES:
            dest = out / "csv" / label / f"{snake(t.name)}.csv"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src / f"{snake(t.name)}.csv", dest)
    # 2. JSON
    write_json_tables(clean, out / "json" / "clean")
    write_json_tables(injected, out / "json" / "injected")
    # 3. SQL
    schema = schema_sql()
    write_text(out / "sql" / "schema.sql", schema)
    write_text(out / "sql" / "seed_clean.sql", seed_sql(clean, f"{scale} clean schedule"))
    write_text(out / "sql" / "seed_injected.sql", seed_sql(injected, f"{scale} schedule with labelled edge cases"))
    # 4. Ground truth, kept in its own folder
    gdir = out / "ground_truth"
    gdir.mkdir()
    shutil.copyfile(Path(f"{cfg.output_dir}_ground_truth") / "ground_truth.csv", gdir / "ground_truth.csv")
    shutil.copyfile(Path(f"{cfg.output_dir}_ground_truth") / "ground_truth.json", gdir / "ground_truth.json")
    write_text(gdir / "ground_truth.sql", ground_truth_sql(gt))
    # 5. Metrics, validation summaries, documentation
    shutil.copytree(Path(cfg.output_dir) / "metrics", out / "metrics")
    for ds in ("clean", "injected"):
        for ext in ("md", "json"):
            src = Path("reports") / f"validation_{scale}_{ds}.{ext}"
            (out / "validation").mkdir(exist_ok=True)
            shutil.copyfile(src, out / "validation" / src.name)
    if Path("docs").exists():
        shutil.copytree("docs", out / "docs")
    # 6. Results summary
    n_before = sum(1 for p in out.rglob("*") if p.is_file())
    write_text(out / "SUMMARY.md", summary_markdown(scale, cfg, metrics, vals, det, n_before + 3))

    # 7. Verify the SQL really loads (foreign keys on) and matches the CSV row counts
    problems = []
    for label, tables in (("clean", clean), ("injected", injected)):
        seed = (out / "sql" / f"seed_{label}.sql").read_text(encoding="utf-8")
        con = load_into_sqlite(schema, seed)
        bad_fk = con.execute("PRAGMA foreign_key_check").fetchall()
        if bad_fk:
            problems.append(f"{label}: {len(bad_fk)} foreign-key violations in SQLite")
        for t in TABLES:
            n = con.execute(f"SELECT COUNT(*) FROM {t.name}").fetchone()[0]
            if n != len(tables[t.name]):
                problems.append(f"{label}.{t.name}: SQL has {n} rows, CSV has {len(tables[t.name])}")
        con.close()
    gt_con = load_into_sqlite("", (gdir / "ground_truth.sql").read_text(encoding="utf-8"))
    if gt_con.execute(f"SELECT COUNT(*) FROM {GROUND_TRUTH.name}").fetchone()[0] != len(gt):
        problems.append("ground truth SQL row count differs from CSV")
    gt_con.close()
    if problems:
        print("Export verification FAILED:")
        for p in problems:
            print("  -", p)
        return 1

    # 8. Manifest and checksums
    manifest = build_manifest(out, {
        "project": "mfgsched: synthetic manufacturing scheduling data, scheduler and validator",
        "scale": scale,
        "seed": cfg.seed,
        "volumes": {"skus": cfg.n_skus, "machines": cfg.n_machines, "orders": cfg.n_orders,
                    "operations": cfg.n_operations, "materials": cfg.n_materials},
        "horizon": {"start": cfg.horizon_start.isoformat(), "days": cfg.horizon_days},
        "generator_command": f"python scripts/build_all.py --scale {scale}",
        "step_commands": [
            f"python scripts/run_schedule.py --scale {scale}",
            f"python scripts/compute_metrics.py --scale {scale}",
            f"python scripts/inject_anomalies.py --scale {scale}",
            f"python scripts/validate.py --scale {scale}",
            "python scripts/build_docs.py",
            f"python scripts/export_data.py --scale {scale}"],
        "external_data_used": False,
        "sources": [],
        "provenance": "All records are generated by this repository's code from the fixed seed. "
                      "No external, public, personal or confidential data was used, so no source "
                      "URL, licence or attribution applies.",
        "row_counts": {"clean": {n: len(df) for n, df in clean.items()},
                       "injected": {n: len(df) for n, df in injected.items()},
                       "ground_truth": len(gt)},
        "validation": vals,
    })
    write_text(out / "source_manifest.json", json.dumps(manifest, indent=2) + "\n")
    write_checksums(out)
    bad = verify_checksums(out)
    if bad:
        print("Checksum verification FAILED:", bad)
        return 1

    n_files = sum(1 for p in out.rglob("*") if p.is_file())
    print(f"Exported {n_files} files to {out}/")
    print(f"  csv/, json/ (clean + injected), sql/ (schema + 2 seeds), ground_truth/ (separate)")
    print(f"  metrics/, validation/, docs/, SUMMARY.md, source_manifest.json, checksums.sha256")
    print("SQLite load check: OK (foreign keys enforced, row counts match CSV)")
    print(f"Validation: clean {vals['clean']['overall']}, injected {vals['injected']['overall']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())