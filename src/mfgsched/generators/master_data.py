"""Step 3: generate master (parent) data.

Dependency order: Product -> Routing, Machine -> RoutingStep -> bridges.
Each table uses its own random stream (see common.get_rng).
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from mfgsched import ids
from mfgsched.config import GeneratorConfig
from mfgsched.generators.common import get_rng, make_frame
from mfgsched.schema import MACHINE_GROUPS, MATERIAL_CATEGORIES, PRODUCT_FAMILIES

# ---------------------------------------------------------------------------
# Domain assumptions (tune here, not in the code below)
# ---------------------------------------------------------------------------
MACHINE_WEIGHTS = {"CUT": 6, "MILL": 7, "DRILL": 6, "WELD": 5,
                   "PAINT": 4, "ASSEMBLE": 8, "TEST": 4}
OPERATORS = {"CUT": (1, 2), "MILL": (1, 2), "DRILL": (1, 1), "WELD": (2, 2),
             "PAINT": (2, 3), "ASSEMBLE": (2, 3), "TEST": (1, 1)}
RATE_RANGE = {  # run minutes per unit, by machine group
    "CUT": (0.2, 0.8), "MILL": (0.3, 1.0), "DRILL": (0.1, 0.5),
    "WELD": (0.4, 1.2), "PAINT": (0.1, 0.4), "ASSEMBLE": (0.5, 1.5),
    "TEST": (0.1, 0.4),
}
CHANGEOVER_BASE = {"CUT": 20, "MILL": 30, "DRILL": 15, "WELD": 25,
                   "PAINT": 45, "ASSEMBLE": 15, "TEST": 10}
CATEGORY_WEIGHTS = {"STEEL": 20, "ALUMINUM": 12, "PLASTIC": 12, "FASTENER": 20,
                    "COATING": 10, "ELECTRONICS": 14, "PACKAGING": 12}
CATEGORY_UOM = {"STEEL": ("KG", "M"), "ALUMINUM": ("KG", "M"), "PLASTIC": ("KG",),
                "FASTENER": ("EA",), "COATING": ("L",),
                "ELECTRONICS": ("EA", "M"), "PACKAGING": ("EA",)}
LEAD_TIME = {"STEEL": (5, 21), "ALUMINUM": (7, 28), "PLASTIC": (3, 14),
             "FASTENER": (2, 10), "COATING": (5, 20), "ELECTRONICS": (14, 45),
             "PACKAGING": (2, 7)}
QTY_RANGE = {"KG": (0.05, 2.0), "M": (0.1, 1.5), "EA": (1, 8), "L": (0.01, 0.2)}
N_TOOLS = 12          # size of the shared-tooling pool
TOOL_PROB = 0.15      # share of steps that need a shared tool
ADJECTIVES = ("Heavy", "Compact", "Standard", "Precision", "Reinforced", "Light")
NOUNS = ("Bracket", "Housing", "Flange", "Shaft", "Gear", "Plate",
         "Manifold", "Bearing Block", "Cover", "Coupling")


def allocate(total: int, weights: dict[str, int], minimum: int = 1) -> dict[str, int]:
    """Split `total` across keys in proportion to weights (each gets >= minimum)."""
    keys = list(weights)
    remaining = total - minimum * len(keys)
    if remaining < 0:
        raise ValueError(f"need at least {minimum * len(keys)} items, got {total}")
    wsum = sum(weights.values())
    raw = {k: remaining * weights[k] / wsum for k in keys}
    counts = {k: minimum + int(raw[k]) for k in keys}
    leftover = total - sum(counts.values())
    by_fraction = sorted(keys, key=lambda k: (-(raw[k] - int(raw[k])), keys.index(k)))
    for k in by_fraction[:leftover]:
        counts[k] += 1
    return counts


# ---------------------------------------------------------------------------
def generate_products(cfg: GeneratorConfig) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "product")
    n = cfg.n_skus
    families = [PRODUCT_FAMILIES[i % len(PRODUCT_FAMILIES)] for i in range(n)]
    families = [families[i] for i in rng.permutation(n)]
    n_obsolete = max(1, round(0.04 * n))
    obsolete = set(rng.choice(n, size=n_obsolete, replace=False).tolist())

    rows = []
    for i in range(1, n + 1):
        sku = ids.sku_code(i)
        adjective = ADJECTIVES[int(rng.integers(len(ADJECTIVES)))]
        noun = NOUNS[int(rng.integers(len(NOUNS)))]
        eff_from = date(2023, 1, 1) + timedelta(days=int(rng.integers(0, 1095)))
        is_obsolete = (i - 1) in obsolete
        eff_to = None
        if is_obsolete:  # retired before the planning horizon starts
            eff_to = (date(2026, 3, 1) + timedelta(days=int(rng.integers(0, 214)))).isoformat()
        rows.append({
            "product_id": ids.product_id(i, cfg.year),
            "sku": sku,
            "product_name": f"{adjective} {noun} {i:04d}",
            "product_family": families[i - 1],
            "effective_from": eff_from.isoformat(),
            "effective_to": eff_to,
            "lifecycle_state": "OBSOLETE" if is_obsolete else "ACTIVE",
        })
    return make_frame("Product", rows)


def generate_routings(products: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for p in products.itertuples(index=False):
        obsolete = p.lifecycle_state == "OBSOLETE"
        rows.append({
            "route_id": ids.route_id(p.sku, 1),
            "product_id": p.product_id,
            "route_version": 1,
            "effective_from": p.effective_from,
            "effective_to": p.effective_to if obsolete else None,
            "lifecycle_state": "RETIRED" if obsolete else "ACTIVE",
        })
    return make_frame("Routing", rows)


def generate_machines(cfg: GeneratorConfig) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "machine")
    counts = allocate(cfg.n_machines, MACHINE_WEIGHTS)
    rows, n = [], 0
    for group in MACHINE_GROUPS:
        for k in range(1, counts[group] + 1):
            n += 1
            lo, hi = OPERATORS[group]
            rows.append({
                "machine_id": ids.machine_id(n),
                "machine_name": f"{group}-{k:02d}",
                "machine_group": group,
                "lifecycle_state": "ACTIVE",
                "operators_required": int(rng.integers(lo, hi + 1)),
                "speed_factor": round(float(rng.uniform(0.8, 1.2)), 2),
                "commissioned_on": (date(2012, 1, 1) + timedelta(
                    days=int(rng.integers(0, 4000)))).isoformat(),
                "effective_to": None,
            })
    # A few machines start in MAINTENANCE (never a group's only machine).
    n_maint = max(1, round(0.05 * cfg.n_machines))
    candidates = [i for i, r in enumerate(rows) if counts[r["machine_group"]] >= 2]
    for i in rng.choice(candidates, size=min(n_maint, len(candidates)), replace=False):
        rows[int(i)]["lifecycle_state"] = "MAINTENANCE"
    return make_frame("Machine", rows)


def generate_routing_steps(cfg: GeneratorConfig, routings: pd.DataFrame,
                           machine_counts: dict[str, int]) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "routing_step")
    n = len(routings)
    n3 = n5 = n // 4
    lengths = rng.permutation(np.array([3] * n3 + [4] * (n - n3 - n5) + [5] * n5))
    weights = np.array([machine_counts[g] for g in MACHINE_GROUPS], dtype=float)
    weights = weights / weights.sum()

    rows = []
    for route_id, length in zip(routings["route_id"], lengths):
        picked = rng.choice(len(MACHINE_GROUPS), size=int(length), replace=False, p=weights)
        for seq, gi in enumerate(sorted(picked.tolist()), start=1):  # keep process order
            group = MACHINE_GROUPS[gi]
            lo, hi = RATE_RANGE[group]
            rate = round(float(rng.uniform(lo, hi)), 2)
            fixed = round(float(rng.uniform(5, 30)), 1)
            u, tool_no = float(rng.random()), int(rng.integers(1, N_TOOLS + 1))
            rows.append({
                "route_id": route_id,
                "step_seq": seq,
                "op_type": group,
                "run_minutes_per_unit": rate,
                "fixed_minutes": fixed,
                "tool_group": f"TOOL-{tool_no:02d}" if u < TOOL_PROB else None,
            })
    return make_frame("RoutingStep", rows)


def generate_routing_step_machines(cfg: GeneratorConfig, steps: pd.DataFrame,
                                   machines: pd.DataFrame) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "routing_step_machine")
    by_group = {
        g: machines.loc[machines["machine_group"] == g,
                        ["machine_id", "lifecycle_state"]].values.tolist()
        for g in MACHINE_GROUPS
    }
    rows = []
    for s in steps.itertuples(index=False):
        pool = by_group[s.op_type]
        g = len(pool)
        k = int(rng.integers(max(1, g // 2), g + 1))
        picked = sorted(int(i) for i in rng.choice(g, size=k, replace=False))
        if not any(pool[i][1] == "ACTIVE" for i in picked):  # keep it schedulable
            actives = [i for i in range(g) if pool[i][1] == "ACTIVE"]
            picked = sorted(picked + [actives[int(rng.integers(len(actives)))]])
        preferred = next(i for i in picked if pool[i][1] == "ACTIVE")
        for i in picked:
            rows.append({
                "route_id": s.route_id, "step_seq": int(s.step_seq),
                "machine_id": pool[i][0], "is_preferred": i == preferred,
            })
    return make_frame("RoutingStepMachine", rows)


def generate_materials(cfg: GeneratorConfig) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "material")
    counts = allocate(cfg.n_materials, CATEGORY_WEIGHTS)
    rows, n = [], 0
    for cat in MATERIAL_CATEGORIES:
        for k in range(1, counts[cat] + 1):
            n += 1
            uoms = CATEGORY_UOM[cat]
            lo, hi = LEAD_TIME[cat]
            rows.append({
                "material_id": ids.material_id(n),
                "material_name": f"{cat.title()} Grade-{k:02d}",
                "material_category": cat,
                "unit_of_measure": uoms[int(rng.integers(len(uoms)))],
                "standard_lead_time_days": int(rng.integers(lo, hi + 1)),
            })
    return make_frame("Material", rows)


def generate_routing_step_materials(cfg: GeneratorConfig, steps: pd.DataFrame,
                                    materials: pd.DataFrame) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "routing_step_material")
    pools = {c: materials.loc[materials["material_category"] == c, "material_id"].tolist()
             for c in MATERIAL_CATEGORIES}
    uom = dict(zip(materials["material_id"], materials["unit_of_measure"]))
    last_seq = steps.groupby("route_id")["step_seq"].max().to_dict()

    rows = []
    for s in steps.itertuples(index=False):
        wanted: list[str] = []  # one category per material this step needs
        if s.step_seq == 1:  # first step consumes raw material
            wanted.append(("STEEL", "ALUMINUM", "PLASTIC")[int(rng.integers(3))])
        if s.op_type == "WELD" and rng.random() < 0.5:
            wanted.append("STEEL")  # filler metal
        if s.op_type == "PAINT":
            wanted.append("COATING")
        if s.op_type == "ASSEMBLE":
            wanted += ["FASTENER"] * int(rng.integers(1, 3))
            if rng.random() < 0.4:
                wanted.append("ELECTRONICS")
        if s.step_seq == last_seq[s.route_id]:  # last step packs the product
            wanted.append("PACKAGING")

        chosen: list[str] = []
        for cat in wanted:
            options = [m for m in pools[cat] if m not in chosen]
            if options:
                chosen.append(options[int(rng.integers(len(options)))])
        for mat in chosen:
            lo, hi = QTY_RANGE[uom[mat]]
            q = float(rng.uniform(lo, hi))
            q = float(max(1, round(q))) if uom[mat] == "EA" else round(q, 3)
            rows.append({"route_id": s.route_id, "step_seq": int(s.step_seq),
                         "material_id": mat, "qty_per_unit": q})
    return make_frame("RoutingStepMaterial", rows)


def generate_changeover_matrix(cfg: GeneratorConfig) -> pd.DataFrame:
    rng = get_rng(cfg.seed, "changeover")
    rows = []
    for group in MACHINE_GROUPS:
        for i, from_fam in enumerate(PRODUCT_FAMILIES):
            for j, to_fam in enumerate(PRODUCT_FAMILIES):
                if i == j:
                    minutes = 0
                else:
                    # farther-apart families cost more; the random part makes it asymmetric
                    factor = 0.6 + 0.3 * abs(i - j) + float(rng.uniform(0, 0.4))
                    minutes = int(min(240, max(5, round(CHANGEOVER_BASE[group] * factor))))
                rows.append({"machine_group": group, "from_family": from_fam,
                             "to_family": to_fam, "changeover_minutes": minutes})
    return make_frame("ChangeoverMatrix", rows)


# ---------------------------------------------------------------------------
def generate_master_data(cfg: GeneratorConfig) -> dict[str, pd.DataFrame]:
    """Generate all master tables (parents before children)."""
    products = generate_products(cfg)
    routings = generate_routings(products)
    machines = generate_machines(cfg)
    counts = machines["machine_group"].value_counts().to_dict()
    steps = generate_routing_steps(cfg, routings, counts)
    materials = generate_materials(cfg)
    return {
        "Product": products,
        "Routing": routings,
        "Machine": machines,
        "RoutingStep": steps,
        "RoutingStepMachine": generate_routing_step_machines(cfg, steps, machines),
        "Material": materials,
        "RoutingStepMaterial": generate_routing_step_materials(cfg, steps, materials),
        "ChangeoverMatrix": generate_changeover_matrix(cfg),
    }