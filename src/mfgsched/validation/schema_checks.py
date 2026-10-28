"""Generic checks driven by schema.py: columns, nulls, types, ranges, enums,
patterns, uniqueness and foreign keys. Each function returns a list of
problem messages (an empty list means everything passed)."""
from __future__ import annotations

import pandas as pd

from mfgsched.schema import RELATIONSHIPS, TABLE_BY_NAME


ALL_ASPECTS = ("keys", "enum", "required", "type", "range", "pattern")


def check_table(df: pd.DataFrame, table_name: str,
                aspects: list[str] | tuple[str, ...] | None = None) -> list[str]:
    """Validate one table. `aspects` limits the checks run (any of ALL_ASPECTS);
    None means every aspect."""
    on = set(ALL_ASPECTS if aspects is None else aspects)
    t = TABLE_BY_NAME[table_name]
    expected = [f.name for f in t.fields]
    if list(df.columns) != expected:
        return [f"{table_name}: columns {list(df.columns)} do not match schema {expected}"]

    problems: list[str] = []
    for f in t.fields:
        label = f"{table_name}.{f.name}"
        col = df[f.name]
        nulls = col.isna()
        if "required" in on and f.required and nulls.any():
            problems.append(f"{label}: {int(nulls.sum())} empty value(s) in a required field")
        vals = col[~nulls]
        if vals.empty:
            continue

        if f.dtype in ("int", "float"):
            num = pd.to_numeric(vals, errors="coerce")
            bad = num.isna()
            if f.dtype == "int":
                bad = bad | (num % 1 != 0)
            if "type" in on and bad.any():
                problems.append(f"{label}: {int(bad.sum())} value(s) are not {f.dtype}")
            num = num[~bad]
            if "range" in on and f.min_value is not None and (num < f.min_value).any():
                problems.append(f"{label}: value(s) below minimum {f.min_value:g}")
            if "range" in on and f.max_value is not None and (num > f.max_value).any():
                problems.append(f"{label}: value(s) above maximum {f.max_value:g}")
        elif f.dtype == "bool":
            if "type" in on and not vals.isin([True, False]).all():
                problems.append(f"{label}: non-boolean value(s)")
        elif f.dtype == "date":
            if "type" in on and pd.to_datetime(vals, format="%Y-%m-%d", errors="coerce").isna().any():
                problems.append(f"{label}: value(s) not in YYYY-MM-DD format")
        elif f.dtype == "datetime":
            if "type" in on and pd.to_datetime(vals, format="%Y-%m-%d %H:%M:%S", errors="coerce").isna().any():
                problems.append(f"{label}: value(s) not in YYYY-MM-DD HH:MM:SS format")

        if "enum" in on and f.allowed:
            bad = ~vals.isin(f.allowed)
            if bad.any():
                problems.append(f"{label}: {int(bad.sum())} value(s) outside allowed list")
        if "pattern" in on and f.pattern:
            bad = ~vals.astype(str).str.match(f.pattern)
            if bad.any():
                problems.append(f"{label}: {int(bad.sum())} value(s) break pattern {f.pattern}")
        if "keys" in on and f.unique and vals.duplicated().any():
            problems.append(f"{label}: duplicate value(s) in a unique field")

    if "keys" in on:
        if df.duplicated(subset=list(t.pk)).any():
            problems.append(f"{table_name}: duplicate primary key {t.pk}")
        for combo in t.unique_together:
            if df.duplicated(subset=list(combo)).any():
                problems.append(f"{table_name}: duplicate combination {combo}")
    return problems


def check_foreign_keys(tables: dict[str, pd.DataFrame]) -> list[str]:
    """Check every relationship whose parent and child tables are both present."""
    problems: list[str] = []
    for r in RELATIONSHIPS:
        if r.parent not in tables or r.child not in tables:
            continue
        parent_keys = set(tables[r.parent][list(r.parent_cols)].itertuples(index=False, name=None))
        child = tables[r.child][list(r.child_cols)]
        if not r.fk_required:
            child = child.dropna()
        missing = [k for k in child.itertuples(index=False, name=None) if k not in parent_keys]
        if missing:
            problems.append(
                f"{r.child}{r.child_cols} -> {r.parent}: {len(missing)} orphan row(s), e.g. {missing[0]}")
    return problems


def check_all(tables: dict[str, pd.DataFrame]) -> list[str]:
    problems: list[str] = []
    for name, df in tables.items():
        problems += check_table(df, name)
    return problems + check_foreign_keys(tables)