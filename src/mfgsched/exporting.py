"""Step 9: export helpers (JSON, SQL schema, SQL seed data, manifest).

The SQL schema is generated from mfgsched.schema, so it can never drift from
the data dictionary. Everything here is deterministic: same input tables give
byte-identical output files.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pandas as pd

from mfgsched.generators.common import snake
from mfgsched.schema import GROUND_TRUTH, TABLES, TABLE_BY_NAME, foreign_keys

SQL_TYPES = {"str": "TEXT", "int": "INTEGER", "float": "REAL",
             "bool": "INTEGER", "date": "TEXT", "datetime": "TEXT"}
BATCH = 500  # rows per INSERT statement


# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
def _q(text: str) -> str:
    return "'" + str(text).replace("'", "''") + "'"


def table_order() -> list[str]:
    """Parent tables before children (self-references are ignored)."""
    names = [t.name for t in TABLES]
    done: list[str] = []
    while len(done) < len(names):
        progressed = False
        for n in names:
            if n in done:
                continue
            parents = {r.parent for r in foreign_keys(n) if r.parent != n}
            if parents <= set(done):
                done.append(n)
                progressed = True
        if not progressed:
            raise ValueError("Circular foreign keys between tables")
    return done


def create_table_sql(name: str, with_foreign_keys: bool = True) -> str:
    t = TABLE_BY_NAME[name]
    lines = []
    for f in t.fields:
        col = f"  {f.name} {SQL_TYPES[f.dtype]}" + (" NOT NULL" if f.required else "")
        if f.allowed:
            col += f" CHECK ({f.name} IN ({', '.join(_q(v) for v in f.allowed)}))"
        if f.min_value is not None:
            col += f" CHECK ({f.name} >= {f.min_value:g})"
        if f.max_value is not None:
            col += f" CHECK ({f.name} <= {f.max_value:g})"
        lines.append(col)
    lines.append(f"  PRIMARY KEY ({', '.join(t.pk)})")
    for u in t.unique_together:
        lines.append(f"  UNIQUE ({', '.join(u)})")
    for f in t.fields:
        if f.unique and (f.name,) != tuple(t.pk):
            lines.append(f"  UNIQUE ({f.name})")
    if with_foreign_keys:
        for r in foreign_keys(name):
            lines.append(f"  FOREIGN KEY ({', '.join(r.child_cols)}) REFERENCES "
                         f"{r.parent} ({', '.join(r.parent_cols)})")
    body = ",\n".join(lines)
    return f"CREATE TABLE {name} (\n{body}\n);"


def schema_sql() -> str:
    parts = ["-- Manufacturing scheduling schema (generated from src/mfgsched/schema.py).",
             "-- Load with:  sqlite3 mfg.db < schema.sql", "PRAGMA foreign_keys = ON;", ""]
    for name in table_order():
        parts += [f"-- {TABLE_BY_NAME[name].description}", create_table_sql(name), ""]
    return "\n".join(parts)


def ground_truth_schema_sql() -> str:
    return create_table_sql(GROUND_TRUTH.name, with_foreign_keys=False)


# ---------------------------------------------------------------------------
# SQL seed data
# ---------------------------------------------------------------------------
def _literal(value, dtype: str) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "NULL"
    if dtype == "bool":
        return "1" if bool(value) else "0"
    if dtype == "int":
        return str(int(value))
    if dtype == "float":
        return repr(float(value))
    return _q(value)


def insert_sql(name: str, df: pd.DataFrame) -> str:
    t = TABLE_BY_NAME[name]
    cols = [f.name for f in t.fields]
    dtypes = [f.dtype for f in t.fields]
    rows = []
    for rec in df[cols].itertuples(index=False, name=None):
        rows.append("(" + ", ".join(_literal(v, d) for v, d in zip(rec, dtypes)) + ")")
    out = []
    for i in range(0, len(rows), BATCH):
        chunk = ",\n  ".join(rows[i:i + BATCH])
        out.append(f"INSERT INTO {name} ({', '.join(cols)}) VALUES\n  {chunk};")
    return "\n".join(out)


def seed_sql(tables: dict[str, pd.DataFrame], title: str) -> str:
    parts = [f"-- Seed data: {title}. Load schema.sql first.", "PRAGMA foreign_keys = ON;",
             "BEGIN TRANSACTION;"]
    for name in table_order():
        parts += [f"-- {name}: {len(tables[name])} rows", insert_sql(name, tables[name]).rstrip()]
    parts += ["COMMIT;", ""]
    return "\n".join(p for p in parts if p != "")


def ground_truth_sql(gt: pd.DataFrame) -> str:
    return "\n".join([
        "-- Ground-truth labels. Keep SEPARATE from the input tables; never load into the same",
        "-- database a detector reads from.", ground_truth_schema_sql(), "BEGIN TRANSACTION;",
        insert_sql(GROUND_TRUTH.name, gt), "COMMIT;", ""])


def load_into_sqlite(schema: str, seed: str) -> sqlite3.Connection:
    """Build an in-memory database from the generated SQL (used to verify it)."""
    con = sqlite3.connect(":memory:")
    con.executescript(schema)
    con.executescript(seed)
    return con


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def write_text(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def write_json_tables(tables: dict[str, pd.DataFrame], out_dir: Path) -> list[Path]:
    paths = []
    for name, df in tables.items():
        path = out_dir / f"{snake(name)}.json"
        out_dir.mkdir(parents=True, exist_ok=True)
        # to_json writes NaN as null and bools as true/false
        text = df.to_json(orient="records", indent=2) + "\n"
        path.write_text(text, encoding="utf-8", newline="\n")
        paths.append(path)
    return paths


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def list_files(root: Path, skip: tuple[str, ...] = ()) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and p.name not in skip)


def build_manifest(root: Path, info: dict) -> dict:
    """File inventory with checksums. No timestamps, so it is reproducible."""
    files = [{"path": p.relative_to(root).as_posix(), "bytes": p.stat().st_size,
              "sha256": sha256_file(p)}
             for p in list_files(root, skip=("source_manifest.json", "checksums.sha256"))]
    return {**info, "files": files}


def write_checksums(root: Path) -> Path:
    lines = [f"{sha256_file(p)}  {p.relative_to(root).as_posix()}"
             for p in list_files(root, skip=("checksums.sha256",))]
    return write_text(root / "checksums.sha256", "\n".join(lines) + "\n")


def verify_checksums(root: Path) -> list[str]:
    """Return a list of problems (empty = every file matches checksums.sha256)."""
    problems = []
    listed = set()
    for line in (root / "checksums.sha256").read_text(encoding="utf-8").splitlines():
        digest, rel = line.split("  ", 1)
        listed.add(rel)
        p = root / rel
        if not p.exists():
            problems.append(f"missing file: {rel}")
        elif sha256_file(p) != digest:
            problems.append(f"checksum mismatch: {rel}")
    for p in list_files(root, skip=("checksums.sha256",)):
        if p.relative_to(root).as_posix() not in listed:
            problems.append(f"unlisted file: {p.relative_to(root).as_posix()}")
    return problems