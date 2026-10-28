"""Business-rule checks for the master data (rules listed in schema.py)."""
from __future__ import annotations

import pandas as pd

from mfgsched import ids
from mfgsched.schema import MACHINE_GROUPS, PRODUCT_FAMILIES


def check_master_rules(t: dict[str, pd.DataFrame]) -> list[str]:
    problems: list[str] = []
    products, routings = t["Product"], t["Routing"]
    steps, bridge, machines = t["RoutingStep"], t["RoutingStepMachine"], t["Machine"]

    # Dates and lifecycle
    for name in ("Product", "Routing"):
        dated = t[name][t[name]["effective_to"].notna()]
        bad = dated[dated["effective_to"] < dated["effective_from"]]
        if len(bad):
            problems.append(f"{name}: effective_to before effective_from in {len(bad)} row(s)")
    obsolete = products[products["lifecycle_state"] == "OBSOLETE"]
    if obsolete["effective_to"].isna().any():
        problems.append("Product: OBSOLETE product without effective_to")

    # Routing naming and versions
    merged = routings.merge(products[["product_id", "sku"]], on="product_id")
    expected = [ids.route_id(s, int(v)) for s, v in zip(merged["sku"], merged["route_version"])]
    if list(merged["route_id"]) != expected:
        problems.append("Routing: route_id does not match ROUTE-<sku>-<version>")
    active = routings[routings["lifecycle_state"] == "ACTIVE"].groupby("product_id").size()
    if (active > 1).any():
        problems.append("Routing: more than one ACTIVE routing for a product")

    # Steps: every routing has steps numbered 1..n
    no_steps = set(routings["route_id"]) - set(steps["route_id"])
    if no_steps:
        problems.append(f"Routing: {len(no_steps)} routing(s) without steps")
    for route_id, g in steps.groupby("route_id"):
        if sorted(g["step_seq"]) != list(range(1, len(g) + 1)):
            problems.append(f"RoutingStep: {route_id} steps are not numbered 1..n")

    # Every machine group has at least one ACTIVE machine
    for group in MACHINE_GROUPS:
        sub = machines[machines["machine_group"] == group]
        if not (sub["lifecycle_state"] == "ACTIVE").any():
            problems.append(f"Machine: group {group} has no ACTIVE machine")

    # Eligibility: group match, at least one ACTIVE machine, exactly one preferred
    m = (bridge.merge(machines[["machine_id", "machine_group", "lifecycle_state"]],
                      on="machine_id", how="left")
               .merge(steps[["route_id", "step_seq", "op_type"]],
                      on=["route_id", "step_seq"], how="left"))
    wrong = m[m["machine_group"] != m["op_type"]]
    if len(wrong):
        problems.append(f"RoutingStepMachine: {len(wrong)} row(s) where machine group != step op_type")
    per_step = m.groupby(["route_id", "step_seq"]).agg(
        active=("lifecycle_state", lambda s: int((s == "ACTIVE").sum())),
        preferred=("is_preferred", "sum"))
    if len(per_step) != len(steps):
        problems.append("RoutingStepMachine: some steps have no eligible machine")
    if (per_step["active"] < 1).any():
        problems.append("RoutingStepMachine: a step has no ACTIVE eligible machine")
    if (per_step["preferred"] != 1).any():
        problems.append("RoutingStepMachine: each step needs exactly one preferred machine")

    # Changeover matrix: complete, zero diagonal, positive elsewhere
    co = t["ChangeoverMatrix"]
    if len(co) != len(MACHINE_GROUPS) * len(PRODUCT_FAMILIES) ** 2:
        problems.append("ChangeoverMatrix: missing group/family combinations")
    same = co["from_family"] == co["to_family"]
    if (co.loc[same, "changeover_minutes"] != 0).any():
        problems.append("ChangeoverMatrix: same-family changeover must be 0")
    if (co.loc[~same, "changeover_minutes"] <= 0).any():
        problems.append("ChangeoverMatrix: different-family changeover must be > 0")
    return problems