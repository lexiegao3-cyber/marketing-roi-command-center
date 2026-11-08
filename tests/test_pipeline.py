"""Checks that the pipeline output is internally consistent. Run: python -m pytest -q"""
import sqlite3
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "warehouse" / "marketing.sqlite"


@pytest.fixture(scope="module")
def con():
    if not DB.exists():
        pytest.skip("run `python run_all.py` first")
    with sqlite3.connect(DB) as c:
        yield c


def test_row_counts(con):
    raw = pd.read_csv(ROOT / "data" / "raw" / "bank-additional-full.csv", sep=";")
    fact = pd.read_sql("SELECT COUNT(*) n FROM fact_contact", con).n[0]
    assert len(raw) == 41_188
    assert fact == len(raw.drop_duplicates())


def test_subscriptions_preserved(con):
    raw = pd.read_csv(ROOT / "data" / "raw" / "bank-additional-full.csv", sep=";").drop_duplicates()
    subs = pd.read_sql("SELECT SUM(subscribed) s FROM fact_contact", con).s[0]
    assert subs == (raw["y"] == "yes").sum()


def test_no_orphan_keys(con):
    q = """SELECT COUNT(*) n FROM fact_contact f
           LEFT JOIN dim_segment s ON s.segment_key = f.segment_key
           LEFT JOIN dim_period  p ON p.period_key  = f.period_key
           WHERE s.segment_key IS NULL OR p.period_key IS NULL"""
    assert pd.read_sql(q, con).n[0] == 0


def test_period_range(con):
    p = pd.read_sql("SELECT MIN(period_start) a, MAX(period_start) b FROM dim_period", con)
    assert (p.a[0], p.b[0]) == ("2008-05-01", "2010-11-01")


def test_conversion_rate_is_ratio_of_sums(con):
    t = pd.read_csv(ROOT / "reports" / "tables" / "conversion_by_channel.csv")
    overall = pd.read_csv(ROOT / "reports" / "tables" / "overall_kpis.csv").conversion_rate[0]
    pooled = t["subscriptions"].sum() / t["clients"].sum()
    assert abs(pooled - overall) < 1e-4


def test_call_cap_monotonic():
    c = pd.read_csv(ROOT / "reports" / "tables" / "call_cap_scenario.csv")
    assert c["attempts"].is_monotonic_increasing
    assert c["subscriptions"].is_monotonic_increasing


def test_model_does_not_use_leaky_features():
    import sys
    sys.path.insert(0, str(ROOT / "src"))
    import analysis
    used = set(analysis.FEATURES_CAT + analysis.FEATURES_NUM)
    assert not used & {"duration", "call_minutes", "contact_attempts", "subscribed"}
