-- =====================================================================
-- Marketing KPI queries (SQLite). Run by src/analysis.py.
-- Each query starts with a "-- name: <id>" line; results are exported to
-- reports/tables/<id>.csv.
--
-- Rule used throughout: rates are ratio-of-sums (SUM(subscribed) / COUNT(*)),
-- never an average of pre-computed rates. See query `rate_aggregation_pitfall`.
-- =====================================================================

-- name: overall_kpis
SELECT
    COUNT(*)                                        AS clients_contacted,
    SUM(contact_attempts)                           AS total_call_attempts,
    SUM(subscribed)                                 AS subscriptions,
    ROUND(1.0 * SUM(subscribed) / COUNT(*), 4)      AS conversion_rate,
    ROUND(1.0 * SUM(contact_attempts) / SUM(subscribed), 2) AS attempts_per_subscription,
    ROUND(SUM(call_minutes), 0)                     AS last_call_minutes_total
FROM fact_contact;

-- name: conversion_by_attempt
-- "Hazard" view: of the clients who were still being called at attempt n,
-- what share converted exactly on attempt n? This is what decides a call cap.
WITH by_n AS (
    SELECT contact_attempts AS attempt_n,
           COUNT(*)         AS clients_stopped_at_n,
           SUM(subscribed)  AS converted_at_n
    FROM fact_contact
    GROUP BY contact_attempts
),
reach AS (
    SELECT attempt_n,
           clients_stopped_at_n,
           converted_at_n,
           SUM(clients_stopped_at_n) OVER (ORDER BY attempt_n DESC) AS clients_reaching_n,
           SUM(converted_at_n)       OVER (ORDER BY attempt_n)      AS cumulative_conversions
    FROM by_n
)
SELECT attempt_n,
       clients_reaching_n,
       converted_at_n,
       ROUND(1.0 * converted_at_n / clients_reaching_n, 4)                       AS conversion_rate_of_nth_call,
       cumulative_conversions,
       ROUND(1.0 * cumulative_conversions / (SELECT SUM(subscribed) FROM fact_contact), 4) AS share_of_all_conversions
FROM reach
WHERE attempt_n <= 15
ORDER BY attempt_n;

-- name: conversion_by_channel
SELECT contact_channel,
       COUNT(*)                                   AS clients,
       SUM(subscribed)                            AS subscriptions,
       ROUND(1.0 * SUM(subscribed) / COUNT(*), 4) AS conversion_rate,
       ROUND(AVG(contact_attempts), 2)            AS avg_attempts,
       ROUND(AVG(call_minutes), 2)                AS avg_last_call_minutes
FROM fact_contact
GROUP BY contact_channel
ORDER BY conversion_rate DESC;

-- name: conversion_by_prior_outcome
SELECT prior_outcome,
       COUNT(*)                                   AS clients,
       SUM(subscribed)                            AS subscriptions,
       ROUND(1.0 * SUM(subscribed) / COUNT(*), 4) AS conversion_rate,
       ROUND(1.0 * SUM(subscribed) / COUNT(*)
             / (SELECT 1.0 * SUM(subscribed) / COUNT(*) FROM fact_contact), 2) AS lift_vs_average
FROM fact_contact
GROUP BY prior_outcome
ORDER BY conversion_rate DESC;

-- name: segment_lift
-- Job x age band, only cells with >= 300 clients so small samples do not top the list.
WITH base AS (SELECT 1.0 * SUM(subscribed) / COUNT(*) AS avg_rate FROM fact_contact)
SELECT s.job,
       s.age_band,
       COUNT(*)                                   AS clients,
       SUM(f.subscribed)                          AS subscriptions,
       ROUND(1.0 * SUM(f.subscribed) / COUNT(*), 4) AS conversion_rate,
       ROUND(1.0 * SUM(f.subscribed) / COUNT(*) / (SELECT avg_rate FROM base), 2) AS lift_vs_average,
       RANK() OVER (ORDER BY 1.0 * SUM(f.subscribed) / COUNT(*) DESC) AS rank_by_rate
FROM fact_contact f
JOIN dim_segment  s ON s.segment_key = f.segment_key
GROUP BY s.job, s.age_band
HAVING COUNT(*) >= 300
ORDER BY conversion_rate DESC;

-- name: monthly_trend
SELECT p.year_month,
       COUNT(*)                                   AS clients,
       SUM(f.subscribed)                          AS subscriptions,
       ROUND(1.0 * SUM(f.subscribed) / COUNT(*), 4) AS conversion_rate,
       ROUND(p.euribor3m, 3)                      AS euribor3m,
       ROUND(p.emp_var_rate, 2)                   AS emp_var_rate
FROM fact_contact f
JOIN dim_period   p ON p.period_key = f.period_key
GROUP BY p.year_month
ORDER BY p.year_month;

-- name: rate_aggregation_pitfall
-- Same data, two answers. Averaging the 26 monthly rates treats a 10-client
-- month the same as a 7,700-client month.
WITH m AS (
    SELECT period_key, COUNT(*) AS n, 1.0 * SUM(subscribed) / COUNT(*) AS rate
    FROM fact_contact GROUP BY period_key
)
SELECT ROUND(AVG(rate), 4)                                              AS avg_of_monthly_rates_WRONG,
       ROUND((SELECT 1.0 * SUM(subscribed) / COUNT(*) FROM fact_contact), 4) AS pooled_rate_CORRECT,
       COUNT(*)                                                         AS months
FROM m;
