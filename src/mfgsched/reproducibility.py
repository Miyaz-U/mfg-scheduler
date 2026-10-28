"""Step 10: rebuild the datasets from the seed and fingerprint every output file."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.metrics import compute_all, write_metrics
from mfgsched.exporting import schema_sql, seed_sql, sha256_file, write_text
from mfgsched.generators.anomalies import inject_anomalies
from mfgsched.generators.common import write_tables
from mfgsched.generators.transaction_data import generate_all
from mfgsched.scheduling.scheduler import schedule


def build_dataset(cfg: GeneratorConfig, out_dir: str | Path) -> Path:
    """Generate, schedule, inject, measure and write everything under out_dir."""
    out = Path(out_dir)
    cfg = replace(cfg, output_dir=str(out / "clean"))
    clean = schedule(generate_all(cfg), cfg)
    injected, gt = inject_anomalies(clean, cfg)
    write_tables(clean, out / "clean")
    write_tables(injected, out / "injected")
    (out / "ground_truth").mkdir(parents=True, exist_ok=True)
    gt.to_csv(out / "ground_truth" / "ground_truth.csv", index=False, encoding="utf-8", lineterminator="\n")
    gt.to_json(out / "ground_truth" / "ground_truth.json", orient="records", indent=2)
    write_metrics(compute_all(clean, cfg), out / "metrics")
    write_text(out / "sql" / "schema.sql", schema_sql())
    write_text(out / "sql" / "seed_clean.sql", seed_sql(clean, "clean"))
    write_text(out / "sql" / "seed_injected.sql", seed_sql(injected, "injected"))
    return out


def fingerprint(root: str | Path) -> dict[str, dict]:
    """{relative path: {rows, sha256}} for every file; rows counts CSV data rows."""
    root = Path(root)
    result = {}
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        rows = None
        if p.suffix == ".csv":
            with open(p, encoding="utf-8") as fh:
                rows = sum(1 for _ in fh) - 1
        result[p.relative_to(root).as_posix()] = {"rows": rows, "sha256": sha256_file(p)}
    return result


def compare(a: dict, b: dict) -> list[str]:
    problems = []
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            problems.append(f"{k}: present in only one run")
        elif a[k]["rows"] != b[k]["rows"]:
            problems.append(f"{k}: row count {a[k]['rows']} vs {b[k]['rows']}")
        elif a[k]["sha256"] != b[k]["sha256"]:
            problems.append(f"{k}: checksum differs")
    return problems


def render_report(scale: str, cfg: GeneratorConfig, fp: dict, problems: list[str],
                  matches_delivered: str) -> str:
    csvs = {k: v for k, v in fp.items() if k.endswith(".csv")}
    lines = [f"# Reproducibility report: {scale} scale", "",
             f"Seed {cfg.seed}. The whole pipeline was run twice in separate processes with "
             "different PYTHONHASHSEED values, into separate folders.", "",
             f"**Result: {'PASS' if not problems else 'FAIL'}**  "
             f"({len(fp)} files compared, {len(problems)} differences)", "",
             f"Delivered data in data/{scale}: {matches_delivered}", ""]
    lines += [f"- {p}" for p in problems]
    lines += ["", "| File | Rows | SHA-256 (first 16) |", "|---|---|---|"]
    for k, v in csvs.items():
        lines.append(f"| {k} | {v['rows']} | {v['sha256'][:16]} |")
    return "\n".join(lines) + "\n"


def report_json(scale: str, cfg: GeneratorConfig, fp: dict, problems: list[str]) -> str:
    return json.dumps({"scale": scale, "seed": cfg.seed, "identical": not problems,
                       "differences": problems, "files": fp}, indent=2) + "\n"