"""The validation suite.

Every check takes (tables, cfg, ground_truth) and returns a list of problem
messages; an empty list means PASS. A check may raise Skipped. Checks are grouped
under the categories named in the problem statement.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.evaluation.detection import evaluate_detection
from mfgsched.schema import BOUNDARY_ISSUE_TYPES, INJECTED_ISSUE_TYPES, TABLES, TRANSITIONS
from mfgsched.validation.master_checks import check_master_rules
from mfgsched.validation.schedule_checks import check_schedule_integrity, find_schedule_violations
from mfgsched.validation.schema_checks import check_foreign_keys, check_table
from mfgsched.validation.transaction_checks import check_transaction_rules

FMT = "%Y-%m-%d %H:%M:%S"
LABEL_COLUMNS = {"issue_type", "ground_truth", "expected_decision", "case_id"}


class Skipped(Exception):
    """Raised by a check that does not apply to this dataset."""


def _per_table(t, aspects) -> list[str]:
    out: list[str] = []
    for tbl in TABLES:
        if tbl.name in t:
            out += check_table(t[tbl.name], tbl.name, aspects)
    return out


def _need_ground_truth(gt) -> None:
    if gt is None:
        raise Skipped("no ground truth supplied (clean dataset)")


# ---------------------------------------------------------------------------
def chk_keys(t, cfg, gt):
    return _per_table(t, ["keys"])


def chk_foreign_keys(t, cfg, gt):
    return check_foreign_keys(t)


def chk_enums(t, cfg, gt):
    return _per_table(t, ["enum"])


def chk_fields(t, cfg, gt):
    return _per_table(t, ["required", "type", "range", "pattern"])


DATE_PAIRS = (  # (table, first column, second column, must be strictly after)
    ("Product", "effective_from", "effective_to", False),
    ("Routing", "effective_from", "effective_to", False),
    ("Machine", "commissioned_on", "effective_to", False),
    ("MachineCalendar", "shift_start", "shift_end", True),
    ("InventoryLot", "expected_available_at", "available_at", False),
    ("ProductionOrder", "release_date", "due_date", False),
    ("OperationSchedule", "planned_start", "planned_end", True),
)


def chk_dates(t, cfg, gt):
    p: list[str] = []
    for table, first, second, strict in DATE_PAIRS:
        df = t[table][[first, second]].dropna()
        bad = (df[first] >= df[second]) if strict else (df[first] > df[second])
        if bad.any():
            p.append(f"{table}: {int(bad.sum())} row(s) where {second} is "
                     f"{'not after' if strict else 'before'} {first}")
    first_day = cfg.horizon_start.isoformat()
    last_day = (cfg.horizon_start + timedelta(days=cfg.horizon_days - 1)).isoformat()
    orders = t["ProductionOrder"]
    if ((orders["release_date"] < first_day) | (orders["due_date"] > last_day)).any():
        p.append("ProductionOrder: release or due date outside the planning horizon")
    sched = t["OperationSchedule"]
    limit = (cfg.horizon_start + timedelta(days=cfg.horizon_days)).isoformat() + " 06:00:00"
    if (sched["planned_start"] < first_day + " 00:00:00").any() or (sched["planned_end"] > limit).any():
        p.append("OperationSchedule: a planned window lies outside the planning horizon")
    ev = t["OrderStatusEvent"]
    created = ev[ev["from_status"].isna()].merge(orders[["order_id", "release_date"]], on="order_id")
    if (created["event_ts"] >= created["release_date"] + " 00:00:00").any():
        p.append("OrderStatusEvent: an order was created on or after its release date")
    return p


def chk_derived(t, cfg, gt):
    return check_transaction_rules(t, cfg)


def chk_totals(t, cfg, gt):
    p: list[str] = []
    req, lots, sched = t["MaterialRequirement"], t["InventoryLot"], t["OperationSchedule"]
    ops, machines = t["Operation"], t["Machine"]
    tol = 0.001 * max(len(lots), 1) + 0.01
    if abs(req["required_qty"].sum() - lots["quantity_allocated"].sum()) > tol:
        p.append("total required quantity != total allocated quantity across lots")
    if abs(lots["quantity_received"].sum() - lots["quantity_allocated"].sum()
           - lots["quantity_remaining"].sum()) > tol:
        p.append("lots: received - allocated != remaining")
    minutes = ((pd.to_datetime(sched["planned_end"], format=FMT)
                - pd.to_datetime(sched["planned_start"], format=FMT)).dt.total_seconds() / 60).round().astype(int)
    if (minutes != sched["changeover_minutes"] + sched["run_minutes"]).any():
        p.append("OperationSchedule: duration != changeover + run")
    m = (sched.merge(ops[["operation_id", "standard_minutes"]], on="operation_id")
              .merge(machines[["machine_id", "speed_factor"]], on="machine_id"))
    if (m["run_minutes"] != np.ceil(m["standard_minutes"] / m["speed_factor"]).astype(int)).any():
        p.append("OperationSchedule: run_minutes != ceil(standard_minutes / speed_factor)")
    planned = (sched["schedule_status"] == "PLANNED").sum()
    if planned + (ops["operation_status"] == "BLOCKED").sum() != len(ops):
        p.append("scheduled + blocked operations != total operations")
    return p


LIFECYCLES = (  # (event table, key, entity table, status column)
    ("OrderStatusEvent", "order_id", "ProductionOrder", "order_status"),
    ("ScheduleEvent", "schedule_id", "OperationSchedule", "schedule_status"),
)


def chk_lifecycle(t, cfg, gt):
    p: list[str] = []
    for ev_name, key, ent_name, status_col in LIFECYCLES:
        allowed = TRANSITIONS[(ent_name, status_col)]
        initial = next(iter(allowed))
        ev = t[ev_name].sort_values([key, "event_ts", "event_id"])
        entities = t[ent_name]
        missing = set(entities[key]) - set(ev[key])
        if missing:
            p.append(f"{ev_name}: {len(missing)} {ent_name} row(s) have no history")
        for k, g in ev.groupby(key):
            prev = None
            for r in g.itertuples(index=False):
                frm = None if pd.isna(r.from_status) else r.from_status
                if frm != prev:
                    p.append(f"{ev_name}: history of {k} is not an unbroken chain")
                    break
                if frm is None and r.to_status != initial:
                    p.append(f"{ev_name}: {k} starts in {r.to_status}, expected {initial}")
                    break
                if frm is not None and r.to_status not in allowed.get(frm, ()):
                    p.append(f"{ev_name}: {k} moves {frm} -> {r.to_status}, which is not allowed")
                    break
                prev = r.to_status
        last = ev.groupby(key).tail(1).set_index(key)["to_status"]
        current = entities.set_index(key)[status_col]
        mismatch = int((current != last.reindex(current.index)).sum())
        if mismatch:
            p.append(f"{ent_name}: {mismatch} row(s) whose {status_col} differs from the last event")
    return p


def chk_duplicates(t, cfg, gt):
    p: list[str] = []
    for tbl in TABLES:
        n = int(t[tbl.name].duplicated().sum())
        if n:
            p.append(f"{tbl.name}: {n} fully duplicated row(s)")
    sched = t["OperationSchedule"]
    if sched.loc[sched["schedule_status"] == "PLANNED", "operation_id"].duplicated().any():
        p.append("OperationSchedule: an operation has more than one PLANNED row")
    for table, col in (("Product", "product_name"), ("Machine", "machine_name"),
                       ("Material", "material_name")):
        if t[table][col].duplicated().any():
            p.append(f"{table}: duplicate {col}")
    for name, cols in (("OrderStatusEvent", ["order_id", "from_status", "to_status"]),
                       ("ScheduleEvent", ["schedule_id", "from_status", "to_status"])):
        if t[name].duplicated(subset=cols).any():
            p.append(f"{name}: the same status change is recorded twice")
    return p


def chk_master_rules(t, cfg, gt):
    return check_master_rules(t)


def chk_schedule_integrity(t, cfg, gt):
    return check_schedule_integrity(t, cfg)


def chk_hard_constraints(t, cfg, gt):
    if gt is None:      # clean data: there must be no violations at all
        v = find_schedule_violations(t, cfg)
        return [f"{k}: {n} violation(s), e.g. {v.loc[v['violation_type'] == k, 'schedule_id'].iloc[0]}"
                for k, n in v["violation_type"].value_counts().items()]
    ev = evaluate_detection(t, gt, cfg)   # injected data: exactly the labelled cases
    p: list[str] = []
    missed = ev["injected_cases"] - ev["detected_cases"]
    if missed:
        p.append(f"{missed} injected case(s) not detected, e.g. {ev['missed'][0]}")
    if ev["false_positives"]:
        p.append(f"{ev['false_positives']} violation(s) are not in the ground truth, "
                 f"e.g. {ev['false_alarm_examples'][0]}")
    if ev["boundary_false_alarms"]:
        p.append(f"{ev['boundary_false_alarms']} valid boundary case(s) were flagged")
    return p


def chk_volumes(t, cfg, gt):
    wanted = (("Product", cfg.n_skus), ("Routing", cfg.n_skus), ("Machine", cfg.n_machines),
              ("ProductionOrder", cfg.n_orders), ("Operation", cfg.n_operations),
              ("Material", cfg.n_materials))
    return [f"{name}: {len(t[name])} rows, expected {n}" for name, n in wanted if len(t[name]) != n]


def chk_scenarios(t, cfg, gt):
    if cfg.n_orders < 500:
        raise Skipped("small scale: scenario coverage is only enforced at full scale")
    cal = t["MachineCalendar"].merge(
        t["Machine"][["machine_id", "operators_required", "lifecycle_state"]], on="machine_id")
    vio = t["ConstraintViolation"]
    wanted = {
        "machine downtime": (cal["downtime_minutes"] > 0).any(),
        "machine in MAINTENANCE": (t["Machine"]["lifecycle_state"] == "MAINTENANCE").any(),
        "understaffed shift": ((cal["operators_assigned"] < cal["operators_required"])
                               & (cal["lifecycle_state"] == "ACTIVE")).any(),
        "urgent orders": (t["ProductionOrder"]["priority"] == 1).any(),
        "shared tooling": t["RoutingStep"]["tool_group"].notna().any(),
        "delayed materials": t["InventoryLot"]["is_delayed"].any(),
        "exception path (ConstraintViolation rows)": len(vio) > 0,
        "late order (LATE_DELIVERY)": (vio["violation_type"] == "LATE_DELIVERY").any(),
    }
    if gt is not None:
        for issue in INJECTED_ISSUE_TYPES + BOUNDARY_ISSUE_TYPES:
            wanted[f"labelled case {issue}"] = (gt["issue_type"] == issue).any()
    return [f"missing scenario: {name}" for name, ok in wanted.items() if not bool(ok)]


VARIATION = (  # (table, column, minimum distinct values)
    ("Product", "product_family", 3), ("RoutingStep", "op_type", 3),
    ("Machine", "speed_factor", 3), ("Material", "material_category", 3),
    ("MachineCalendar", "downtime_reason", 2), ("MachineCalendar", "shift_code", 2),
    ("ProductionOrder", "priority", 2), ("ProductionOrder", "quantity", 5),
    ("Operation", "standard_minutes", 20), ("ChangeoverMatrix", "changeover_minutes", 5),
    ("InventoryLot", "quantity_received", 5),
)


def chk_variation(t, cfg, gt):
    return [f"{table}.{col}: only {t[table][col].nunique()} distinct value(s), expected at least {need}"
            for table, col, need in VARIATION if t[table][col].nunique() < need]


def chk_gt_schema(t, cfg, gt):
    _need_ground_truth(gt)
    return check_table(gt, "GroundTruth")


def chk_gt_separation(t, cfg, gt):
    _need_ground_truth(gt)
    p = ["GroundTruth is included among the input tables"] if "GroundTruth" in t else []
    for name, df in t.items():
        leaked = LABEL_COLUMNS & set(df.columns)
        if leaked:
            p.append(f"{name}: label column(s) {sorted(leaked)} found in an input table")
    return p


def chk_gt_references(t, cfg, gt):
    _need_ground_truth(gt)
    p: list[str] = []
    if not set(gt["entity_id"]) <= set(t["OperationSchedule"]["schedule_id"]):
        p.append("a label points at a schedule_id that does not exist")
    if gt["entity_id"].duplicated().any():
        p.append("a schedule row is labelled more than once")
    if list(gt["case_id"]) != [ids.ground_truth_id(i, cfg.year) for i in range(1, len(gt) + 1)]:
        p.append("case_ids are not sequential")
    injected = gt["ground_truth"] == "INJECTED_VIOLATION"
    if (injected != (gt["expected_decision"] == "FLAG_AND_REPORT")).any():
        p.append("label and expected_decision disagree")
    if (~gt.loc[injected, "issue_type"].isin(INJECTED_ISSUE_TYPES)).any() or \
            (~gt.loc[~injected, "issue_type"].isin(BOUNDARY_ISSUE_TYPES)).any():
        p.append("issue_type does not belong to its label")
    return p


# ---------------------------------------------------------------------------
@dataclass
class Check:
    id: str
    category: str
    name: str
    fn: Callable


@dataclass
class Result:
    check_id: str
    category: str
    name: str
    status: str                       # PASS, FAIL or SKIPPED
    issues: int = 0
    examples: list = field(default_factory=list)
    detail: str = ""


CHECKS = (
    Check("keys", "Primary-key uniqueness", "Primary keys and unique fields have no duplicates", chk_keys),
    Check("foreign_keys", "Foreign-key integrity", "Every foreign key resolves to a parent row", chk_foreign_keys),
    Check("enums", "Allowed enums", "Enum fields hold only allowed values", chk_enums),
    Check("fields", "Field validity", "Required fields filled; types, ranges and ID patterns hold", chk_fields),
    Check("dates", "Date ordering", "Dates and timestamps are in a valid order and inside the horizon", chk_dates),
    Check("derived", "Arithmetic reconciliation", "Derived values recompute correctly from source records", chk_derived),
    Check("totals", "Arithmetic reconciliation", "Totals reconcile across tables", chk_totals),
    Check("lifecycle", "Lifecycle transitions", "Status histories follow allowed transitions", chk_lifecycle),
    Check("duplicates", "Duplicates", "No duplicate rows or duplicate business records", chk_duplicates),
    Check("master_rules", "Business rules", "Master-data rules (routings, eligibility, changeovers)", chk_master_rules),
    Check("schedule_integrity", "Business rules", "Schedule integrity (IDs, statuses, durations, histories)", chk_schedule_integrity),
    Check("hard_constraints", "Schedule hard constraints", "Hard constraints hold (or match the labelled anomalies)", chk_hard_constraints),
    Check("volumes", "Coverage and variation", "Row counts match the configured volumes", chk_volumes),
    Check("scenarios", "Coverage and variation", "Required scenarios and edge cases are present", chk_scenarios),
    Check("variation", "Coverage and variation", "Distributions show meaningful variation", chk_variation),
    Check("gt_schema", "Ground-truth separation", "Ground truth conforms to its schema", chk_gt_schema),
    Check("gt_separation", "Ground-truth separation", "Ground truth is not leaked into input tables", chk_gt_separation),
    Check("gt_references", "Ground-truth separation", "Labels reference real rows and are consistent", chk_gt_references),
)


def run_suite(tables: dict[str, pd.DataFrame], cfg: GeneratorConfig,
              ground_truth: pd.DataFrame | None = None, only: set[str] | None = None) -> list[Result]:
    results: list[Result] = []
    for c in CHECKS:
        if only is not None and c.id not in only:
            continue
        try:
            issues = c.fn(tables, cfg, ground_truth)
            results.append(Result(c.id, c.category, c.name, "FAIL" if issues else "PASS",
                                  len(issues), issues[:3]))
        except Skipped as why:
            results.append(Result(c.id, c.category, c.name, "SKIPPED", detail=str(why)))
        except Exception as err:  # a crash means the data is badly broken
            results.append(Result(c.id, c.category, c.name, "FAIL", 1,
                                  [f"check crashed: {type(err).__name__}: {err}"]))
    return results


def summarize(results: list[Result]) -> dict:
    counts = {s: sum(r.status == s for r in results) for s in ("PASS", "FAIL", "SKIPPED")}
    return {"overall": "PASS" if counts["FAIL"] == 0 else "FAIL", "checks": len(results), **counts}


def render_markdown(results: list[Result], scale: str, dataset: str, cfg: GeneratorConfig) -> str:
    s = summarize(results)
    lines = [f"# Validation report: {scale} scale, {dataset} dataset", "",
             f"Seed {cfg.seed}; horizon {cfg.horizon_start.isoformat()} for {cfg.horizon_days} days.", "",
             f"**Overall: {s['overall']}**  ({s['PASS']} passed, {s['FAIL']} failed, {s['SKIPPED']} skipped)", "",
             "| Category | Check | Status | Issues |", "|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r.category} | {r.name} | {r.status} | {r.issues} |")
    skipped = [r for r in results if r.status == "SKIPPED"]
    if skipped:
        lines += ["", "## Skipped checks", ""] + [f"- {r.name}: {r.detail}" for r in skipped]
    failed = [r for r in results if r.status == "FAIL"]
    if failed:
        lines += ["", "## Failures", ""]
        for r in failed:
            lines += [f"### {r.name}"] + [f"- {e}" for e in r.examples] + [""]
    return "\n".join(lines) + "\n"


def write_reports(results: list[Result], out_dir, scale: str, dataset: str,
                  cfg: GeneratorConfig) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"validation_{scale}_{dataset}"
    md, js = out / f"{stem}.md", out / f"{stem}.json"
    md.write_text(render_markdown(results, scale, dataset, cfg), encoding="utf-8")
    js.write_text(json.dumps({"scale": scale, "dataset": dataset, "seed": cfg.seed,
                              "summary": summarize(results),
                              "checks": [asdict(r) for r in results]}, indent=2), encoding="utf-8")
    return [md, js]