"""
ETL: UCI Bank Marketing (bank-additional-full.csv) -> cleaned table + SQLite star schema.

Source: Moro, S., Rita, P., & Cortez, P. (2014). Bank Marketing [Dataset].
UCI Machine Learning Repository. https://doi.org/10.24432/C5K306  (CC BY 4.0)

Key decisions (each is logged to reports/data_quality_log.csv):
  * The file has month but no year. Rows are documented as ordered by date
    (May 2008 -> Nov 2010), so the year is rebuilt by incrementing whenever the
    month number goes backwards. The rebuilt range is asserted against the
    documented range.
  * "unknown" is the dataset's missing-value code. It is kept as its own
    category (dropping every row with an "unknown" would silently remove 26% of clients).
  * pdays = 999 means "never contacted before"; it is turned into a flag
    instead of being left as a fake 999-day number that would distort averages.
  * Exact duplicate rows are removed and counted.
  * `duration` (last call length) is only known after the call ends, so it is
    kept for cost/effort analysis but excluded from any targeting model.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
RAW_FILE = ROOT / "data" / "raw" / "bank-additional-full.csv"
PROCESSED = ROOT / "data" / "processed"
WAREHOUSE = ROOT / "warehouse" / "marketing.sqlite"
REPORTS = ROOT / "reports"

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
EXPECTED_ROWS = 41_188


def load_raw() -> pd.DataFrame:
    df = pd.read_csv(RAW_FILE, sep=";")
    assert len(df) == EXPECTED_ROWS, f"expected {EXPECTED_ROWS} rows, got {len(df)} - wrong file?"
    df.columns = [c.replace(".", "_") for c in df.columns]
    return df


def rebuild_period(df: pd.DataFrame) -> pd.DataFrame:
    month_num = df["month"].map({m: i for i, m in enumerate(MONTHS, 1)})
    year = 2008 + (month_num.diff() < 0).cumsum()
    period = pd.to_datetime({"year": year, "month": month_num, "day": 1})
    assert period.min() == pd.Timestamp("2008-05-01") and period.max() == pd.Timestamp("2010-11-01"), \
        "rebuilt date range does not match the documented May-2008..Nov-2010 range"
    return df.assign(period_start=period.dt.date, period_key=period.dt.strftime("%Y%m").astype(int))


def clean(df: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    log: list[dict] = []
    n0 = len(df)

    # keep source order for the period rebuild, then de-duplicate
    df = rebuild_period(df)
    dups = df.duplicated()
    log.append({"check": "exact duplicate rows", "rows_affected": int(dups.sum()), "action": "removed"})
    df = df.loc[~dups].copy()

    for col in ["job", "marital", "education", "default", "housing", "loan"]:
        log.append({"check": f"'unknown' in {col}", "rows_affected": int((df[col] == "unknown").sum()),
                    "action": "kept as its own category"})

    never = df["pdays"].eq(999)
    log.append({"check": "pdays = 999 (never contacted)", "rows_affected": int(never.sum()),
                "action": "converted to was_previously_contacted flag; days set to NULL"})
    df["was_previously_contacted"] = (~never).astype(int)
    df["days_since_prior_contact"] = df["pdays"].where(~never)

    zero_calls = df["duration"].eq(0)
    log.append({"check": "duration = 0 seconds", "rows_affected": int(zero_calls.sum()),
                "action": "kept (call not connected); excluded from model features"})

    df["subscribed"] = df["y"].eq("yes").astype(int)
    df["age_band"] = pd.cut(df["age"], [0, 24, 34, 44, 54, 64, 120],
                            labels=["<25", "25-34", "35-44", "45-54", "55-64", "65+"]).astype(str)
    df["attempts_band"] = pd.cut(df["campaign"], [0, 1, 2, 3, 4, 5, 7, 10, 1000],
                                 labels=["1", "2", "3", "4", "5", "6-7", "8-10", "11+"]).astype(str)
    df["call_minutes"] = (df["duration"] / 60).round(2)
    df = df.rename(columns={"campaign": "contact_attempts", "previous": "prior_campaign_contacts",
                            "poutcome": "prior_outcome", "contact": "contact_channel",
                            "default": "credit_default"})
    df.insert(0, "contact_id", range(1, len(df) + 1))
    df = df.drop(columns=["y", "pdays"])

    log.append({"check": "rows in -> rows out", "rows_affected": n0 - len(df), "action": f"{n0} -> {len(df)}"})
    return df, log


def build_star_schema(df: pd.DataFrame) -> dict[str, pd.DataFrame]:
    dim_period = (df.groupby(["period_key", "period_start"], as_index=False)
                    .agg(emp_var_rate=("emp_var_rate", "mean"), cons_price_idx=("cons_price_idx", "mean"),
                         cons_conf_idx=("cons_conf_idx", "mean"), euribor3m=("euribor3m", "mean"),
                         nr_employed=("nr_employed", "mean")))
    ps = pd.to_datetime(dim_period["period_start"])
    dim_period.insert(2, "year", ps.dt.year)
    dim_period.insert(3, "month_num", ps.dt.month)
    dim_period.insert(4, "month_name", ps.dt.strftime("%b"))
    dim_period.insert(5, "quarter", "Q" + ps.dt.quarter.astype(str))
    dim_period.insert(6, "year_month", ps.dt.strftime("%Y-%m"))

    seg_cols = ["job", "marital", "education", "age_band", "credit_default", "housing", "loan"]
    dim_segment = df[seg_cols].drop_duplicates().sort_values(seg_cols).reset_index(drop=True)
    dim_segment.insert(0, "segment_key", range(1, len(dim_segment) + 1))

    fact = df.merge(dim_segment, on=seg_cols, how="left")
    fact = fact[["contact_id", "period_key", "period_start", "segment_key", "contact_channel", "day_of_week",
                 "contact_attempts", "attempts_band", "prior_campaign_contacts", "was_previously_contacted",
                 "days_since_prior_contact", "prior_outcome", "duration", "call_minutes", "euribor3m",
                 "subscribed"]]
    assert fact["segment_key"].notna().all()
    return {"dim_period": dim_period, "dim_segment": dim_segment, "fact_contact": fact}


def main() -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    WAREHOUSE.parent.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)

    df, log = clean(load_raw())
    df.to_csv(PROCESSED / "contacts_clean.csv", index=False)
    pd.DataFrame(log).to_csv(REPORTS / "data_quality_log.csv", index=False)

    tables = build_star_schema(df)
    WAREHOUSE.unlink(missing_ok=True)
    with sqlite3.connect(WAREHOUSE) as con:
        for name, t in tables.items():
            t.to_sql(name, con, index=False)
            t.to_csv(PROCESSED / f"{name}.csv", index=False)   # Power BI can read these directly
        con.execute("CREATE INDEX ix_fact_period ON fact_contact(period_key)")
        con.execute("CREATE INDEX ix_fact_segment ON fact_contact(segment_key)")

    print(pd.DataFrame(log).to_string(index=False))
    print({k: len(v) for k, v in tables.items()})


if __name__ == "__main__":
    main()
