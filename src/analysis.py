"""
Run the SQL KPI layer, two budget scenarios, and a targeting back-test; write
CSV tables, charts, an Excel workbook with live formulas, and findings.md.

Scenarios use the editable cost/value assumptions in config/assumptions.csv.
The dataset contains no cost or revenue fields, so every EUR figure below is
"under these assumptions" - change the CSV and re-run to get your own numbers.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "warehouse" / "marketing.sqlite"
SQL = ROOT / "sql" / "kpi_queries.sql"
TABLES = ROOT / "reports" / "tables"
FIGS = ROOT / "reports" / "figures"

BLUE, ORANGE, INK, INK2, GRID, SURFACE = "#2a78d6", "#eb6834", "#0b0b0b", "#52514e", "#e6e5e1", "#fcfcfb"


# ---------------------------------------------------------------- helpers
def load_assumptions() -> dict[str, float]:
    a = pd.read_csv(ROOT / "config" / "assumptions.csv")
    return dict(zip(a["parameter"], a["value"].astype(float)))


def run_sql(con) -> dict[str, pd.DataFrame]:
    out = {}
    for block in re.split(r"^-- name: ", SQL.read_text(), flags=re.M)[1:]:
        name, _, query = block.partition("\n")
        out[name.strip()] = pd.read_sql(query, con)
    return out


def style(ax, title, xlabel=None, ylabel=None):
    ax.set_facecolor(SURFACE)
    ax.figure.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", fontsize=12, color=INK, pad=12)
    for s in ["top", "right"]:
        ax.spines[s].set_visible(False)
    for s in ["left", "bottom"]:
        ax.spines[s].set_color(GRID)
    ax.tick_params(colors=INK2, labelsize=9)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if xlabel:
        ax.set_xlabel(xlabel, color=INK2, fontsize=9)
    if ylabel:
        ax.set_ylabel(ylabel, color=INK2, fontsize=9)


# ---------------------------------------------------------------- scenario 1: call cap
def call_cap_scenario(fact: pd.DataFrame, a: dict) -> pd.DataFrame:
    """If we stop calling after N attempts, what do we save and what do we lose?
    Assumes calling stops once a client subscribes, so a client with k attempts
    who subscribed converted on call k."""
    rows = []
    base_attempts = fact["contact_attempts"].sum()
    base_subs = fact["subscribed"].sum()
    for cap in range(1, 16):
        attempts = np.minimum(fact["contact_attempts"], cap).sum()
        subs = fact.loc[fact["contact_attempts"] <= cap, "subscribed"].sum()
        rows.append({"call_cap": cap, "attempts": int(attempts), "subscriptions": int(subs),
                     "attempts_saved_pct": round(1 - attempts / base_attempts, 4),
                     "subscriptions_kept_pct": round(subs / base_subs, 4)})
    df = pd.DataFrame(rows)
    df["net_value_eur"] = (df["subscriptions"] * a["value_per_subscription"]
                           - df["attempts"] * a["cost_per_call_attempt"]).round(0)
    return df


# ---------------------------------------------------------------- scenario 2: targeting back-test
FEATURES_CAT = ["job", "marital", "education", "credit_default", "housing", "loan", "contact_channel",
                "day_of_week", "prior_outcome"]
FEATURES_NUM = ["age", "prior_campaign_contacts", "was_previously_contacted"]
MACRO = ["month", "emp_var_rate", "cons_price_idx", "cons_conf_idx", "euribor3m", "nr_employed"]
# Deliberately excluded:
#   duration          - only known after the call (target leakage)
#   contact_attempts  - an outcome of the current campaign, not known up front
#   MACRO             - tested and rejected: rates in the test window fall outside the training
#                       range, so these features hurt out-of-time AUC (see model_comparison table).


def _fit_score(train, test, cat, num):
    model = Pipeline([
        ("prep", ColumnTransformer([
            ("cat", OneHotEncoder(handle_unknown="ignore"), cat),
            ("num", StandardScaler(), num)])),
        ("clf", LogisticRegression(max_iter=2000, class_weight="balanced")),
    ])
    model.fit(train[cat + num], train["subscribed"])
    return model.predict_proba(test[cat + num])[:, 1]


def targeting_backtest(clean: pd.DataFrame, a: dict):
    df = clean.sort_values("contact_id")                         # source order == time order
    cut = int(len(df) * 0.75)
    train, test = df.iloc[:cut], df.iloc[cut:].copy()
    test["score"] = _fit_score(train, test, FEATURES_CAT, FEATURES_NUM)
    auc = roc_auc_score(test["subscribed"], test["score"])
    with_macro = _fit_score(train, test, FEATURES_CAT + ["month"], FEATURES_NUM + MACRO[1:])
    comparison = pd.DataFrame([
        {"feature_set": "client profile + contact history (used)", "test_auc": round(auc, 3)},
        {"feature_set": "+ month and macro-economic indicators", "test_auc": round(roc_auc_score(test["subscribed"], with_macro), 3)},
    ])

    test["decile"] = pd.qcut(test["score"].rank(method="first", ascending=False), 10, labels=range(1, 11)).astype(int)
    cost = (test["contact_attempts"] * a["cost_per_call_attempt"]
            + test["call_minutes"] * a["cost_per_connected_minute"])
    test["cost_eur"] = cost
    g = (test.groupby("decile")
             .agg(clients=("subscribed", "size"), subscriptions=("subscribed", "sum"), cost_eur=("cost_eur", "sum"))
             .reset_index())
    g["conversion_rate"] = (g["subscriptions"] / g["clients"]).round(4)
    g["cum_clients_pct"] = (g["clients"].cumsum() / g["clients"].sum()).round(3)
    g["cum_subscriptions_pct"] = (g["subscriptions"].cumsum() / g["subscriptions"].sum()).round(3)
    g["cum_net_value_eur"] = (g["subscriptions"].cumsum() * a["value_per_subscription"]
                              - g["cost_eur"].cumsum()).round(0)
    g["cost_eur"] = g["cost_eur"].round(0)
    meta = {"train_rows": len(train), "test_rows": len(test), "auc": auc,
            "train_period": f"{train['period_start'].min()} to {train['period_start'].max()}",
            "test_period": f"{test['period_start'].min()} to {test['period_start'].max()}",
            "test_conversion": test["subscribed"].mean(), "comparison": comparison}
    return g, meta


# ---------------------------------------------------------------- charts
def charts(t: dict[str, pd.DataFrame], gains: pd.DataFrame) -> None:
    FIGS.mkdir(parents=True, exist_ok=True)

    h = t["conversion_by_attempt"].head(10)
    fig, ax = plt.subplots(figsize=(8, 4.2))
    ax.bar(h["attempt_n"], h["conversion_rate_of_nth_call"] * 100, color=BLUE, width=0.6)
    style(ax, "Each extra call converts less: conversion rate of the n-th call", "Call attempt", "% converting on this call")
    ax.set_xticks(h["attempt_n"])
    for x, y, c in zip(h["attempt_n"], h["conversion_rate_of_nth_call"] * 100, h["share_of_all_conversions"]):
        if x in (1, 5, 10):
            ax.annotate(f"{y:.1f}%\n({c:.0%} of all\nconversions so far)", (x, y), xytext=(0, 4),
                        textcoords="offset points", ha="center", fontsize=8, color=INK2)
    ax.set_ylim(0, h["conversion_rate_of_nth_call"].max() * 100 * 1.45)
    fig.tight_layout(); fig.savefig(FIGS / "01_conversion_by_call_attempt.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(6, 4.6))
    x = np.r_[0, gains["cum_clients_pct"]] * 100
    y = np.r_[0, gains["cum_subscriptions_pct"]] * 100
    ax.plot(x, y, color=BLUE, linewidth=2, marker="o", markersize=5, label="Targeting model (back-test)")
    ax.plot([0, 100], [0, 100], color=INK2, linewidth=1, linestyle="--", label="Random calling")
    style(ax, "Cumulative gains on the held-out last 25% of contacts", "% of clients called (highest score first)",
          "% of subscriptions captured")
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    i3 = 2   # 30% of clients
    ax.annotate(f"Top 30% of clients\n-> {y[i3+1]:.0f}% of subscriptions", (x[i3+1], y[i3+1]), xytext=(-20, 40),
                textcoords="offset points", ha="right", fontsize=8, color=INK2, arrowprops=dict(arrowstyle="-", color=INK2))
    ax.legend(frameon=False, fontsize=8, loc="lower right")
    fig.tight_layout(); fig.savefig(FIGS / "02_targeting_gains_curve.png", dpi=160); plt.close(fig)

    m = t["monthly_trend"]
    m = m[m["clients"] >= 100]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(m["year_month"], m["conversion_rate"] * 100, color=BLUE, linewidth=2, marker="o", markersize=4)
    style(ax, "Monthly conversion rate (months with 100+ clients contacted)", None, "% subscribed")
    ax.set_xticks(range(len(m)))
    ax.set_xticklabels(m["year_month"], rotation=60, fontsize=8)
    fig.tight_layout(); fig.savefig(FIGS / "03_monthly_conversion_rate.png", dpi=160); plt.close(fig)


# ---------------------------------------------------------------- Excel workbook with live formulas
def excel_workbook(t, cap: pd.DataFrame, gains: pd.DataFrame, a: dict) -> Path:
    path = ROOT / "reports" / "marketing_kpi_workbook.xlsx"
    wb = Workbook()
    head = Font(bold=True, color="FFFFFF")
    fill = PatternFill("solid", fgColor="2A78D6")
    inp = PatternFill("solid", fgColor="FFF4CC")

    ws = wb.active
    ws.title = "Assumptions"
    ws.append(["Parameter", "Value (EUR)", "Note"])
    notes = pd.read_csv(ROOT / "config" / "assumptions.csv").set_index("parameter")["note"]
    for k in ["cost_per_call_attempt", "cost_per_connected_minute", "value_per_subscription"]:
        ws.append([k, a[k], notes[k]])
    for c in ws[1]:
        c.font, c.fill = head, fill
    for r in range(2, 5):
        ws.cell(r, 2).fill = inp
    ws.append([])
    ws.append(["Yellow cells are inputs. Every EUR figure in this workbook recalculates from them."])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width, ws.column_dimensions["C"].width = 28, 14, 95

    def sheet(name, df, formulas: dict[str, str] | None = None):
        s = wb.create_sheet(name)
        cols = list(df.columns) + list((formulas or {}).keys())
        s.append(cols)
        for i, row in enumerate(df.itertuples(index=False), start=2):
            vals = list(row)
            for f in (formulas or {}).values():
                vals.append(f.format(r=i))
            s.append(vals)
        for c in s[1]:
            c.font, c.fill = head, fill
            c.alignment = Alignment(wrap_text=True)
        for j, col in enumerate(cols, start=1):
            s.column_dimensions[get_column_letter(j)].width = max(12, min(30, len(col) + 2))
        s.freeze_panes = "A2"
        return s

    capv = cap.drop(columns=["net_value_eur"])
    # columns: A cap, B attempts, C subs, D saved%, E kept%  -> F/G/H formulas
    sheet("Call cap scenario", capv, {
        "cost_eur": "=B{r}*Assumptions!$B$2",
        "value_eur": "=C{r}*Assumptions!$B$4",
        "net_value_eur": "=G{r}-F{r}",
    })
    s = wb["Call cap scenario"]
    n = len(capv) + 1
    s.cell(n + 2, 1, "Best cap under current assumptions:")
    s.cell(n + 2, 3, f"=INDEX(A2:A{n},MATCH(MAX(H2:H{n}),H2:H{n},0))")
    s.cell(n + 2, 1).font = Font(bold=True)

    gv = gains[["decile", "clients", "subscriptions", "cost_eur", "conversion_rate", "cum_clients_pct",
                "cum_subscriptions_pct"]]
    # A decile, B clients, C subs, D cost, E rate, F cum%, G cum subs% -> H/I formulas
    sheet("Targeting back-test", gv, {
        "decile_net_value_eur": "=C{r}*Assumptions!$B$4-D{r}",
        "cum_net_value_eur": "=SUM($H$2:H{r})",
    })

    seg = t["segment_lift"]
    sheet("Segment lift", seg)
    sheet("Monthly trend", t["monthly_trend"])
    sheet("By channel", t["conversion_by_channel"])
    sheet("By prior outcome", t["conversion_by_prior_outcome"])
    wb.save(path)
    return path


# ---------------------------------------------------------------- findings
def findings(t, cap, gains, meta, a) -> str:
    o = t["overall_kpis"].iloc[0]
    h = t["conversion_by_attempt"].set_index("attempt_n")
    ch = t["conversion_by_channel"].set_index("contact_channel")
    po = t["conversion_by_prior_outcome"].set_index("prior_outcome")
    pit = t["rate_aggregation_pitfall"].iloc[0]
    best = cap.loc[cap["net_value_eur"].idxmax()]
    cap5 = cap.set_index("call_cap").loc[5]
    top = gains.loc[gains["cum_net_value_eur"].idxmax()]
    g30 = gains.set_index("decile").loc[3]
    seg = t["segment_lift"].head(3)
    m = t["monthly_trend"]
    corr = np.corrcoef(m["euribor3m"], m["conversion_rate"])[0, 1]

    return f"""# Findings

