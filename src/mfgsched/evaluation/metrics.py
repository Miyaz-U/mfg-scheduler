"""Evaluation metrics, derived only from source records.

Every function takes the table dictionary (in memory or loaded from CSV files)
and recomputes its numbers from scratch.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from mfgsched.config import GeneratorConfig
from mfgsched.schema import MACHINE_GROUPS
from mfgsched.validation.schedule_checks import find_schedule_violations

FMT = "%Y-%m-%d %H:%M:%S"


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format=FMT)


def _hours(delta: pd.Series) -> pd.Series:
    return delta.dt.total_seconds() / 3600


def _planned(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    s = t["OperationSchedule"]
    return s[s["schedule_status"] == "PLANNED"].copy()


def json_default(o):
    """Lets json.dump handle numpy numbers."""
    return o.item() if hasattr(o, "item") else str(o)


# ---------------------------------------------------------------------------
def operation_waits(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Per scheduled operation: why did it not start earlier?"""
    ops, orders = t["Operation"], t["ProductionOrder"]
    s = (_planned(t)
         .merge(ops[["operation_id", "order_id", "predecessor_operation_id"]], on="operation_id")
         .merge(orders[["order_id", "release_date"]], on="order_id"))
    s["start"], s["end"] = _dt(s["planned_start"]), _dt(s["planned_end"])

    pred_end = s[["operation_id", "end"]].rename(
        columns={"operation_id": "predecessor_operation_id", "end": "pred_end"})
    s = s.merge(pred_end, on="predecessor_operation_id", how="left")
    s["base_ready"] = s["pred_end"].fillna(pd.to_datetime(s["release_date"]))

    m = t["MaterialRequirement"].merge(t["InventoryLot"][["lot_id", "available_at"]],
                                       left_on="allocated_lot_id", right_on="lot_id", how="left")
    m["avail"] = _dt(m["available_at"])
    mat = m.groupby("operation_id")["avail"].max().rename("mat_avail").reset_index()
    s = s.merge(mat, on="operation_id", how="left")

    ready = pd.concat([s["base_ready"], s["mat_avail"]], axis=1).max(axis=1)
    s["material_wait_minutes"] = ((s["mat_avail"] - s["base_ready"]).dt.total_seconds() / 60
                                  ).clip(lower=0).fillna(0)
    s["capacity_wait_minutes"] = ((s["start"] - ready).dt.total_seconds() / 60).clip(lower=0)
    s["duration_minutes"] = (s["end"] - s["start"]).dt.total_seconds() / 60
    return s[["operation_id", "order_id", "start", "end", "duration_minutes",
              "material_wait_minutes", "capacity_wait_minutes"]]


def order_metrics(t: dict[str, pd.DataFrame], waits: pd.DataFrame) -> pd.DataFrame:
    orders, ops = t["ProductionOrder"], t["Operation"]
    s = _planned(t).merge(ops[["operation_id", "order_id"]], on="operation_id")
    s["start"], s["end"] = _dt(s["planned_start"]), _dt(s["planned_end"])
    g = (s.groupby("order_id")
          .agg(first_start=("start", "min"), completion=("end", "max"),
               scheduled_ops=("operation_id", "count"),
               changeover_minutes=("changeover_minutes", "sum"), run_minutes=("run_minutes", "sum"))
          .reset_index())
    w = (waits.groupby("order_id")
              .agg(material_wait_minutes=("material_wait_minutes", "sum"),
                   capacity_wait_minutes=("capacity_wait_minutes", "sum"))
              .reset_index())
    cols = ["order_id", "product_id", "quantity", "priority", "is_urgent",
            "release_date", "due_date", "order_status"]
    out = (orders[cols]
           .merge(ops.groupby("order_id").size().rename("total_ops").reset_index(), on="order_id")
           .merge(g, on="order_id", how="left")
           .merge(w, on="order_id", how="left"))

    out["scheduled_ops"] = out["scheduled_ops"].fillna(0).astype(int)
    for c in ("changeover_minutes", "run_minutes"):
        out[c] = out[c].fillna(0).astype(int)
    out["material_wait_hours"] = (out["material_wait_minutes"].fillna(0) / 60).round(2)
    out["capacity_wait_hours"] = (out["capacity_wait_minutes"].fillna(0) / 60).round(2)
    out = out.drop(columns=["material_wait_minutes", "capacity_wait_minutes"])

    out["fully_scheduled"] = out["scheduled_ops"] == out["total_ops"]
    out["completion"] = out["completion"].where(out["fully_scheduled"])   # only complete plans finish
    release = pd.to_datetime(out["release_date"])
    due_end = pd.to_datetime(out["due_date"]) + pd.Timedelta(days=1)       # decision D11
    out["flow_time_hours"] = _hours(out["completion"] - release).round(2)
    out["lateness_hours"] = _hours(out["completion"] - due_end).round(2)
    out["tardiness_hours"] = out["lateness_hours"].clip(lower=0)
    out["is_late"] = out["lateness_hours"] > 0
    out["queue_hours"] = (out["flow_time_hours"]
                          - (out["run_minutes"] + out["changeover_minutes"]) / 60).round(2)
    for c in ("first_start", "completion"):
        out[c] = out[c].dt.strftime(FMT)
    return out


