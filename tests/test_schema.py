import re

from mfgsched.schema import (
    CORE_ENTITIES, DTYPES, GROUND_TRUTH, NM_RELATIONSHIPS, RELATIONSHIPS,
    TABLE_BY_NAME, TABLES, TRANSITIONS,
)


def test_all_core_entities_present():
    core = {t.name for t in TABLES if t.kind == "core"}
    assert core == set(CORE_ENTITIES)
    assert len(core) == 10


def test_field_names_unique_and_types_valid():
    for t in TABLES + (GROUND_TRUTH,):
        names = [f.name for f in t.fields]
        assert len(names) == len(set(names)), f"duplicate field in {t.name}"
        assert all(f.dtype in DTYPES for f in t.fields), t.name


def test_primary_keys_exist_and_are_required():
    for t in TABLES + (GROUND_TRUTH,):
        assert t.pk, t.name
        for col in t.pk:
            assert t.field(col).required, f"{t.name}.{col} must be required"


def test_unique_together_columns_exist():
    for t in TABLES:
        for combo in t.unique_together:
            for col in combo:
                t.field(col)  # raises StopIteration if missing


def test_relationships_are_valid():
    for r in RELATIONSHIPS:
        parent, child = TABLE_BY_NAME[r.parent], TABLE_BY_NAME[r.child]
        assert r.parent_cols == parent.pk, f"{r.parent}->{r.child}: not parent PK"
        assert len(r.parent_cols) == len(r.child_cols)
        for pc, cc in zip(r.parent_cols, r.child_cols):
            assert parent.field(pc).dtype == child.field(cc).dtype, (r, pc, cc)
            # a required FK must be a required column; an optional FK must not
            assert child.field(cc).required == r.fk_required, (r, cc)


def test_patterns_and_enums_are_well_formed():
    for t in TABLES + (GROUND_TRUTH,):
        for f in t.fields:
            if f.pattern:
                re.compile(f.pattern)
                assert f.dtype == "str", f"{t.name}.{f.name}"
            assert len(f.allowed) == len(set(f.allowed)), f"{t.name}.{f.name}"


def test_ground_truth_is_separate():
    assert GROUND_TRUTH not in TABLES
    assert all(GROUND_TRUTH.name not in (r.parent, r.child) for r in RELATIONSHIPS)
    leaked = {"issue_type", "ground_truth", "expected_decision"}
    for t in TABLES:
        assert not leaked & {f.name for f in t.fields}, t.name


def test_lifecycle_transitions_use_allowed_values():
    for (table, col), moves in TRANSITIONS.items():
        allowed = set(TABLE_BY_NAME[table].field(col).allowed)
        assert set(moves) == allowed, (table, col)
        for dsts in moves.values():
            assert set(dsts) <= allowed


def test_many_to_many_have_bridge_tables():
    pairs = {(r.parent, r.child) for r in RELATIONSHIPS}
    for a, b, bridge in NM_RELATIONSHIPS:
        assert (a, bridge) in pairs and (b, bridge) in pairs, bridge