_Generated by `src/analysis.py` from UCI Bank Marketing (41,188 rows -> {int(o.clients_contacted):,} after de-duplication).
EUR figures use the assumptions in `config/assumptions.csv`
(EUR {a['cost_per_call_attempt']:.2f} per call attempt, EUR {a['cost_per_connected_minute']:.2f} per connected minute,
EUR {a['value_per_subscription']:.0f} per subscription). They are not in the dataset; change them and re-run._

## Headline KPIs
| KPI | Value |
|---|---|
| Clients contacted | {int(o.clients_contacted):,} |
| Call attempts | {int(o.total_call_attempts):,} |
| Subscriptions | {int(o.subscriptions):,} |
| Conversion rate | {o.conversion_rate:.1%} |
| Call attempts per subscription | {o.attempts_per_subscription:.1f} |

## 1. Most of the value is in the first few calls
- The 1st call converts {h.loc[1, 'conversion_rate_of_nth_call']:.1%} of clients reached; by the 5th call it is
  {h.loc[5, 'conversion_rate_of_nth_call']:.1%}, and by the 10th {h.loc[10, 'conversion_rate_of_nth_call']:.1%}.
- {h.loc[5, 'share_of_all_conversions']:.0%} of all subscriptions happen within 5 calls.
- **Capping at 5 calls** would save {cap5.attempts_saved_pct:.0%} of call attempts while keeping
  {cap5.subscriptions_kept_pct:.0%} of subscriptions.
