"""Inject labelled edge cases into a valid baseline schedule.

Every case changes exactly ONE record. Boundary cases (margin 0) are valid rows
labelled ACCEPT, so we can also measure false alarms. After injecting, the
independent checker must find exactly the labelled violations, nothing else.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import get_rng, make_frame
from mfgsched.schema import BOUNDARY_ISSUE_TYPES, INJECTED_ISSUE_TYPES
from mfgsched.validation.schedule_checks import find_schedule_violations

FMT = "%Y-%m-%d %H:%M:%S"
NEG = -10**9
MAX_OVERLAP_MIN = 20       # largest overlap created
MAX_MARGIN_MIN = 30        # largest violation margin (precedence, material)
MAX_SHIFT_MARGIN_MIN = 15  # largest overrun past the shift end
SELECTION_ORDER = (
    "OUTSIDE_SHIFT_CALENDAR", "PRECEDENCE_VIOLATED", "MACHINE_OVERLAP",
    "MATERIAL_NOT_AVAILABLE", "CHANGEOVER_OMITTED",
    "BOUNDARY_ENDS_AT_SHIFT_END", "BOUNDARY_STARTS_AT_PREDECESSOR_END",
    "BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE",
)


class AnomalyInjectionError(RuntimeError):
    pass


@dataclass
class Row:
    i: int
    schedule_id: str
    operation_id: str
    machine_id: str
    calendar_id: str
    start: int
    end: int
    changeover: int
    run: int
    slot_start: int
    usable_end: int
    release: int
    tool: str | None
    lots: list                 # [(lot_id, available_minute, users_of_lot)]
    pred_op: str | None
    pred_row: int | None = None
    succ_row: int | None = None
    prev_row: int | None = None    # previous job on the same machine
    next_row: int | None = None    # next job on the same machine


def _mins(epoch: datetime, series: pd.Series) -> pd.Series:
    delta = pd.to_datetime(series, format=FMT) - pd.Timestamp(epoch)
    return (delta.dt.total_seconds() // 60).astype("int64")


def _ts(epoch: datetime, minutes: int) -> str:
    return (epoch + timedelta(minutes=int(minutes))).strftime(FMT)


def _pick(lo: int, hi: int, salt: int) -> int:
    """Deterministic 'random-looking' number in [lo, hi] from a row index."""
    return lo + (salt * 7919) % (hi - lo + 1)


# ---------------------------------------------------------------------------
def _build_rows(t: dict[str, pd.DataFrame], cfg: GeneratorConfig, epoch: datetime) -> list[Row]:
    sched = t["OperationSchedule"]
    sched = sched[sched["schedule_status"] == "PLANNED"]
    sched = sched.assign(start=_mins(epoch, sched["planned_start"]),
                         end=_mins(epoch, sched["planned_end"]))
    sched = sched.sort_values(["machine_id", "start", "schedule_id"]).reset_index(drop=True)

    cal = t["MachineCalendar"].copy()
    cal["slot_start"] = _mins(epoch, cal["shift_start"])
    slot = {r.calendar_id: (int(r.slot_start), int(r.slot_start + r.available_minutes))
            for r in cal.itertuples(index=False)}

    op_info = {r.operation_id: (r.order_id,
                                None if pd.isna(r.predecessor_operation_id) else r.predecessor_operation_id,
                                r.route_id, int(r.step_seq))
               for r in t["Operation"].itertuples(index=False)}
    succ_of = {pred: op for op, (_, pred, _, _) in op_info.items() if pred is not None}
    release = {r.order_id: (date.fromisoformat(r.release_date) - cfg.horizon_start).days * 1440
               for r in t["ProductionOrder"].itertuples(index=False)}
    tool_of = {(r.route_id, int(r.step_seq)): r.tool_group
               for r in t["RoutingStep"].itertuples(index=False) if isinstance(r.tool_group, str)}

    req = t["MaterialRequirement"].merge(t["InventoryLot"][["lot_id", "available_at"]],
                                         left_on="allocated_lot_id", right_on="lot_id", how="left")
    req["avail"] = _mins(epoch, req["available_at"])
    users = req.groupby("allocated_lot_id").size().to_dict()
    lots_of: dict[str, list] = {}
    for r in req.itertuples(index=False):
        lots_of.setdefault(r.operation_id, []).append(
            (r.allocated_lot_id, int(r.avail), int(users[r.allocated_lot_id])))

    rows: list[Row] = []
    for i, r in enumerate(sched.itertuples(index=False)):
        order_id, pred, route_id, step = op_info[r.operation_id]
        s0, s1 = slot[r.calendar_id]
        rows.append(Row(i=i, schedule_id=r.schedule_id, operation_id=r.operation_id,
                        machine_id=r.machine_id, calendar_id=r.calendar_id,
                        start=int(r.start), end=int(r.end),
                        changeover=int(r.changeover_minutes), run=int(r.run_minutes),
                        slot_start=s0, usable_end=s1, release=release[order_id],
                        tool=tool_of.get((route_id, step)),
                        lots=lots_of.get(r.operation_id, []), pred_op=pred))
    row_of_op = {r.operation_id: r.i for r in rows}
    for r in rows:
        r.pred_row = row_of_op.get(r.pred_op) if r.pred_op else None
        succ = succ_of.get(r.operation_id)
        r.succ_row = row_of_op.get(succ) if succ else None
    for a, b in zip(rows, rows[1:]):
        if a.machine_id == b.machine_id:
            a.next_row, b.prev_row = b.i, a.i
    return rows


def _lower_bound(b: Row, rows: list[Row], with_pred=False, with_prev=False) -> int:
    """Earliest minute at which row b could start without breaking another rule."""
    vals = [b.release, b.slot_start] + [a for _, a, _ in b.lots]
    if with_pred and b.pred_row is not None:
        vals.append(rows[b.pred_row].end)
    if with_prev and b.prev_row is not None:
        vals.append(rows[b.prev_row].end)
    return max(vals)


def _plan(b: Row, issue: str, new_start: int, k: int, new_end: int | None = None, **extra) -> dict:
    return {"i": b.i, "issue_type": issue, "new_start": new_start, "k": k,
            "new_end": new_end if new_end is not None else new_start + (b.end - b.start), **extra}


# ---------------------------------------------------------------------------
# Candidate generators: each returns plans that break (or, with margin 0, only
# touch) exactly one rule.
# ---------------------------------------------------------------------------
def _cand_overlap(rows):
    out = []
    for b in rows:
        if b.tool or b.prev_row is None:
            continue
        a = rows[b.prev_row]
        if a.calendar_id != b.calendar_id or a.end - a.start < 2:
            continue
        k = _pick(1, min(MAX_OVERLAP_MIN, a.end - a.start - 1), b.i)
        s_new = a.end - k
        if s_new >= _lower_bound(b, rows, with_pred=True):
            out.append(_plan(b, "MACHINE_OVERLAP", s_new, k))
    return out


def _cand_precedence(rows, boundary: bool):
    out = []
    for b in rows:
        if b.tool or b.pred_row is None:
            continue
        p_end = rows[b.pred_row].end
        room = p_end - _lower_bound(b, rows, with_prev=True)
        if room < (0 if boundary else 1):
            continue
        k = 0 if boundary else _pick(1, min(MAX_MARGIN_MIN, room), b.i)
        out.append(_plan(b, "BOUNDARY_STARTS_AT_PREDECESSOR_END" if boundary
                         else "PRECEDENCE_VIOLATED", p_end - k, k))
    return out


def _cand_material(rows, boundary: bool):
    out = []
    for b in rows:
        single = [lot_id for lot_id, _, users in b.lots if users == 1]
        if not single:
            continue
        k = 0 if boundary else _pick(1, MAX_MARGIN_MIN, b.i)
        out.append({"i": b.i, "k": k, "lot": (single[0], b.start + k),
                    "issue_type": "BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE" if boundary
                    else "MATERIAL_NOT_AVAILABLE"})
    return out


def _cand_outside(rows, boundary: bool):
    out = []
    for b in rows:
        if b.tool:
            continue
        k = 0 if boundary else _pick(1, MAX_SHIFT_MARGIN_MIN, b.i)
        end_new = b.usable_end + k
        nxt = rows[b.next_row].start if b.next_row is not None else None
        suc = rows[b.succ_row].start if b.succ_row is not None else None
        if (nxt is not None and nxt < end_new) or (suc is not None and suc < end_new):
            continue
        start_new = end_new - (b.end - b.start)
        if start_new >= b.start:
            out.append(_plan(b, "BOUNDARY_ENDS_AT_SHIFT_END" if boundary
                             else "OUTSIDE_SHIFT_CALENDAR", start_new, k))
    return out


def _cand_changeover(rows):
    return [_plan(b, "CHANGEOVER_OMITTED", b.start, b.changeover,
                  new_end=b.end - b.changeover, new_changeover=0)
            for b in rows if b.changeover > 0]


# ---------------------------------------------------------------------------
def _neighbourhood(rows: list[Row], i: int) -> set[int]:
    r = rows[i]
    return {x for x in (i, r.prev_row, r.next_row, r.pred_row, r.succ_row) if x is not None}


def _select(cands: list[dict], n: int, used: set[int], rows: list[Row], rng) -> list[dict]:
    chosen: list[dict] = []
    for idx in rng.permutation(len(cands)):
        plan = cands[int(idx)]
        if plan["i"] in used:
            continue
        chosen.append(plan)
        used.update(_neighbourhood(rows, plan["i"]))
        if len(chosen) == n:
            break
    return chosen


def _describe(p: dict, rows: list[Row], epoch: datetime) -> str:
    b, k, issue = rows[p["i"]], p["k"], p["issue_type"]
    ts = lambda m: _ts(epoch, m)  # noqa: E731
    if issue == "MACHINE_OVERLAP":
        a = rows[b.prev_row]
        return (f"Moved {b.schedule_id} from {ts(b.start)} to {ts(p['new_start'])}; "
                f"it now overlaps {a.schedule_id} by {k} min on {b.machine_id}.")
    if issue == "PRECEDENCE_VIOLATED":
        return (f"Moved {b.schedule_id} to start at {ts(p['new_start'])}, "
                f"{k} min before predecessor {b.pred_op} finishes.")
    if issue == "MATERIAL_NOT_AVAILABLE":
        return (f"Moved availability of lot {p['lot'][0]} to {ts(p['lot'][1])}, "
                f"{k} min after {b.schedule_id} starts.")
    if issue == "OUTSIDE_SHIFT_CALENDAR":
        return (f"Moved {b.schedule_id} to end at {ts(p['new_end'])}, "
                f"{k} min after the usable end of {b.calendar_id}.")
    if issue == "CHANGEOVER_OMITTED":
        return (f"Removed the {k}-minute changeover from {b.schedule_id}; the previous job on "
                f"{b.machine_id} is a different product family.")
    if issue == "BOUNDARY_STARTS_AT_PREDECESSOR_END":
        return f"Valid: {b.schedule_id} starts exactly when predecessor {b.pred_op} finishes ({ts(p['new_start'])})."
    if issue == "BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE":
        return f"Valid: {b.schedule_id} starts exactly when lot {p['lot'][0]} becomes available ({ts(p['lot'][1])})."
    return f"Valid: {b.schedule_id} ends exactly at the usable end of {b.calendar_id} ({ts(p['new_end'])})."


def _apply(tables, cfg, plans, rows, epoch) -> dict[str, pd.DataFrame]:
    out = dict(tables)
    sched, lots = tables["OperationSchedule"].copy(), tables["InventoryLot"].copy()
    s_pos = {sid: ix for ix, sid in zip(sched.index, sched["schedule_id"])}
    l_pos = {lid: ix for ix, lid in zip(lots.index, lots["lot_id"])}
    midnight = pd.Timestamp(cfg.horizon_start)
    for p in plans:
        b = rows[p["i"]]
        if "lot" in p:
            lot_id, new_avail = p["lot"]
            ix = l_pos[lot_id]
            lots.at[ix, "available_at"] = _ts(epoch, new_avail)
            exp = pd.Timestamp(lots.at[ix, "expected_available_at"])
            av = pd.Timestamp(lots.at[ix, "available_at"])
            delay = max(0, (av - exp).days)                   # recompute derived lot fields
            lots.at[ix, "delay_days"] = delay
            lots.at[ix, "is_delayed"] = delay > 0
            lots.at[ix, "lot_status"] = ("EXHAUSTED" if lots.at[ix, "quantity_remaining"] == 0
                                         else "IN_TRANSIT" if av > midnight else "AVAILABLE")
        else:
            ix = s_pos[b.schedule_id]
            sched.at[ix, "planned_start"] = _ts(epoch, p["new_start"])
            sched.at[ix, "planned_end"] = _ts(epoch, p["new_end"])
            if "new_changeover" in p:
                sched.at[ix, "changeover_minutes"] = p["new_changeover"]
    out["OperationSchedule"], out["InventoryLot"] = sched, lots
    return out


def inject_anomalies(tables: dict[str, pd.DataFrame], cfg: GeneratorConfig
                     ) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Return (tables with anomalies, ground truth). The input tables are not modified."""
    epoch = datetime.combine(cfg.horizon_start, time(0, 0))
    rows = _build_rows(tables, cfg, epoch)
    rng = get_rng(cfg.seed, "anomaly")

    candidates = {
        "OUTSIDE_SHIFT_CALENDAR": _cand_outside(rows, False),
        "PRECEDENCE_VIOLATED": _cand_precedence(rows, False),
        "MACHINE_OVERLAP": _cand_overlap(rows),
        "MATERIAL_NOT_AVAILABLE": _cand_material(rows, False),
        "CHANGEOVER_OMITTED": _cand_changeover(rows),
        "BOUNDARY_ENDS_AT_SHIFT_END": _cand_outside(rows, True),
        "BOUNDARY_STARTS_AT_PREDECESSOR_END": _cand_precedence(rows, True),
        "BOUNDARY_STARTS_AT_MATERIAL_AVAILABLE": _cand_material(rows, True),
    }
    used: set[int] = set()
    plans: list[dict] = []
    for issue in SELECTION_ORDER:
        plans += _select(candidates[issue], cfg.n_anomalies_per_type, used, rows, rng)

    out = _apply(tables, cfg, plans, rows, epoch)

    # Verify: the independent checker must find exactly the labelled violations.
    expected = {(rows[p["i"]].schedule_id, p["issue_type"]) for p in plans
                if p["issue_type"] in INJECTED_ISSUE_TYPES}
    found = find_schedule_violations(out, cfg)
    detected = set(zip(found["schedule_id"], found["violation_type"]))
    if detected != expected:
        raise AnomalyInjectionError(
            f"labels and checker disagree: missed={sorted(expected - detected)[:5]}, "
            f"unexpected={sorted(detected - expected)[:5]}")

    rank = {t: n for n, t in enumerate(INJECTED_ISSUE_TYPES + BOUNDARY_ISSUE_TYPES)}
    ordered = sorted(plans, key=lambda p: (rank[p["issue_type"]], rows[p["i"]].schedule_id))
    gt_rows = []
    for n, p in enumerate(ordered, start=1):
        injected = p["issue_type"] in INJECTED_ISSUE_TYPES
        gt_rows.append({
            "case_id": ids.ground_truth_id(n, cfg.year), "issue_type": p["issue_type"],
            "entity_type": "OperationSchedule", "entity_id": rows[p["i"]].schedule_id,
            "ground_truth": "INJECTED_VIOLATION" if injected else "VALID_BOUNDARY",
            "expected_decision": "FLAG_AND_REPORT" if injected else "ACCEPT",
            "description": _describe(p, rows, epoch),
        })
    return out, make_frame("GroundTruth", gt_rows)