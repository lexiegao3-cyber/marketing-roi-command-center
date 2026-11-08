# Power BI build guide

Everything the report needs is already in this repo: cleaned tables, Power Query
scripts, DAX measures. This guide turns them into the `.pbix` (about 3–4 hours the
first time). Run `python run_all.py` first so `data/processed/` and `reports/` exist.

## 1. Load data (Power Query)

1. Power BI Desktop → **Transform data** → **Manage Parameters** → New:
   `ProjectFolder` (Text) = the full path to this folder, ending in `\`.
2. For each block in `power_query/queries.pq`: **New Source → Blank Query →
   Advanced Editor**, paste, rename the query to the name in its comment.
3. **Close & Apply.** Expected row counts: `fact_contact` 41,176 · `dim_segment` 4,270 ·
   `dim_period` 26 · `targeting_gains` 10.

Three different source types are used on purpose: CSV (warehouse export), an
Excel workbook (model back-test), and a hand-maintained config file. The optional
ODBC block shows the same data read straight from the SQLite warehouse.

## 2. Model

1. **Modeling → New table**: create `Date` and `dim_attempt` from `measures.dax`.
   Mark `Date` as a date table.
2. Relationships (Model view), all single-direction, one-to-many:
   - `Date[Date]` → `fact_contact[period_start]`
   - `dim_period[period_key]` → `fact_contact[period_key]`
   - `dim_segment[segment_key]` → `fact_contact[segment_key]`
   - leave `dim_attempt` and the what-if tables **unconnected**
3. Sort `dim_segment[age_band]` by `age_band_sort`.
4. Hide all key columns and the raw `subscribed`, `contact_attempts` columns from report view
   (users should only see measures).
5. **Modeling → New parameter → Numeric range** for the four what-if parameters listed in
   `measures.dax` (tick "Add slicer to this page").
6. Create a blank table `_Measures` and add every measure from `measures.dax`.
   Format: rates as %, 1 decimal; EUR measures as currency, 0 decimals.

**Check before building visuals:** a card with `[Conversion Rate]` must show **11.3%**,
`[Avg of Monthly Rates (do not use)]` **35.4%**, `[Subscriptions]` **4,639**.
These match `reports/findings.md`; if they don't, a relationship is wrong.

## 3. Report pages

| Page | Visuals |
|---|---|
| **1. Executive overview** | KPI cards: Clients Contacted, Subscriptions, Conversion Rate, Attempts per Subscription, Est. ROI · Line: Conversion Rate by `Date[Year-Month]` · Bar: Conversion Rate by `contact_channel` · Bar: Conversion Rate by `prior_outcome` · Slicers: Year, channel |
| **2. Call cap & cost** | What-if slicers (Cost per Attempt, Cost per Minute, Value per Subscription, Call Cap) · Column: `dim_attempt[attempt_n]` × Conversion Rate of Call N · Line on same page (separate visual): Cumulative Share of Conversions · Cards: Attempts Saved %, Subscriptions Kept %, Net Value Change vs No Cap |
| **3. Segments** | Matrix: rows `job`, columns `age_band`, values Conversion Rate with **background conditional formatting** on Lift vs Baseline (diverging, midpoint 1.0) · Table: top segments with Clients, Conversion Rate, Lift · Slicers: education, marital |
| **4. Targeting** | Line: `targeting_gains[cum_clients_pct]` × `cum_subscriptions_pct` plus `cum_clients_pct` as a second line (the random-calling baseline) · Column: conversion_rate by decile · Text box: method + caveat from findings.md §2 |
| **5. Data quality & method** | Table: `data_quality_log` · Two cards side by side: Conversion Rate vs Avg of Monthly Rates (do not use) with a one-line explanation · Text box: data source citation |

Design rules: one accent colour (blue) for "our" metric, grey for comparisons;
no dual-axis charts; every chart has a sentence-style title that states the takeaway.

## 4. Publish & administer (Power BI Service)

1. **Publish** to a workspace named e.g. `Marketing Analytics – DEV`.
2. **Scheduled refresh**: local file paths need an on-premises (personal) gateway.
   Simpler: put the project folder in OneDrive/SharePoint, change `ProjectFolder`,
   republish → refresh works without a gateway. Set a daily refresh and a failure e-mail.
3. **Workspace roles**: Admin (you), Member (analyst peers), Viewer (marketing stakeholders).
   Share via an **App** rather than the workspace so viewers see a curated set of pages.
4. **Troubleshooting log** — keep notes in `docs/` as you hit issues, e.g.:
   - *"Expression.Error: column not found"* after a CSV change → check `Table.TransformColumnTypes` lists.
   - Refresh fails in Service but works in Desktop → credentials / privacy levels
     (Data source settings → set all sources to Organizational).
   - Totals look wrong → a measure is averaging a rate column; replace with DIVIDE(SUM, SUM).
5. Export page screenshots into `reports/figures/powerbi_*.png` and add them to the README.