- Under the current assumptions the value-maximising cap is **{int(best.call_cap)} calls**
  (the cap moves if cost per call or value per subscription changes - see the Excel workbook).

## 2. Targeting: score clients before calling
- Logistic regression trained on the first 75% of contacts ({meta['train_period']}), tested on the last 25%
  ({meta['test_period']}). Call duration and attempt count are excluded because neither is known before calling.
- Test AUC **{meta['auc']:.3f}**. If the call centre only has capacity for 30% of the list, calling the
  highest-scored 30% captures **{g30.cum_subscriptions_pct:.0%}** of the test-window subscriptions (random calling: 30%).
- Adding month and macro-economic indicators *lowered* out-of-time AUC to
  {meta['comparison'].iloc[1].test_auc:.3f}: interest rates in the test window are outside anything seen in training.
  They are useful for explaining the past (section 4), not for scoring the future.
- """ + (f"With unlimited capacity and the current assumptions, every decile is still profitable, so net value peaks at "
         f"calling everyone (EUR {top.cum_net_value_eur:,.0f}); the model's value is in **prioritising limited calling capacity**."
         if int(top.decile) == 10 else
         f"Net value peaks when calling the top **{int(top.decile) * 10}%** (EUR {top.cum_net_value_eur:,.0f}).") + f"""
