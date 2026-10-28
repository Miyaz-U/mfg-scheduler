"""Independent verification of a schedule.

find_schedule_violations(): re-derives every hard constraint from the tables
alone (it does not trust the scheduler) and returns typed violations.
check_schedule_integrity(): checks derived values, IDs, statuses and histories.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.schema import TRANSITIONS

FMT = "%Y-%m-%d %H:%M:%S"
COLUMNS = ["violation_type", "schedule_id", "operation_id", "details"]


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format=FMT)


def _records(frame: pd.DataFrame, vtype: str, text) -> list[dict]:
    return [{"violation_type": vtype, "schedule_id": r.schedule_id,
             "operation_id": r.operation_id, "details": text(r)}
            for r in frame.itertuples(index=False)]


def _planned_frame(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    sched = t["OperationSchedule"]
    sched = sched[sched["schedule_status"] == "PLANNED"]
    cal = t["MachineCalendar"][["calendar_id", "machine_id", "shift_start", "available_minutes",
                                "operators_assigned"]].rename(columns={"machine_id": "cal_machine_id"})
    df = (sched
          .merge(t["Operation"][["operation_id", "order_id", "route_id", "step_seq",
                                 "predecessor_operation_id"]], on="operation_id", how="left")
          .merge(t["ProductionOrder"][["order_id", "product_id", "release_date"]], on="order_id", how="left")
          .merge(t["Product"][["product_id", "product_family"]], on="product_id", how="left")
          .merge(t["RoutingStep"][["route_id", "step_seq", "tool_group"]], on=["route_id", "step_seq"], how="left")
          .merge(t["Machine"][["machine_id", "machine_group", "lifecycle_state", "operators_required"]],
                 on="machine_id", how="left")
          .merge(cal, on="calendar_id", how="left"))
    df["start"] = _dt(df["planned_start"])
    df["end"] = _dt(df["planned_end"])
    return df


def find_schedule_violations(t: dict[str, pd.DataFrame], cfg: GeneratorConfig) -> pd.DataFrame:
    """Typed hard-constraint violations found in the PLANNED schedule rows."""
    df = _planned_frame(t)
    v: list[dict] = []

    # 1. Two operations overlapping on one machine
    d = df.sort_values(["machine_id", "start", "schedule_id"]).copy()
    d["prev_max_end"] = d.groupby("machine_id")["end"].transform(lambda x: x.cummax().shift())
    v += _records(d[d["start"] < d["prev_max_end"]], "MACHINE_OVERLAP",
                  lambda r: f"{r.machine_id}: starts {r.planned_start} before an earlier job ends ({r.prev_max_end})")

    # 2. Precedence (and release date)
    pe = df[["operation_id", "end"]].rename(columns={"operation_id": "predecessor_operation_id",
                                                     "end": "pred_end"})
    p = df.merge(pe, on="predecessor_operation_id", how="left")
    has_pred = p["predecessor_operation_id"].notna()
    v += _records(p[has_pred & (p["pred_end"].isna() | (p["start"] < p["pred_end"]))],
                  "PRECEDENCE_VIOLATED",
                  lambda r: f"starts {r.planned_start} before predecessor {r.predecessor_operation_id} finishes")
    v += _records(df[df["start"] < pd.to_datetime(df["release_date"])], "PRECEDENCE_VIOLATED",
                  lambda r: f"starts {r.planned_start} before the order release date {r.release_date}")

    # 3. Material consumed before it is available
    m = t["MaterialRequirement"].merge(t["InventoryLot"][["lot_id", "available_at"]],
                                       left_on="allocated_lot_id", right_on="lot_id", how="left")
    m["avail"] = _dt(m["available_at"])
    latest = (m.sort_values(["operation_id", "avail", "lot_id"]).groupby("operation_id")
               .tail(1)[["operation_id", "avail", "lot_id"]])
    dm = df.merge(latest, on="operation_id", how="left")
    v += _records(dm[dm["avail"].notna() & (dm["start"] < dm["avail"])], "MATERIAL_NOT_AVAILABLE",
                  lambda r: f"starts {r.planned_start} but lot {r.lot_id} is only available at {r.avail}")

    # 4. Outside the shift window, and 5. unstaffed shift
    slot_start = _dt(df["shift_start"])
    usable_end = slot_start + pd.to_timedelta(df["available_minutes"], unit="m")
    outside = (df["shift_start"].isna() | (df["cal_machine_id"] != df["machine_id"])
               | (df["start"] < slot_start) | (df["end"] > usable_end))
    v += _records(df[outside], "OUTSIDE_SHIFT_CALENDAR",
                  lambda r: f"{r.planned_start} - {r.planned_end} is not inside the usable window of {r.calendar_id}")
    v += _records(df[df["operators_assigned"] < df["operators_required"]], "UNSTAFFED_SHIFT",
                  lambda r: f"{r.calendar_id} has {r.operators_assigned} operators, needs {r.operators_required}")

    # 6. Changeover omitted: recompute the required setup from the previous job on the machine
    co = t["ChangeoverMatrix"].rename(columns={"from_family": "prev_family", "to_family": "product_family",
                                               "changeover_minutes": "required_changeover"})
    d["prev_family"] = d.groupby("machine_id")["product_family"].shift()
    d = d.merge(co, on=["machine_group", "prev_family", "product_family"], how="left")
    d["required_changeover"] = d["required_changeover"].fillna(0)
    v += _records(d[d["changeover_minutes"] < d["required_changeover"]], "CHANGEOVER_OMITTED",
                  lambda r: f"changeover {r.changeover_minutes} min, but {r.prev_family} -> {r.product_family} needs {r.required_changeover:.0f}")

    # 7. Shared tool used by two operations at the same time
    tl = df[df["tool_group"].notna()].sort_values(["tool_group", "start", "schedule_id"]).copy()
    tl["prev_max_end"] = tl.groupby("tool_group")["end"].transform(lambda x: x.cummax().shift())
    v += _records(tl[tl["start"] < tl["prev_max_end"]], "SHARED_TOOL_CONFLICT",
                  lambda r: f"tool {r.tool_group} is still in use until {r.prev_max_end}")

    # 8. Machine not allowed for this step (or not ACTIVE)
    elig = t["RoutingStepMachine"][["route_id", "step_seq", "machine_id"]].assign(_eligible=True)
    de = df.merge(elig, on=["route_id", "step_seq", "machine_id"], how="left")
    v += _records(de[de["_eligible"].isna() | (de["lifecycle_state"] != "ACTIVE")], "MACHINE_NOT_ELIGIBLE",
                  lambda r: f"{r.machine_id} ({r.lifecycle_state}) is not an eligible machine for this step")

    return pd.DataFrame(v, columns=COLUMNS)


def check_schedule_integrity(t: dict[str, pd.DataFrame], cfg: GeneratorConfig) -> list[str]:
    p: list[str] = []
    sched, ops, orders = t["OperationSchedule"], t["Operation"], t["ProductionOrder"]
    machines, sev = t["Machine"], t["ScheduleEvent"]
    ev_o, vio = t["OrderStatusEvent"], t["ConstraintViolation"]

    # IDs, durations and derived values
    expected_ids = {ids.schedule_id(i, cfg.year) for i in range(1, len(sched) + 1)}
    if sched["schedule_id"].duplicated().any() or set(sched["schedule_id"]) != expected_ids:
        p.append("OperationSchedule: schedule_ids are not unique and numbered 1..n")
    minutes = ((_dt(sched["planned_end"]) - _dt(sched["planned_start"])).dt.total_seconds() / 60).round().astype(int)
    if (minutes != sched["changeover_minutes"] + sched["run_minutes"]).any():
        p.append("OperationSchedule: planned_end - planned_start != changeover + run")
    m = (sched.merge(ops[["operation_id", "standard_minutes"]], on="operation_id")
              .merge(machines[["machine_id", "speed_factor"]], on="machine_id"))
    if (m["run_minutes"] != np.ceil(m["standard_minutes"] / m["speed_factor"]).astype(int)).any():
        p.append("OperationSchedule: run_minutes != ceil(standard_minutes / speed_factor)")
    bym = sched.sort_values(["machine_id", "planned_start", "schedule_id"])
    expected_prev = bym.groupby("machine_id")["schedule_id"].shift()
    if (bym["previous_schedule_id"].fillna("") != expected_prev.fillna("")).any():
        p.append("OperationSchedule: previous_schedule_id is not the previous job on the machine")
    planned = sched[sched["schedule_status"] == "PLANNED"]
    if planned["operation_id"].duplicated().any():
        p.append("OperationSchedule: an operation has more than one PLANNED row")

    # Operation and order statuses
    scheduled = set(planned["operation_id"])
    expected_ops = np.where(ops["operation_id"].isin(scheduled), "SCHEDULED", "BLOCKED")
    if (ops["operation_status"].to_numpy() != expected_ops).any():
        p.append("Operation: operation_status does not match the schedule")
    blocked = set(ops.loc[ops["operation_status"] == "BLOCKED", "operation_id"])
    if blocked - set(vio["operation_id"]):
        p.append("Operation: a BLOCKED operation has no ConstraintViolation explaining it")
    all_sched = ops.assign(ok=ops["operation_id"].isin(scheduled)).groupby("order_id")["ok"].all()
    expected_orders = np.where(all_sched.reindex(orders["order_id"]).to_numpy(dtype=bool), "PLANNED", "NEW")
    if (orders["order_status"].to_numpy() != expected_orders).any():
        p.append("ProductionOrder: order_status is not PLANNED exactly when all operations are scheduled")

    # Order status history: unbroken chain, valid transitions, last event = current status
    allowed = TRANSITIONS[("ProductionOrder", "order_status")]
    ev = ev_o.sort_values(["order_id", "event_ts", "event_id"])
    for oid, g in ev.groupby("order_id"):
        prev = None
        for r in g.itertuples(index=False):
            frm = None if pd.isna(r.from_status) else r.from_status
            if frm != prev or (frm is not None and r.to_status not in allowed[frm]):
                p.append(f"OrderStatusEvent: invalid history for {oid}")
                break
            prev = r.to_status
    last = ev.groupby("order_id").tail(1).set_index("order_id")["to_status"]
    if (orders.set_index("order_id")["order_status"] != last.reindex(orders["order_id"])).any():
        p.append("ProductionOrder: order_status differs from the last OrderStatusEvent")

    # Schedule history
    if len(sev) != len(sched) or set(sev["schedule_id"]) != set(sched["schedule_id"]):
        p.append("ScheduleEvent: expected exactly one creation event per schedule row")
    if sev["from_status"].notna().any() or (sev["to_status"] != "PLANNED").any():
        p.append("ScheduleEvent: creation events must go from empty to PLANNED")

    # Violation records
    if ((vio["severity"] == "SOFT") != (vio["violation_type"] == "LATE_DELIVERY")).any():
        p.append("ConstraintViolation: only LATE_DELIVERY may be SOFT")
    op_order = dict(zip(ops["operation_id"], ops["order_id"]))
    comp = _dt(planned.assign(order_id=planned["operation_id"].map(op_order))
                      .groupby("order_id")["planned_end"].max())
    comp = comp[comp.index.isin(all_sched[all_sched].index)]
    due_end = pd.to_datetime(orders.set_index("order_id")["due_date"]) + pd.Timedelta(days=1)
    late = set(comp.index[comp > due_end.reindex(comp.index)])
    flagged = set(vio.loc[vio["violation_type"] == "LATE_DELIVERY", "operation_id"].map(op_order))
    if late != flagged:
        p.append("ConstraintViolation: LATE_DELIVERY rows do not match the orders that finish late")
    return p