def machine_metrics(t: dict[str, pd.DataFrame]) -> pd.DataFrame:
    machines, cal = t["Machine"], t["MachineCalendar"]
    cal = cal.merge(machines[["machine_id", "operators_required"]], on="machine_id")
    staffed = cal["operators_assigned"] >= cal["operators_required"]
    cal["usable_minutes"] = np.where(staffed, cal["available_minutes"], 0)
    cal["unstaffed_slot"] = (~staffed) & (cal["available_minutes"] > 0)
    cap = (cal.groupby("machine_id")
              .agg(calendar_slots=("calendar_id", "count"), nominal_minutes=("available_minutes", "sum"),
                   downtime_minutes=("downtime_minutes", "sum"), unstaffed_slots=("unstaffed_slot", "sum"),
                   usable_minutes=("usable_minutes", "sum"))
              .reset_index())
    busy = (_planned(t).groupby("machine_id")
                       .agg(jobs=("schedule_id", "count"), changeover_minutes=("changeover_minutes", "sum"),
                            run_minutes=("run_minutes", "sum"))
                       .reset_index())
    out = (machines[["machine_id", "machine_name", "machine_group", "lifecycle_state"]]
           .merge(cap, on="machine_id", how="left").merge(busy, on="machine_id", how="left"))
    for c in ("calendar_slots", "nominal_minutes", "downtime_minutes", "unstaffed_slots",
              "usable_minutes", "jobs", "changeover_minutes", "run_minutes"):
        out[c] = out[c].fillna(0).astype(int)
    out["busy_minutes"] = out["changeover_minutes"] + out["run_minutes"]
    out["utilization"] = (out["busy_minutes"] / out["usable_minutes"].replace(0, np.nan)).fillna(0.0).round(4)
    return out