- Caveat: the test window has a much higher base conversion ({meta['test_conversion']:.0%}) than training,
  so the model is ranking well under a market shift, but absolute rates should not be carried forward.

## 3. Who and how
- Mobile beats landline: cellular {ch.loc['cellular', 'conversion_rate']:.1%} vs telephone {ch.loc['telephone', 'conversion_rate']:.1%}.
- Clients who said yes in a previous campaign convert at {po.loc['success', 'conversion_rate']:.0%}
  ({po.loc['success', 'lift_vs_average']:.1f}x the average) - the clearest re-targeting list.
- Highest-lift segments (300+ clients each): """ + "; ".join(
        f"{r.job} {r.age_band} ({r.conversion_rate:.0%}, {r.lift_vs_average:.1f}x)" for r in seg.itertuples()) + f""".

## 4. Market context
- Monthly conversion moves inversely with the 3-month Euribor rate (correlation {corr:.2f} across {len(m)} months):
  conversion rose as rates fell after 2008. This is a correlation across time, not proof of cause.

## 5. A reporting trap this project avoids
- Averaging the {int(pit.months)} monthly conversion rates gives **{pit.avg_of_monthly_rates_WRONG:.1%}**.
  The correct pooled rate is **{pit.pooled_rate_CORRECT:.1%}**. Small months (10-70 clients) with very high rates
  dominate a simple average. Every rate in the SQL layer and the DAX measures is a ratio of sums for this reason.
"""


def main() -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    a = load_assumptions()
    with sqlite3.connect(DB) as con:
        t = run_sql(con)
        fact = pd.read_sql("SELECT contact_attempts, subscribed FROM fact_contact", con)
    clean = pd.read_csv(ROOT / "data" / "processed" / "contacts_clean.csv")

    cap = call_cap_scenario(fact, a)
    gains, meta = targeting_backtest(clean, a)
    t["call_cap_scenario"] = cap
    t["targeting_gains_by_decile"] = gains
    t["model_comparison"] = meta["comparison"]
    for name, df in t.items():
        df.to_csv(TABLES / f"{name}.csv", index=False)

    charts(t, gains)
    xl = excel_workbook(t, cap, gains, a)
    (ROOT / "reports" / "findings.md").write_text(findings(t, cap, gains, meta, a))
    print(f"AUC={meta['auc']:.3f}  tables={len(t)}  workbook={xl.name}")


if __name__ == "__main__":
    main()
