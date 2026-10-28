from dataclasses import replace

from mfgsched.config import GeneratorConfig
from mfgsched.reproducibility import build_dataset, compare, fingerprint

CFG = GeneratorConfig.starter()


def test_same_seed_gives_identical_files(tmp_path):
    build_dataset(CFG, tmp_path / "a")
    build_dataset(CFG, tmp_path / "b")
    fa, fb = fingerprint(tmp_path / "a"), fingerprint(tmp_path / "b")
    assert len(fa) > 40
    assert compare(fa, fb) == []


def test_different_seed_changes_the_data(tmp_path):
    build_dataset(CFG, tmp_path / "a")
    build_dataset(replace(CFG, seed=CFG.seed + 1), tmp_path / "b")
    problems = compare(fingerprint(tmp_path / "a"), fingerprint(tmp_path / "b"))
    assert any("production_order.csv" in p for p in problems)


def test_compare_reports_missing_and_changed_files(tmp_path):
    build_dataset(CFG, tmp_path / "a")
    fa = fingerprint(tmp_path / "a")
    fb = {k: dict(v) for k, v in fa.items()}
    fb["clean/machine.csv"]["sha256"] = "0" * 64
    del fb["clean/product.csv"]
    problems = compare(fa, fb)
    assert "clean/machine.csv: checksum differs" in problems
    assert "clean/product.csv: present in only one run" in problems