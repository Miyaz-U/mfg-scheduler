"""Business-rule checks for calendars, orders, operations, requirements and lots."""
from __future__ import annotations

from datetime import timedelta

import numpy as np
import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.transaction_data import SHIFT_START_HOUR


def _dt(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, format="%Y-%m-%d %H:%M:%S")


def _horizon(cfg: GeneratorConfig) -> tuple[str, str]:
    last = cfg.horizon_start + timedelta(days=cfg.horizon_days - 1)
    return cfg.horizon_start.isoformat(), last.isoformat()


def check_transaction_rules(t: dict[str, pd.DataFrame], cfg: GeneratorConfig) -> list[str]:
    return (_check_calendar(t, cfg) + _check_orders(t, cfg) + _check_operations(t, cfg)
            + _check_requirements_and_lots(t, cfg) + _check_events(t))


def _check_calendar(t, cfg) -> list[str]:
    p: list[str] = []
    cal, machines = t["MachineCalendar"], t["Machine"]
    start, end = _dt(cal["shift_start"]), _dt(cal["shift_end"])
    if not (end > start).all():
        p.append("MachineCalendar: shift_end must be after shift_start")
    length = ((end - start).dt.total_seconds() / 60).round().astype(int)
    expected = length - cal["planned_break_minutes"] - cal["downtime_minutes"]
    if (cal["available_minutes"] != expected).any():
        p.append("MachineCalendar: available_minutes != shift length - break - downtime")
    if ((cal["downtime_minutes"] > 0) != (cal["downtime_reason"] != "NONE")).any():
        p.append("MachineCalendar: downtime_minutes and downtime_reason disagree")
    expected_ids = [ids.calendar_id(m, d, s) for m, d, s in
                    zip(cal["machine_id"], cal["calendar_date"], cal["shift_code"])]
    if list(cal["calendar_id"]) != expected_ids:
        p.append("MachineCalendar: calendar_id does not match machine/date/shift")
    if (start.dt.strftime("%Y-%m-%d") != cal["calendar_date"]).any():
        p.append("MachineCalendar: shift_start is not on calendar_date")
    if (start.dt.hour != cal["shift_code"].map(SHIFT_START_HOUR)).any():
        p.append("MachineCalendar: shift_start hour does not match shift_code")
    first, last = _horizon(cfg)
    if ((cal["calendar_date"] < first) | (cal["calendar_date"] > last)).any():
        p.append("MachineCalendar: slot outside the planning horizon")
    maint = machines.loc[machines["lifecycle_state"] == "MAINTENANCE", "machine_id"]
    if (cal.loc[cal["machine_id"].isin(maint), "available_minutes"] != 0).any():
        p.append("MachineCalendar: a MAINTENANCE machine has available minutes")
    return p


def _check_orders(t, cfg) -> list[str]:
    p: list[str] = []
    orders, routings, products = t["ProductionOrder"], t["Routing"], t["Product"]
    first, last = _horizon(cfg)
    if len(orders) != cfg.n_orders:
        p.append(f"ProductionOrder: {len(orders)} rows, expected {cfg.n_orders}")
    if (orders["due_date"] < orders["release_date"]).any():
        p.append("ProductionOrder: due_date before release_date")
    if ((orders["release_date"] < first) | (orders["due_date"] > last)).any():
        p.append("ProductionOrder: dates outside the planning horizon")
    m = orders.merge(routings[["route_id", "product_id", "lifecycle_state"]],
                     on="route_id", how="left", suffixes=("", "_route"))
    if (m["product_id"] != m["product_id_route"]).any():
        p.append("ProductionOrder: routing belongs to a different product")
    if (m["lifecycle_state"] != "ACTIVE").any():
        p.append("ProductionOrder: routing is not ACTIVE")
    obsolete = set(products.loc[products["lifecycle_state"] == "OBSOLETE", "product_id"])
    if orders["product_id"].isin(obsolete).any():
        p.append("ProductionOrder: order for an OBSOLETE product")
    if (orders["is_urgent"] != (orders["priority"] == 1)).any():
        p.append("ProductionOrder: is_urgent != (priority == 1)")
    return p


