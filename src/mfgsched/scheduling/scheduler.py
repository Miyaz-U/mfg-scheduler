"""Step 5: constructive scheduler (priority dispatching, earliest completion).

An operation is "ready" when its predecessor has finished (or its order is
released) and its materials have arrived. Ready operations are taken day by day
(urgent first, then earliest due date). Each goes to the eligible machine that
finishes it earliest. Times are integer minutes since the horizon start.
"""
from __future__ import annotations

import heapq
import math
from bisect import bisect_right, insort
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import make_frame

FMT = "%Y-%m-%d %H:%M:%S"
PLANNING_LAG_HOURS = 2          # decision D12
NEG_INF = -10**9


@dataclass
class MachineState:
    machine_id: str
    group: str
    speed: float
    starts: list                # slot start minutes
    ends: list                  # slot usable-end minutes (start + available_minutes)
    cal_ids: list
    flags: list                 # "ok", "unstaffed" or "no_capacity"
    free_at: int = NEG_INF      # machine is busy until this minute
    last_family: str | None = None
    last_index: int | None = None   # index of the previous placement on this machine


@dataclass
class Placement:
    operation_id: str
    machine_id: str
    calendar_id: str
    start: int
    end: int
    changeover: int
    run: int
    prev_index: int | None


def _minutes(epoch: datetime, series: pd.Series) -> pd.Series:
    delta = pd.to_datetime(series, format=FMT) - pd.Timestamp(epoch)
    return (delta.dt.total_seconds() // 60).fillna(NEG_INF).astype("int64")


def _ts(epoch: datetime, minutes: int) -> str:
    return (epoch + timedelta(minutes=int(minutes))).strftime(FMT)


def _find_slot(st: MachineState, ready: int, run: int, family: str, tool_list: list,
               co_lookup: dict, diag: dict):
    """Earliest feasible (start, slot index, changeover, duration) on one machine."""
    changeover = co_lookup[(st.group, st.last_family, family)] if st.last_family is not None else 0
    duration = changeover + run
    t_min = max(ready, st.free_at)
    for j in range(bisect_right(st.ends, t_min), len(st.starts)):
        flag = st.flags[j]
        if flag != "ok":
            diag[flag] += 1
            continue
        s, limit, blocked = max(t_min, st.starts[j]), st.ends[j], False
        while s + duration <= limit:
            clash = next((b for a, b in tool_list if a < s + duration and s < b), None)
            if clash is None:
                return s, j, changeover, duration
            blocked = True
            s = clash                      # wait until the shared tool is free
        diag["tool_blocked" if blocked else "too_short"] += 1
    return None


def schedule(tables: dict[str, pd.DataFrame], cfg: GeneratorConfig) -> dict[str, pd.DataFrame]:
    """Return a new table dict with the schedule added. Inputs are not modified."""
    epoch = datetime.combine(cfg.horizon_start, time(0, 0))
    horizon_end = cfg.horizon_days * 1440
    machines, cal = tables["Machine"], tables["MachineCalendar"]
    orders, ops, events = tables["ProductionOrder"], tables["Operation"], tables["OrderStatusEvent"]

    # ---------------- lookups ----------------
    changeover = {(r.machine_group, r.from_family, r.to_family): int(r.changeover_minutes)
                  for r in tables["ChangeoverMatrix"].itertuples(index=False)}
    family_of = dict(zip(tables["Product"]["product_id"], tables["Product"]["product_family"]))
    order_info = {}
    for r in orders.itertuples(index=False):
        order_info[r.order_id] = {
            "release": (date.fromisoformat(r.release_date) - cfg.horizon_start).days * 1440,
            "due": ((date.fromisoformat(r.due_date) - cfg.horizon_start).days + 1) * 1440,
            "priority": int(r.priority),
            "family": family_of[r.product_id],
        }
    created = events[events["from_status"].isna()]
    planned_ts = {oid: datetime.strptime(ts, FMT) + timedelta(hours=PLANNING_LAG_HOURS)
                  for oid, ts in zip(created["order_id"], created["event_ts"])}

    # machine timelines
    needed = dict(zip(machines["machine_id"], machines["operators_required"]))
    states = {m.machine_id: MachineState(m.machine_id, m.machine_group, float(m.speed_factor), [], [], [], [])
              for m in machines.itertuples(index=False)}
    cal = cal.copy()
    cal["start_min"] = _minutes(epoch, cal["shift_start"])
    for r in cal.sort_values(["machine_id", "start_min"]).itertuples(index=False):
        st = states[r.machine_id]
        if r.available_minutes == 0:
            flag = "no_capacity"
        elif r.operators_assigned < needed[r.machine_id]:
            flag = "unstaffed"
        else:
            flag = "ok"
        st.starts.append(int(r.start_min))
        st.ends.append(int(r.start_min + r.available_minutes))
        st.cal_ids.append(r.calendar_id)
        st.flags.append(flag)

    # eligible ACTIVE machines and shared tools per routing step
    active = set(machines.loc[machines["lifecycle_state"] == "ACTIVE", "machine_id"])
    eligible: dict[tuple, list] = {}
    for r in tables["RoutingStepMachine"].itertuples(index=False):
        if r.machine_id in active:
            eligible.setdefault((r.route_id, int(r.step_seq)), []).append((r.machine_id, bool(r.is_preferred)))
    tool_of = {(r.route_id, int(r.step_seq)): r.tool_group
               for r in tables["RoutingStep"].itertuples(index=False) if isinstance(r.tool_group, str)}

    # material ready time per operation = latest allocated lot availability
    req = tables["MaterialRequirement"].merge(
        tables["InventoryLot"][["lot_id", "available_at"]],
        left_on="allocated_lot_id", right_on="lot_id", how="left")
    req["avail_min"] = _minutes(epoch, req["available_at"])
    latest = req.sort_values(["operation_id", "avail_min", "lot_id"]).groupby("operation_id").tail(1)
    mat_ready = {r.operation_id: (int(r.avail_min), r.lot_id) for r in latest.itertuples(index=False)}

    op_list = ops.sort_values(["order_id", "step_seq"]).to_dict("records")
    by_order: dict[str, list[int]] = {}
    for i, o in enumerate(op_list):
        by_order.setdefault(o["order_id"], []).append(i)
    order_of_op = {o["operation_id"]: o["order_id"] for o in op_list}

    # ---------------- dispatching ----------------
    placements: list[Placement] = []
    placement_of: dict[int, int] = {}
    tool_busy: dict[str, list] = {}
    violations: list[dict] = []
    heap: list = []

    def ready_of(i: int, base: int) -> int:
        return max(base, mat_ready.get(op_list[i]["operation_id"], (NEG_INF, None))[0])

    def push(i: int, ready: int) -> None:
        info = order_info[op_list[i]["order_id"]]
        heapq.heappush(heap, (ready // 1440, info["priority"], info["due"], ready, i))

    for oid, idxs in by_order.items():
        push(idxs[0], ready_of(idxs[0], order_info[oid]["release"]))

    while heap:
        _, _, _, ready, i = heapq.heappop(heap)
        o = op_list[i]
        info = order_info[o["order_id"]]
        key = (o["route_id"], int(o["step_seq"]))
        tool = tool_of.get(key)
        tool_list = tool_busy.get(tool, []) if tool else []
        diag = {"unstaffed": 0, "no_capacity": 0, "too_short": 0, "tool_blocked": 0}

        best = None
        for machine_id, preferred in eligible.get(key, []):
            st = states[machine_id]
            run = math.ceil(o["standard_minutes"] / st.speed)
            hit = _find_slot(st, ready, run, info["family"], tool_list, changeover, diag)
            if hit is None:
                continue
            start, slot_j, co, duration = hit
            cand = (start + duration, 0 if preferred else 1, machine_id, start, slot_j, co, run)
            if best is None or cand[:3] < best[:3]:
                best = cand

        if best is not None:                                   # commit the placement
            end, _, machine_id, start, j, co, run = best
            st = states[machine_id]
            placements.append(Placement(o["operation_id"], machine_id, st.cal_ids[j],
                                        start, end, co, run, st.last_index))
            st.last_index = len(placements) - 1
            st.free_at, st.last_family = end, info["family"]
            if tool:
                insort(tool_busy.setdefault(tool, []), (start, end))
            placement_of[i] = len(placements) - 1
            nxt = i + 1
            if nxt < len(op_list) and op_list[nxt]["order_id"] == o["order_id"]:
                push(nxt, ready_of(nxt, end))
            continue

        # ---- infeasible: explain it, and block the rest of this order ----
        lot_min, lot_id = mat_ready.get(o["operation_id"], (NEG_INF, None))
        names = ", ".join(m for m, _ in eligible.get(key, [])) or "none"
        if lot_min >= horizon_end:
            vtype = "MATERIAL_NOT_AVAILABLE"
            text = (f"Allocated lot {lot_id} only becomes available at {_ts(epoch, lot_min)}, "
                    f"after the planning horizon ends ({_ts(epoch, horizon_end)}).")
        else:
            vtype = "NO_CAPACITY_IN_HORIZON"
            text = (f"No feasible slot on eligible machines ({names}) from {_ts(epoch, ready)} to the "
                    f"end of the horizon; slots skipped: {diag['unstaffed']} unstaffed, "
                    f"{diag['no_capacity']} without capacity, {diag['too_short']} too short, "
                    f"{diag['tool_blocked']} blocked by shared tool {tool or '-'}.")
        violations.append({"order_id": o["order_id"], "operation_id": o["operation_id"],
                           "schedule_id": None, "violation_type": vtype,
                           "severity": "HARD", "details": text})
        prev_id, k = o["operation_id"], i + 1
        while k < len(op_list) and op_list[k]["order_id"] == o["order_id"]:
            violations.append({"order_id": o["order_id"], "operation_id": op_list[k]["operation_id"],
                               "schedule_id": None, "violation_type": "PREDECESSOR_UNSCHEDULED",
                               "severity": "HARD",
                               "details": f"Predecessor {prev_id} could not be scheduled."})
            prev_id, k = op_list[k]["operation_id"], k + 1

    # ---------------- build output tables ----------------
    order_idx = sorted(range(len(placements)),
                       key=lambda k: (placements[k].start, placements[k].machine_id, placements[k].operation_id))
    sid = {k: ids.schedule_id(n, cfg.year) for n, k in enumerate(order_idx, start=1)}

    sched_rows, sev_rows = [], []
    for n, k in enumerate(order_idx, start=1):
        pl = placements[k]
        sched_rows.append({
            "schedule_id": sid[k], "operation_id": pl.operation_id, "machine_id": pl.machine_id,
            "calendar_id": pl.calendar_id, "planned_start": _ts(epoch, pl.start),
            "planned_end": _ts(epoch, pl.end),
            "previous_schedule_id": sid[pl.prev_index] if pl.prev_index is not None else None,
            "changeover_minutes": pl.changeover, "run_minutes": pl.run,
            "schedule_version": 1, "schedule_status": "PLANNED",
        })
        sev_rows.append({
            "event_id": ids.schedule_event_id(n, cfg.year), "schedule_id": sid[k],
            "from_status": None, "to_status": "PLANNED",
            "event_ts": planned_ts[order_of_op[pl.operation_id]].strftime(FMT),
            "reason": "Initial schedule",
        })

    planned_orders = []
    for oid, idxs in by_order.items():
        if not all(k in placement_of for k in idxs):
            continue
        planned_orders.append(oid)
        last = idxs[-1]
        end, due = placements[placement_of[last]].end, order_info[oid]["due"]
        if end > due:                                          # decision D11
            violations.append({
                "order_id": oid, "operation_id": op_list[last]["operation_id"],
                "schedule_id": sid[placement_of[last]], "violation_type": "LATE_DELIVERY",
                "severity": "SOFT",
                "details": (f"Order {oid} completes {_ts(epoch, end)} but is due by "
                            f"{_ts(epoch, due)}; late by {(end - due) / 60:.1f} hours."),
            })

    violations.sort(key=lambda v: (v["operation_id"], v["violation_type"]))
    vio_rows = [{
        "violation_id": ids.violation_id(n, cfg.year), "operation_id": v["operation_id"],
        "schedule_id": v["schedule_id"], "violation_type": v["violation_type"],
        "severity": v["severity"], "detected_at": planned_ts[v["order_id"]].strftime(FMT),
        "details": v["details"],
    } for n, v in enumerate(violations, start=1)]

    planned_orders.sort()
    n0 = len(events)
    new_event_rows = [{
        "event_id": ids.order_event_id(n0 + n, cfg.year), "order_id": oid,
        "from_status": "NEW", "to_status": "PLANNED",
        "event_ts": planned_ts[oid].strftime(FMT), "reason": "All operations scheduled",
    } for n, oid in enumerate(planned_orders, start=1)]

    new_orders = orders.copy()
    new_orders.loc[new_orders["order_id"].isin(planned_orders), "order_status"] = "PLANNED"
    scheduled_ops = {op_list[i]["operation_id"] for i in placement_of}
    new_ops = ops.copy()
    new_ops["operation_status"] = new_ops["operation_id"].map(
        lambda x: "SCHEDULED" if x in scheduled_ops else "BLOCKED")

    out = dict(tables)
    out["ProductionOrder"] = new_orders
    out["Operation"] = new_ops
    out["OrderStatusEvent"] = pd.concat([events, make_frame("OrderStatusEvent", new_event_rows)],
                                        ignore_index=True)
    out["OperationSchedule"] = make_frame("OperationSchedule", sched_rows)
    out["ScheduleEvent"] = make_frame("ScheduleEvent", sev_rows)
    out["ConstraintViolation"] = make_frame("ConstraintViolation", vio_rows)
    return out