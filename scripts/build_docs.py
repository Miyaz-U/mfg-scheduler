"""Generate docs/data_dictionary.md, docs/data_dictionary.csv, docs/er_diagram.md."""
from pathlib import Path

import pandas as pd

from mfgsched.schema import (
    DECISIONS, GROUND_TRUTH, NM_RELATIONSHIPS, RELATIONSHIPS, TABLES,
    TRANSITIONS, foreign_keys,
)

DOCS = Path("docs")
FENCE = "`" * 3  # avoids writing a literal code fence inside this script


def constraint_text(f) -> str:
    parts = []
    if f.allowed:
        parts.append("one of: " + ", ".join(str(v) for v in f.allowed))
    if f.pattern:
        parts.append(f"pattern `{f.pattern}`")
    if f.min_value is not None:
        parts.append(f">= {f.min_value:g}")
    if f.max_value is not None:
        parts.append(f"<= {f.max_value:g}")
    return "; ".join(parts) or "-"


def table_md(t) -> list[str]:
    lines = [f"### {t.name}", "", f"*{t.kind}*: {t.description}", "",
             f"- **Primary key:** {', '.join(t.pk)}"]
    for r in foreign_keys(t.name):
        req = "required" if r.fk_required else "optional"
        lines.append(
            f"- **Foreign key:** ({', '.join(r.child_cols)}) references "
            f"{r.parent}({', '.join(r.parent_cols)}); cardinality 1 : {r.child_card}; {req}")
    for u in t.unique_together:
        lines.append(f"- **Unique together:** ({', '.join(u)})")
    for rule in t.rules:
        lines.append(f"- **Rule:** {rule}")
    lines += ["", "| Field | Type | Required | Constraint | Unit | Unique | Description |",
              "|---|---|---|---|---|---|---|"]
    for f in t.fields:
        desc = f.description + (f" **Derived:** {f.derived}" if f.derived else "")
        lines.append(
            f"| {f.name} | {f.dtype} | {'yes' if f.required else 'no'} | "
            f"{constraint_text(f)} | {f.unit or '-'} | {'yes' if f.unique else 'no'} | {desc} |")
    return lines + [""]


def build_markdown() -> str:
    out = ["# Data Dictionary", "",
           "Generated from `src/mfgsched/schema.py`. Do not edit by hand.", "",
           "## Design decisions", ""]
    out += [f"- **{k}**: {v}" for k, v in DECISIONS]
    out += ["", "## Tables", ""]
    for t in TABLES + (GROUND_TRUTH,):
        out += table_md(t)
    out += ["## Relationships", "",
            "| Parent | Child | Parent columns | Child (FK) columns | Cardinality | FK required |",
            "|---|---|---|---|---|---|"]
    for r in RELATIONSHIPS:
        out.append(f"| {r.parent} | {r.child} | {', '.join(r.parent_cols)} | "
                   f"{', '.join(r.child_cols)} | 1 : {r.child_card} | "
                   f"{'yes' if r.fk_required else 'no'} |")
    out += ["", "## Many-to-many relationships (resolved by bridge tables)", ""]
    out += [f"- {a} N:M {b} via **{bridge}**" for a, b, bridge in NM_RELATIONSHIPS]
    out += ["", "## Lifecycle transitions", ""]
    for (table, col), moves in TRANSITIONS.items():
        out.append(f"**{table}.{col}**")
        out.append("")
        for src, dsts in moves.items():
            out.append(f"- {src} -> {', '.join(dsts) if dsts else '(final)'}")
        out.append("")
    return "\n".join(out)


def build_er() -> str:
    right = {"1..N": "|{", "0..N": "o{", "0..1": "o|"}
    lines = [f"{FENCE}mermaid", "erDiagram"]
    for r in RELATIONSHIPS:
        left = "||" if r.fk_required else "|o"
        label = ",".join(r.child_cols)
        lines.append(f'    {r.parent} {left}--{right[r.child_card]} {r.child} : "{label}"')
    lines.append(FENCE)
    return "# Entity-Relationship Diagram\n\n" + "\n".join(lines) + "\n"


def build_csv() -> pd.DataFrame:
    rows = []
    for t in TABLES + (GROUND_TRUTH,):
        for f in t.fields:
            rows.append({
                "table": t.name, "field": f.name, "type": f.dtype,
                "required": f.required, "unique": f.unique,
                "constraint": constraint_text(f), "unit": f.unit,
                "derived": f.derived, "description": f.description,
                "is_primary_key": f.name in t.pk,
            })
    return pd.DataFrame(rows)


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    (DOCS / "data_dictionary.md").write_text(build_markdown(), encoding="utf-8")
    (DOCS / "er_diagram.md").write_text(build_er(), encoding="utf-8")
    df = build_csv()
    df.to_csv(DOCS / "data_dictionary.csv", index=False, encoding="utf-8")
    print(f"Tables (incl. GroundTruth): {len(TABLES) + 1}")
    print(f"Input tables              : {len(TABLES)}")
    print(f"Fields documented         : {len(df)}")
    print(f"Relationships (FKs)       : {len(RELATIONSHIPS)}")
    print("Wrote docs/data_dictionary.md, docs/data_dictionary.csv, docs/er_diagram.md")


if __name__ == "__main__":
    main()