# ---------------------------------------------------------------------------
def summary_metrics(t, cfg: GeneratorConfig, om: pd.DataFrame, mm: pd.DataFrame,
                    waits: pd.DataFrame) -> dict:
    ops, lots, vio = t["Operation"], t["InventoryLot"], t["ConstraintViolation"]
    sched = _planned(t)
    start, end = _dt(sched["planned_start"]).min(), _dt(sched["planned_end"]).max()
    makespan_h = (end - start).total_seconds() / 3600
    planned = om[om["fully_scheduled"]]
    late = planned[planned["is_late"]]
    n_planned = len(planned)

    def pct(a, b) -> float:
        return round(100.0 * float(a) / float(b), 2) if b else 0.0

    def mean(s: pd.Series) -> float:
        return round(float(s.mean()), 2) if len(s) else 0.0

    by_priority = {}
    for pr, g in om.groupby("priority"):
        gp = g[g["fully_scheduled"]]
        by_priority[f"P{pr}"] = {
            "orders": int(len(g)), "planned": int(len(gp)),
            "on_time_rate_pct": pct((~gp["is_late"]).sum(), len(gp)),
            "mean_lateness_hours": mean(gp["lateness_hours"]),
        }

    by_group = mm.groupby("machine_group")[["usable_minutes", "busy_minutes"]].sum()
    util_group = {g: pct(by_group.loc[g, "busy_minutes"], by_group.loc[g, "usable_minutes"])
                  for g in MACHINE_GROUPS if g in by_group.index}
    active = mm[(mm["lifecycle_state"] == "ACTIVE") & (mm["usable_minutes"] > 0)]

    co_total, run_total = int(sched["changeover_minutes"].sum()), int(sched["run_minutes"].sum())
    waiting = waits[waits["material_wait_minutes"] > 0]

    blocked = set(ops.loc[ops["operation_status"] == "BLOCKED", "operation_id"])
    explained = blocked & set(vio["operation_id"])

    hard = find_schedule_violations(t, cfg)
    hard_counts = {k: int(v) for k, v in hard["violation_type"].value_counts().items()}
    calendar_material = sum(hard_counts.get(k, 0) for k in
                            ("OUTSIDE_SHIFT_CALENDAR", "UNSTAFFED_SHIFT", "MATERIAL_NOT_AVAILABLE"))
    criteria = [
        {"criterion": "Schedules satisfy hard constraints",
         "measure": f"{len(hard)} hard-constraint violations found by the independent checker",
         "passed": len(hard) == 0},
        {"criterion": "Makespan and lateness are computable",
         "measure": f"makespan {makespan_h:.1f} h; lateness defined for {n_planned} planned orders",
         "passed": bool(np.isfinite(makespan_h) and planned["lateness_hours"].notna().all())},
        {"criterion": "Any infeasibility is explained",
         "measure": f"{len(explained)} of {len(blocked)} blocked operations have a ConstraintViolation",
         "passed": explained == blocked},
        {"criterion": "Material and machine calendars are respected",
         "measure": f"{calendar_material} shift/staffing/material violations",
         "passed": calendar_material == 0},
    ]

    return {
        "scale": {"seed": int(cfg.seed), "horizon_start": cfg.horizon_start.isoformat(),
                  "horizon_days": int(cfg.horizon_days)},
        "volume": {
            "orders": int(len(om)), "orders_planned": int(n_planned),
            "orders_not_fully_planned": int(len(om) - n_planned),
            "operations": int(len(ops)),
            "operations_scheduled": int((ops["operation_status"] == "SCHEDULED").sum()),
            "operations_blocked": int(len(blocked)),
            "operations_scheduled_pct": pct((ops["operation_status"] == "SCHEDULED").sum(), len(ops)),
        },
        "time": {
            "schedule_start": start.strftime(FMT), "schedule_end": end.strftime(FMT),
            "makespan_hours": round(makespan_h, 2), "makespan_days": round(makespan_h / 24, 2),
            "finishes_days_after_horizon_start": round(
                (end - pd.Timestamp(cfg.horizon_start)).total_seconds() / 86400, 2),
        },
        "delivery": {
            "on_time_orders": int(n_planned - len(late)), "late_orders": int(len(late)),
            "on_time_rate_pct": pct(n_planned - len(late), n_planned),
            "total_tardiness_hours": round(float(planned["tardiness_hours"].sum()), 2),
            "mean_tardiness_hours_of_late_orders": mean(late["tardiness_hours"]),
            "max_tardiness_hours": round(float(planned["tardiness_hours"].max()), 2) if n_planned else 0.0,
            "mean_flow_time_hours": mean(planned["flow_time_hours"]),
            "mean_queue_hours": mean(planned["queue_hours"]),
            "by_priority": by_priority,
        },
        "utilization": {
            "overall_pct": pct(mm["busy_minutes"].sum(), mm["usable_minutes"].sum()),
            "by_group_pct": util_group,
            "busiest_machine_pct": round(100 * float(active["utilization"].max()), 2) if len(active) else 0.0,
            "least_busy_active_machine_pct": round(100 * float(active["utilization"].min()), 2) if len(active) else 0.0,
        },
        "changeover": {
            "total_minutes": co_total, "setups": int((sched["changeover_minutes"] > 0).sum()),
            "share_of_busy_time_pct": pct(co_total, co_total + run_total),
        },
        "materials": {
            "lots": int(len(lots)), "delayed_lots": int(lots["is_delayed"].sum()),
            "operations_waiting_for_material": int(len(waiting)),
            "material_wait_hours_total": round(float(waiting["material_wait_minutes"].sum()) / 60, 1),
            "max_material_wait_hours": round(float(waiting["material_wait_minutes"].max()) / 60, 1) if len(waiting) else 0.0,
            "mean_capacity_wait_hours_per_operation": round(float(waits["capacity_wait_minutes"].mean()) / 60, 2),
        },
        "exceptions": {
            "violations_by_type": {k: int(v) for k, v in vio["violation_type"].value_counts().items()},
            "blocked_operations_explained_pct": pct(len(explained), len(blocked)) if blocked else 100.0,
            "hard_constraint_violations": hard_counts,
        },
        "acceptance_criteria": criteria,
    }


