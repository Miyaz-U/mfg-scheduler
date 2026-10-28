"""Step 4: generate dependent (transaction) data.

Order: MachineCalendar; ProductionOrder -> Operation -> MaterialRequirement
(+ OrderStatusEvent); then InventoryLot, built from the requirements.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta

import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import get_rng, make_frame
from mfgsched.generators.master_data import generate_master_data

# ---------------------------------------------------------------------------
# Domain assumptions (tune here)
# ---------------------------------------------------------------------------
SHIFT_START_HOUR = {"A": 6, "B": 14, "C": 22}   # every shift is 8 hours
SHIFT_MINUTES = 480
BREAK_MINUTES = 30
MAX_DOWNTIME = SHIFT_MINUTES - BREAK_MINUTES
P_BREAKDOWN, P_PLANNED_MAINT = 0.02, 0.03
P_UNDERSTAFFED, P_OVERSTAFFED = 0.04, 0.05
PRIORITY_WEIGHTS = (0.08, 0.22, 0.45, 0.25)      # priority 1 (urgent) .. 4
QTY_MIN, QTY_STEP, QTY_CAP = 20, 5, 400
P_LOT_DELAY = 0.15
LOT_SIZE_RANGE = (4.0, 12.0)                     # lot size = requirement x this


def fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
def generate_machine_calendar(cfg: GeneratorConfig, machines: pd.DataFrame) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "machine_calendar")
    rows = []
    for m in machines.itertuples(index=False):
        works_saturday = rng.random() < 0.5
        has_night_shift = rng.random() < 0.2
        in_maintenance = m.lifecycle_state == "MAINTENANCE"
        for d in range(cfg.horizon_days):
            day = cfg.horizon_start + timedelta(days=d)
            weekday = day.weekday()                    # Monday = 0 ... Sunday = 6
            if weekday == 6 or (weekday == 5 and not works_saturday):
                continue
            shifts = ["A"] if weekday == 5 else ["A", "B"]
            if has_night_shift and weekday < 5:
                shifts.append("C")
            for shift in shifts:
                u_kind, u_len, u_staff = rng.random(), rng.random(), rng.random()
                required = int(m.operators_required)
                if in_maintenance:
                    reason, downtime, staffed = "PLANNED_MAINTENANCE", MAX_DOWNTIME, 0
                else:
                    if u_kind < P_BREAKDOWN:
                        reason, downtime = "BREAKDOWN", int(120 + u_len * 330)
                    elif u_kind < P_BREAKDOWN + P_PLANNED_MAINT:
                        reason, downtime = "PLANNED_MAINTENANCE", int(60 + u_len * 180)
                    else:
                        reason, downtime = "NONE", 0
                    staffed = required
                    if u_staff < P_UNDERSTAFFED:
                        staffed = max(0, required - 1)
                    elif u_staff > 1 - P_OVERSTAFFED:
                        staffed = min(6, required + 1)
                downtime = min(downtime, MAX_DOWNTIME)
                start = datetime.combine(day, time(SHIFT_START_HOUR[shift], 0))
                end = start + timedelta(minutes=SHIFT_MINUTES)
                rows.append({
                    "calendar_id": ids.calendar_id(m.machine_id, day, shift),
                    "machine_id": m.machine_id,
                    "calendar_date": day.isoformat(),
                    "shift_code": shift,
                    "shift_start": fmt(start),
                    "shift_end": fmt(end),
                    "planned_break_minutes": BREAK_MINUTES,
                    "downtime_minutes": downtime,
                    "downtime_reason": reason,
                    "operators_assigned": staffed,
                    "available_minutes": SHIFT_MINUTES - BREAK_MINUTES - downtime,
                })
    return make_frame("MachineCalendar", rows)


# ---------------------------------------------------------------------------
def _fix_total(picks: list[int], steps_per_product: list[int], target: int, rng) -> list[int]:
    """Swap products on random orders until total routing steps == target."""
    total = sum(steps_per_product[p] for p in picks)
    for _ in range(200_000):
        if total == target:
            return picks
        diff = total - target
        i = int(rng.integers(len(picks)))
        old = steps_per_product[picks[i]]
        candidates = [p for p, s in enumerate(steps_per_product)
                      if abs(diff - old + s) < abs(diff)]
        if not candidates:
            continue
        new = candidates[int(rng.integers(len(candidates)))]
        total += steps_per_product[new] - old
        picks[i] = new
    raise RuntimeError(f"could not reach {target} operations; check n_orders/n_operations")


def generate_orders(cfg: GeneratorConfig, master: dict[str, pd.DataFrame]):
    """Return (ProductionOrder, Operation, MaterialRequirement, OrderStatusEvent)."""
    rng = get_rng(cfg.seed, "order")
    products, routings = master["Product"], master["Routing"]
    steps, step_materials = master["RoutingStep"], master["RoutingStepMaterial"]

    active = products.loc[products["lifecycle_state"] == "ACTIVE", "product_id"].tolist()
    active_routes = routings[routings["lifecycle_state"] == "ACTIVE"]
    route_of = dict(zip(active_routes["product_id"], active_routes["route_id"]))
    route_steps = {
        rid: [(int(s), float(r), float(f)) for s, r, f in
              zip(g["step_seq"], g["run_minutes_per_unit"], g["fixed_minutes"])]
        for rid, g in steps.sort_values(["route_id", "step_seq"]).groupby("route_id")
    }
    mats_of: dict[tuple[str, int], list[tuple[str, float]]] = {}
    for r in step_materials.itertuples(index=False):
        mats_of.setdefault((r.route_id, int(r.step_seq)), []).append(
            (r.material_id, float(r.qty_per_unit)))
    steps_per_product = [len(route_steps[route_of[p]]) for p in active]

    # Which product each order is for (popular SKUs get more orders)
    weights = rng.lognormal(0.0, 0.6, size=len(active))
    weights = weights / weights.sum()
    picks = [int(i) for i in rng.choice(len(active), size=cfg.n_orders, p=weights)]
    picks = _fix_total(picks, steps_per_product, cfg.n_operations, rng)

    horizon_end = cfg.horizon_start + timedelta(days=cfg.horizon_days - 1)
    release_span = max(1, int(0.5 * cfg.horizon_days))
    raw = []
    for k, pi in enumerate(picks):
        product_id = active[pi]
        route_id = route_of[product_id]
        rsteps = route_steps[route_id]
        n_st = len(rsteps)
        priority = int(rng.choice(4, p=PRIORITY_WEIGHTS)) + 1
        release = cfg.horizon_start + timedelta(days=int(rng.integers(0, release_span)))
        if priority == 1:                                  # urgent: tight lead time
            lead = n_st + int(rng.integers(1, 4))
        else:
            lead = int(n_st * float(rng.uniform(1.5, 3.0))) + int(rng.integers(2, 9))
        due = min(release + timedelta(days=lead), horizon_end)
        due = max(due, release + timedelta(days=n_st))
        # largest quantity for which every step still fits the operation cap
        qmax = min(QTY_CAP, min(int((cfg.max_operation_minutes - f) / r) for _, r, f in rsteps))
        qty = QTY_STEP * int(rng.integers(QTY_MIN // QTY_STEP, qmax // QTY_STEP + 1))
        created = (datetime.combine(release, time(8, 0))
                   - timedelta(days=int(rng.integers(1, 8)), minutes=int(rng.integers(0, 240))))
        raw.append({"k": k, "product_id": product_id, "route_id": route_id, "qty": qty,
                    "priority": priority, "release": release, "due": due, "created": created})

    raw.sort(key=lambda o: (o["release"], o["due"], o["k"]))   # chronological IDs

    order_rows, op_rows, req_rows, event_rows = [], [], [], []
    req_n = 0
    for n, o in enumerate(raw, start=1):
        oid = ids.order_id(n, cfg.year)
        order_rows.append({
            "order_id": oid, "product_id": o["product_id"], "route_id": o["route_id"],
            "quantity": o["qty"], "release_date": o["release"].isoformat(),
            "due_date": o["due"].isoformat(), "priority": o["priority"],
            "is_urgent": o["priority"] == 1, "order_status": "NEW",
        })
        prev = None
        for seq, rate, fixed in route_steps[o["route_id"]]:
            op_id = ids.operation_id(oid, seq)
            op_rows.append({
                "operation_id": op_id, "order_id": oid, "route_id": o["route_id"],
                "step_seq": seq, "predecessor_operation_id": prev,
                "standard_minutes": round(fixed + o["qty"] * rate, 2),
                "operation_status": "PENDING",
            })
            for material_id, qty_per_unit in mats_of.get((o["route_id"], seq), []):
                req_n += 1
                req_rows.append({
                    "requirement_id": ids.requirement_id(req_n, cfg.year),
                    "operation_id": op_id, "material_id": material_id,
                    "allocated_lot_id": None,
                    "required_qty": round(qty_per_unit * o["qty"], 3),
                })
            prev = op_id
        event_rows.append({
            "event_id": ids.order_event_id(n, cfg.year), "order_id": oid,
            "from_status": None, "to_status": "NEW",
            "event_ts": fmt(o["created"]), "reason": "Order received",
        })
    return (make_frame("ProductionOrder", order_rows), make_frame("Operation", op_rows),
            make_frame("MaterialRequirement", req_rows), make_frame("OrderStatusEvent", event_rows))


# ---------------------------------------------------------------------------
def allocate_lots(cfg: GeneratorConfig, materials: pd.DataFrame, orders: pd.DataFrame,
                  operations: pd.DataFrame, requirements: pd.DataFrame):
    """Create inventory lots from demand and reserve one lot per requirement."""
    rng = get_rng(cfg.seed, "inventory_lot")
    uom = dict(zip(materials["material_id"], materials["unit_of_measure"]))
    order_of_op = dict(zip(operations["operation_id"], operations["order_id"]))
    order_dates = {o.order_id: (date.fromisoformat(o.release_date), date.fromisoformat(o.due_date))
                   for o in orders.itertuples(index=False)}
    start_midnight = datetime.combine(cfg.horizon_start, time(0, 0))
    stock_time = datetime.combine(cfg.horizon_start, time(6, 0))

    def round_up(x: float, material_id: str) -> float:
        return float(math.ceil(x)) if uom[material_id] == "EA" else math.ceil(x * 100) / 100

    reqs = requirements.to_dict("records")
    by_order: dict[str, list[dict]] = {}
    for r in reqs:
        by_order.setdefault(order_of_op[r["operation_id"]], []).append(r)

    pools: dict[str, list[dict]] = {}
    for oid in sorted(by_order, key=lambda o: (order_dates[o][1], o)):   # earliest due first
        release, due = order_dates[oid]
        for r in by_order[oid]:
            mat, qty = r["material_id"], r["required_qty"]
            pool = pools.setdefault(mat, [])
            lot = next((l for l in pool if l["remaining"] >= qty), None)
            if lot is None:                                        # open a new lot
                size = round_up(qty * float(rng.uniform(*LOT_SIZE_RANGE)), mat)
                u_time, u_delay = float(rng.random()), float(rng.random())
                delay_draw, stock_days = int(rng.integers(1, 8)), int(rng.integers(1, 5))
                if not pool:                                       # first lot: in stock
                    expected, delay = stock_time - timedelta(days=stock_days), 0
                else:                                              # later lot: still arriving
                    base = max(cfg.horizon_start, release)
                    window = max(1, (due - base).days) * 24 * 60 * 0.6
                    expected = datetime.combine(base, time(6, 0)) + timedelta(minutes=int(u_time * window))
                    delay = delay_draw if u_delay < P_LOT_DELAY else 0
                lot = {"material_id": mat, "seq": len(pool), "received": size,
                       "remaining": size, "expected": expected, "delay": delay}
                pool.append(lot)
            lot["remaining"] = max(0.0, round(lot["remaining"] - qty, 3))
            r["_lot"] = lot

    for mat in materials["material_id"]:          # materials nobody needs still get stock
        if mat not in pools:
            size = round_up(float(rng.uniform(50, 500)), mat)
            stock_days = int(rng.integers(1, 5))
            pools[mat] = [{"material_id": mat, "seq": 0, "received": size, "remaining": size,
                           "expected": stock_time - timedelta(days=stock_days), "delay": 0}]

    all_lots = sorted((l for pool in pools.values() for l in pool),
                      key=lambda l: (l["material_id"], l["seq"]))
    for n, lot in enumerate(all_lots, start=1):
        lot["lot_id"] = ids.lot_id(n, cfg.year)
    for r in reqs:
        r["allocated_lot_id"] = r.pop("_lot")["lot_id"]
    requirements_out = make_frame("MaterialRequirement", reqs)

    allocated = requirements_out.groupby("allocated_lot_id")["required_qty"].sum()
    rows = []
    for lot in all_lots:
        alloc = round(float(allocated.get(lot["lot_id"], 0.0)), 3)
        remaining = max(0.0, round(lot["received"] - alloc, 3))
        available = lot["expected"] + timedelta(days=lot["delay"])
        if remaining == 0:
            status = "EXHAUSTED"
        elif available > start_midnight:
            status = "IN_TRANSIT"
        else:
            status = "AVAILABLE"
        rows.append({
            "lot_id": lot["lot_id"], "material_id": lot["material_id"],
            "quantity_received": lot["received"],
            "expected_available_at": fmt(lot["expected"]), "available_at": fmt(available),
            "delay_days": lot["delay"], "is_delayed": lot["delay"] > 0,
            "quantity_allocated": alloc, "quantity_remaining": remaining, "lot_status": status,
        })
    return make_frame("InventoryLot", rows), requirements_out


# ---------------------------------------------------------------------------
def generate_transaction_data(cfg: GeneratorConfig, master: dict[str, pd.DataFrame]):
    calendar = generate_machine_calendar(cfg, master["Machine"])
    orders, operations, requirements, events = generate_orders(cfg, master)
    lots, requirements = allocate_lots(cfg, master["Material"], orders, operations, requirements)
    return {
        "MachineCalendar": calendar,
        "InventoryLot": lots,
        "ProductionOrder": orders,
        "Operation": operations,
        "MaterialRequirement": requirements,
        "OrderStatusEvent": events,
    }


def generate_all(cfg: GeneratorConfig) -> dict[str, pd.DataFrame]:
    """Master data + transaction data (parents always come before children)."""
    master = generate_master_data(cfg)
    return {**master, **generate_transaction_data(cfg, master)}