def _check_operations(t, cfg) -> list[str]:
    p: list[str] = []
    ops, orders, steps = t["Operation"], t["ProductionOrder"], t["RoutingStep"]
    if len(ops) != cfg.n_operations:
        p.append(f"Operation: {len(ops)} rows, expected {cfg.n_operations}")
    expected_ids = [ids.operation_id(o, int(s)) for o, s in zip(ops["order_id"], ops["step_seq"])]
    if list(ops["operation_id"]) != expected_ids:
        p.append("Operation: operation_id does not match order and step")
    exp_pred = [ids.operation_id(o, int(s) - 1) if s > 1 else None
                for o, s in zip(ops["order_id"], ops["step_seq"])]
    act_pred = [None if pd.isna(x) else x for x in ops["predecessor_operation_id"]]
    if exp_pred != act_pred:
        p.append("Operation: predecessor is not the previous step of the same order")

    n_steps = steps.groupby("route_id").size()
    cmp = pd.concat([orders.set_index("order_id")["route_id"].map(n_steps).rename("e"),
                     ops.groupby("order_id").size().rename("a")], axis=1)
    if cmp.isna().any().any() or (cmp["e"] != cmp["a"]).any():
        p.append("Operation: an order's operations do not match its routing steps")

    r = ops.merge(orders[["order_id", "route_id"]], on="order_id", suffixes=("", "_order"))
    if (r["route_id"] != r["route_id_order"]).any():
        p.append("Operation: route_id differs from its order's route_id")

    m = (ops.merge(orders[["order_id", "quantity"]], on="order_id")
            .merge(steps, on=["route_id", "step_seq"]))
    if len(m) != len(ops):
        p.append("Operation: some operations have no matching routing step")
    expected = (m["fixed_minutes"] + m["quantity"] * m["run_minutes_per_unit"]).round(2)
    if ((m["standard_minutes"] - expected).abs() > 0.011).any():
        p.append("Operation: standard_minutes != fixed + quantity x rate")
    if (ops["standard_minutes"] > cfg.max_operation_minutes).any():
        p.append(f"Operation: standard_minutes above {cfg.max_operation_minutes} (cannot fit a shift)")
    return p


def _check_requirements_and_lots(t, cfg) -> list[str]:
    p: list[str] = []
    ops, orders, reqs = t["Operation"], t["ProductionOrder"], t["MaterialRequirement"]
    lots, bom = t["InventoryLot"], t["RoutingStepMaterial"]

    m = (reqs.merge(ops[["operation_id", "order_id", "route_id", "step_seq"]], on="operation_id")
             .merge(orders[["order_id", "quantity"]], on="order_id")
             .merge(bom, on=["route_id", "step_seq", "material_id"], how="left"))
    if m["qty_per_unit"].isna().any():
        p.append("MaterialRequirement: requirement without a matching BOM line")
    expected = (m["qty_per_unit"] * m["quantity"]).round(3)
    if ((m["required_qty"] - expected).abs() > 0.0011).any():
        p.append("MaterialRequirement: required_qty != qty_per_unit x order quantity")
    per_step = bom.groupby(["route_id", "step_seq"]).size().rename("n").reset_index()
    expected_n = ops.merge(per_step, on=["route_id", "step_seq"], how="left")["n"].fillna(0).sum()
    if len(reqs) != int(expected_n):
        p.append(f"MaterialRequirement: {len(reqs)} rows, expected {int(expected_n)}")
    if reqs["allocated_lot_id"].isna().any():
        p.append("MaterialRequirement: requirement without an allocated lot")
    mm = reqs.merge(lots[["lot_id", "material_id"]], left_on="allocated_lot_id",
                    right_on="lot_id", how="left", suffixes=("", "_lot"))
    if (mm["material_id"] != mm["material_id_lot"]).any():
        p.append("MaterialRequirement: allocated lot holds a different material")

    expected_at, available_at = _dt(lots["expected_available_at"]), _dt(lots["available_at"])
    if (available_at < expected_at).any():
        p.append("InventoryLot: available_at before expected_available_at")
    if (lots["delay_days"] != (available_at - expected_at).dt.days).any():
        p.append("InventoryLot: delay_days is not the gap between expected and actual")
    if (lots["is_delayed"] != (lots["delay_days"] > 0)).any():
        p.append("InventoryLot: is_delayed != (delay_days > 0)")
    allocated = lots["lot_id"].map(reqs.groupby("allocated_lot_id")["required_qty"].sum()).fillna(0.0).round(3)
    if ((lots["quantity_allocated"] - allocated).abs() > 0.0011).any():
        p.append("InventoryLot: quantity_allocated != sum of allocated requirements")
    remaining = (lots["quantity_received"] - lots["quantity_allocated"]).round(3)
    if ((lots["quantity_remaining"] - remaining).abs() > 0.0011).any():
        p.append("InventoryLot: quantity_remaining != received - allocated")
    if (lots["quantity_remaining"] < 0).any():
        p.append("InventoryLot: a lot is over-allocated")
    status = np.where(lots["quantity_remaining"] == 0, "EXHAUSTED",
                      np.where(available_at > pd.Timestamp(cfg.horizon_start), "IN_TRANSIT", "AVAILABLE"))
    if (lots["lot_status"].to_numpy() != status).any():
        p.append("InventoryLot: lot_status does not follow its rule")
    return p


def _check_events(t) -> list[str]:
    """Every order must have exactly one creation event (empty -> NEW)."""
    p: list[str] = []
    ev, orders = t["OrderStatusEvent"], t["ProductionOrder"]
    created = ev[ev["from_status"].isna()]
    if len(created) != len(orders) or set(created["order_id"]) != set(orders["order_id"]):
        p.append("OrderStatusEvent: expected exactly one creation event per order")
    if (created["to_status"] != "NEW").any():
        p.append("OrderStatusEvent: creation events must go from empty to NEW")
    return p