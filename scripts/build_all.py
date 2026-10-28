"""One command that rebuilds everything, in order, and stops at the first failure.

Usage:
    python scripts/build_all.py --scale full
    python scripts/build_all.py --scale starter
"""
import argparse
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale", choices=["full", "starter"], default="full")
    args = parser.parse_args()
    steps = [
        ["scripts/run_schedule.py", "--scale", args.scale],
        ["scripts/compute_metrics.py", "--scale", args.scale],
        ["scripts/inject_anomalies.py", "--scale", args.scale],
        ["scripts/validate.py", "--scale", args.scale],
        ["scripts/build_docs.py"],
        ["scripts/export_data.py", "--scale", args.scale],
        ["scripts/check_reproducibility.py", "--scale", args.scale],
    ]
    for i, step in enumerate(steps, 1):
        print(f"\n===== [{i}/{len(steps)}] python {' '.join(step)} =====", flush=True)
        if subprocess.call([sys.executable, *step]) != 0:
            print(f"\nStopped: step {i} failed.")
            return 1
    print(f"\nBuild complete: exports/{args.scale}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())