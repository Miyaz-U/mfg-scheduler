import json
import sqlite3

import pandas as pd
import pytest

from mfgsched.config import GeneratorConfig
from mfgsched.exporting import (
    build_manifest, ground_truth_sql, load_into_sqlite, schema_sql, seed_sql,
    table_order, verify_checksums, write_checksums, write_json_tables, write_text,
)
from mfgsched.generators.anomalies import inject_anomalies
from mfgsched.generators.transaction_data import generate_all
from mfgsched.schema import TABLES
from mfgsched.scheduling.scheduler import schedule

CFG = GeneratorConfig.starter()


@pytest.fixture(scope="module")
def data():
    clean = schedule(generate_all(CFG), CFG)
    injected, gt = inject_anomalies(clean, CFG)
    return clean, injected, gt


def test_table_order_puts_parents_first():
    order = table_order()
    assert set(order) == {t.name for t in TABLES}
    assert order.index("Product") < order.index("Routing") < order.index("RoutingStep")
    assert order.index("Operation") < order.index("OperationSchedule")


def test_sql_loads_with_foreign_keys_and_matches_row_counts(data):
    clean, injected, _ = data
    for tables in (clean, injected):
        con = load_into_sqlite(schema_sql(), seed_sql(tables, "test"))
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        for t in TABLES:
            n = con.execute(f"SELECT COUNT(*) FROM {t.name}").fetchone()[0]
            assert n == len(tables[t.name])


def test_sql_schema_enforces_keys_and_enums(data):
    clean, _, _ = data
    con = load_into_sqlite(schema_sql(), seed_sql(clean, "test"))
    with pytest.raises(sqlite3.IntegrityError):          # duplicate primary key
        con.execute("INSERT INTO Machine SELECT * FROM Machine LIMIT 1")
    with pytest.raises(sqlite3.IntegrityError):          # orphan foreign key
        con.execute("UPDATE Operation SET order_id = 'ORD-2026-999999' WHERE rowid = 1")
    with pytest.raises(sqlite3.IntegrityError):          # enum CHECK
        con.execute("UPDATE Machine SET machine_group = 'LASER' WHERE rowid = 1")


def test_sql_values_round_trip(data):
    clean, _, _ = data
    con = load_into_sqlite(schema_sql(), seed_sql(clean, "test"))
    lots = clean["InventoryLot"].sort_values("lot_id").reset_index(drop=True)
    sql = pd.read_sql_query("SELECT * FROM InventoryLot ORDER BY lot_id", con)
    assert sql["lot_id"].tolist() == lots["lot_id"].tolist()
    assert sql["quantity_received"].round(6).tolist() == lots["quantity_received"].round(6).tolist()
    assert sql["is_delayed"].astype(bool).tolist() == lots["is_delayed"].astype(bool).tolist()
    nulls = clean["OperationSchedule"]["previous_schedule_id"].isna().sum()
    assert con.execute("SELECT COUNT(*) FROM OperationSchedule WHERE previous_schedule_id IS NULL"
                       ).fetchone()[0] == nulls


def test_sql_output_is_deterministic(data):
    clean, _, _ = data
    assert seed_sql(clean, "x") == seed_sql({k: v.copy() for k, v in clean.items()}, "x")
    assert schema_sql() == schema_sql()


def test_ground_truth_is_separate(data):
    clean, injected, gt = data
    assert "ground_truth" not in seed_sql(injected, "t").lower()
    assert "GroundTruth" not in schema_sql()
    con = sqlite3.connect(":memory:")
    con.executescript(ground_truth_sql(gt))
    assert con.execute("SELECT COUNT(*) FROM GroundTruth").fetchone()[0] == len(gt)


def test_json_export_round_trips(data, tmp_path):
    clean, _, _ = data
    write_json_tables(clean, tmp_path)
    rows = json.loads((tmp_path / "machine.json").read_text(encoding="utf-8"))
    assert len(rows) == len(clean["Machine"])
    assert rows[0]["machine_id"] == clean["Machine"].loc[0, "machine_id"]
    assert rows[0]["effective_to"] is None               # empty value becomes null
    lots = json.loads((tmp_path / "inventory_lot.json").read_text(encoding="utf-8"))
    assert isinstance(lots[0]["is_delayed"], bool)


def test_manifest_and_checksums_detect_tampering(tmp_path):
    write_text(tmp_path / "a.txt", "alpha\n")
    write_text(tmp_path / "sub" / "b.txt", "beta\n")
    manifest = build_manifest(tmp_path, {"seed": 1})
    assert [f["path"] for f in manifest["files"]] == ["a.txt", "sub/b.txt"]
    write_text(tmp_path / "source_manifest.json", json.dumps(manifest))
    write_checksums(tmp_path)
    assert verify_checksums(tmp_path) == []
    (tmp_path / "a.txt").write_text("changed\n", encoding="utf-8")
    assert verify_checksums(tmp_path) == ["checksum mismatch: a.txt"]
    write_text(tmp_path / "extra.txt", "x")
    assert "unlisted file: extra.txt" in verify_checksums(tmp_path)