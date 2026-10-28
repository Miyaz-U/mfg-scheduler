"""Helpers shared by all generators."""
from __future__ import annotations

import re
import zlib
from pathlib import Path

import numpy as np
import pandas as pd

from mfgsched.schema import TABLE_BY_NAME


def get_rng(seed: int, stream: str) -> np.random.Generator:
    """An independent, reproducible random stream for one generator.

    crc32 is used (not Python's hash()) because hash() changes between runs.
    """
    return np.random.default_rng([seed, zlib.crc32(stream.encode("utf-8"))])


def make_frame(table_name: str, rows: list[dict]) -> pd.DataFrame:
    """Build a DataFrame with exactly the columns (and order) of the schema."""
    columns = [f.name for f in TABLE_BY_NAME[table_name].fields]
    return pd.DataFrame(rows, columns=columns)


def snake(name: str) -> str:
    """RoutingStepMachine -> routing_step_machine"""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def write_tables(tables: dict[str, pd.DataFrame], out_dir: str | Path) -> list[Path]:
    """Write each table to <out_dir>/<snake_name>.csv (portable line endings)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, df in tables.items():
        path = out / f"{snake(name)}.csv"
        df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
        paths.append(path)
    return paths


def read_table(directory: str | Path, name: str) -> pd.DataFrame:
    return pd.read_csv(Path(directory) / f"{snake(name)}.csv")