def reconcile(t, om: pd.DataFrame, mm: pd.DataFrame, waits: pd.DataFrame) -> list[str]:
    """Cross-check that independently derived numbers agree."""
    p: list[str] = []
    sched = _planned(t)
    if int(mm["busy_minutes"].sum()) != int((sched["changeover_minutes"] + sched["run_minutes"]).sum()):
        p.append("machine busy minutes != changeover + run minutes in the schedule")
    if int(om["run_minutes"].sum()) != int(sched["run_minutes"].sum()):
        p.append("order run minutes != schedule run minutes")
    if int(om["changeover_minutes"].sum()) != int(sched["changeover_minutes"].sum()):
        p.append("order changeover minutes != schedule changeover minutes")
    if int(om["scheduled_ops"].sum()) != len(sched) or len(waits) != len(sched):
        p.append("scheduled operation counts disagree")
    if (mm["utilization"] > 1 + 1e-9).any():
        p.append("a machine has utilization above 100 percent")

    pl = om[om["fully_scheduled"]]
    window = _hours(pd.to_datetime(pl["due_date"]) + pd.Timedelta(days=1) - pd.to_datetime(pl["release_date"]))
    if ((pl["lateness_hours"] - (pl["flow_time_hours"] - window)).abs() > 0.03).any():
        p.append("lateness != flow time - (due - release)")
    if (pl["flow_time_hours"] < 0).any():
        p.append("negative flow time")
    if (pl["queue_hours"] < -0.03).any():
        p.append("flow time is shorter than the order's own processing time")
    if ((pl["tardiness_hours"] > 0) != pl["is_late"]).any():
        p.append("is_late disagrees with tardiness")
    if (_dt(om.loc[om["fully_scheduled"], "completion"]) > _dt(sched["planned_end"]).max()).any():
        p.append("an order completes after the end of the schedule")
    return p


def compute_all(t: dict[str, pd.DataFrame], cfg: GeneratorConfig) -> dict:
    waits = operation_waits(t)
    om, mm = order_metrics(t, waits), machine_metrics(t)
    return {"orders": om, "machines": mm, "operations": waits,
            "summary": summary_metrics(t, cfg, om, mm, waits),
            "problems": reconcile(t, om, mm, waits)}


def write_metrics(metrics: dict, out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for key, name in (("orders", "order_metrics.csv"), ("machines", "machine_metrics.csv"),
                      ("operations", "operation_waits.csv")):
        path = out / name
        metrics[key].to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
        paths.append(path)
    path = out / "summary.json"
    path.write_text(json.dumps(metrics["summary"], indent=2, default=json_default), encoding="utf-8")
    return paths + [path]