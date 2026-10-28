"""Step 10: prove the generator is reproducible.

Builds every dataset twice from the fixed seed, in two separate processes with
different PYTHONHASHSEED values, then compares row counts and SHA-256 checksums
of every output file. Also checks the result matches the delivered data/<scale>.

Usage:
    python scripts/check_reproducibility.py --scale full
Writes reports/reproducibility_<scale>.md and .json
"""
import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from mfgsched.config import GeneratorConfig
from mfgsched.exporting import write_text
from mfgsched.reproducibility import (
    build_dataset, compare, fingerprint, render_report, report_json,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    parser.add_argument("--worker", help="internal: build one run into this folder")
    args = parser.parse_args()
    cfg = GeneratorConfig.full() if args.scale == "full" else GeneratorConfig.starter()

    if args.worker:
        build_dataset(cfg, args.worker)
        return 0

    tmp = Path(tempfile.mkdtemp(prefix="repro_"))
    try:
        for n, hash_seed in ((1, "1"), (2, "2")):
            print(f"Run {n} (PYTHONHASHSEED={hash_seed}) ...", flush=True)
            env = {**os.environ, "PYTHONHASHSEED": hash_seed}
            code = subprocess.call([sys.executable, __file__, "--scale", args.scale,
                                    "--worker", str(tmp / f"run{n}")], env=env)
            if code != 0:
                print(f"Run {n} failed.")
                return 1
        fp1, fp2 = fingerprint(tmp / "run1"), fingerprint(tmp / "run2")
        problems = compare(fp1, fp2)

        # Does a fresh build match the data already delivered in data/<scale>?
        delivered = Path(cfg.output_dir)
        if delivered.exists():
            fresh = {k[len("clean/"):]: v for k, v in fp1.items()
                     if k.startswith("clean/") and k.endswith(".csv")}
            have = fingerprint(delivered)
            diffs = [k for k, v in fresh.items() if k not in have or have[k]["sha256"] != v["sha256"]]
            matches = "MATCH (all clean CSVs identical)" if not diffs else \
                "DIFFERENT: " + ", ".join(diffs[:5])
            if diffs:
                problems.append(f"data/{args.scale} differs from a fresh build; re-run build_all.py")
        else:
            matches = "not present (skipped)"

        write_text(Path("reports") / f"reproducibility_{args.scale}.md",
                   render_report(args.scale, cfg, fp1, problems, matches))
        write_text(Path("reports") / f"reproducibility_{args.scale}.json",
                   report_json(args.scale, cfg, fp1, problems))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    csvs = [k for k in fp1 if k.endswith(".csv")]
    print(f"\nScale {args.scale}, seed {cfg.seed}: compared {len(fp1)} files "
          f"({len(csvs)} CSV) across two runs")
    print(f"Delivered data/{args.scale}: {matches}")
    if problems:
        print("REPRODUCIBILITY FAILED:")
        for p in problems[:20]:
            print("  -", p)
        return 1
    print("REPRODUCIBLE: identical row counts and checksums.")
    print(f"Wrote reports/reproducibility_{args.scale}.md and .json")
    return 0


if __name__ == "__main__":
    sys.exit(main())