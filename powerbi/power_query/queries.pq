// =====================================================================
// Power Query (M) - one query per block. In Power BI Desktop:
// Home > Transform data > New Source > Blank Query > Advanced Editor,
// paste one block, rename the query to the name in the comment.
//
// Create a text parameter first: Manage Parameters > New
//   Name: ProjectFolder   Type: Text
//   Value: C:\path\to\marketing-roi-command-center\   (keep the trailing \)
// Moving the project to OneDrive/SharePoint later only means changing this
// one value (and lets Power BI Service refresh without a gateway).
// =====================================================================


// ---------- query: fact_contact  (source 1: CSV produced by src/etl.py)
let
    Source   = Csv.Document(File.Contents(ProjectFolder & "data\processed\fact_contact.csv"),
                            [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers  = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed    = Table.TransformColumnTypes(Headers, {
        {"contact_id", Int64.Type}, {"period_key", Int64.Type}, {"period_start", type date},
        {"segment_key", Int64.Type}, {"contact_channel", type text}, {"day_of_week", type text},
        {"contact_attempts", Int64.Type}, {"attempts_band", type text},
        {"prior_campaign_contacts", Int64.Type}, {"was_previously_contacted", Int64.Type},
        {"days_since_prior_contact", Int64.Type}, {"prior_outcome", type text},
        {"duration", Int64.Type}, {"call_minutes", type number}, {"euribor3m", type number},
        {"subscribed", Int64.Type}}, "en-US")
in
    Typed


// ---------- query: dim_segment
let
    Source  = Csv.Document(File.Contents(ProjectFolder & "data\processed\dim_segment.csv"),
                           [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed   = Table.TransformColumnTypes(Headers, {{"segment_key", Int64.Type}}, "en-US"),
    // readable labels for slicers
    AgeSort = Table.AddColumn(Typed, "age_band_sort", each
                List.PositionOf({"<25", "25-34", "35-44", "45-54", "55-64", "65+"}, [age_band]), Int64.Type)
in
    AgeSort


// ---------- query: dim_period  (macro-economic context per month)
let
    Source  = Csv.Document(File.Contents(ProjectFolder & "data\processed\dim_period.csv"),
                           [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed   = Table.TransformColumnTypes(Headers, {
        {"period_key", Int64.Type}, {"period_start", type date}, {"year", Int64.Type},
        {"month_num", Int64.Type}, {"emp_var_rate", type number}, {"cons_price_idx", type number},
        {"cons_conf_idx", type number}, {"euribor3m", type number}, {"nr_employed", type number}}, "en-US")
in
    Typed


// ---------- query: Assumptions  (source 2: hand-maintained CSV, reference only;
// the live what-if values come from Power BI numeric-range parameters)
let
    Source  = Csv.Document(File.Contents(ProjectFolder & "config\assumptions.csv"),
                           [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed   = Table.TransformColumnTypes(Headers, {{"value", type number}}, "en-US")
in
    Typed


// ---------- query: targeting_gains  (source 3: Excel workbook from src/analysis.py)
let
    Source = Excel.Workbook(File.Contents(ProjectFolder & "reports\marketing_kpi_workbook.xlsx"), null, true),
    Sheet  = Source{[Item = "Targeting back-test", Kind = "Sheet"]}[Data],
    Headers = Table.PromoteHeaders(Sheet, [PromoteAllScalars = true]),
    Keep   = Table.SelectColumns(Headers, {"decile", "clients", "subscriptions", "conversion_rate",
                                           "cum_clients_pct", "cum_subscriptions_pct"}),
    Typed  = Table.TransformColumnTypes(Keep, {
        {"decile", Int64.Type}, {"clients", Int64.Type}, {"subscriptions", Int64.Type},
        {"conversion_rate", type number}, {"cum_clients_pct", type number},
        {"cum_subscriptions_pct", type number}}, "en-US")
in
    Typed


// ---------- query: data_quality_log
let
    Source  = Csv.Document(File.Contents(ProjectFolder & "reports\data_quality_log.csv"),
                           [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Typed   = Table.TransformColumnTypes(Headers, {{"rows_affected", Int64.Type}}, "en-US")
in
    Typed


// ---------- OPTIONAL source: query the SQLite warehouse directly
// Needs the SQLite ODBC driver (http://www.ch-werner.de/sqliteodbc/) and a DSN named
// "marketing_sqlite" pointing at warehouse\marketing.sqlite.
// let
//     Source = Odbc.Query("dsn=marketing_sqlite",
//         "SELECT p.year_month, COUNT(*) AS clients, SUM(f.subscribed) AS subs
//          FROM fact_contact f JOIN dim_period p ON p.period_key = f.period_key
//          GROUP BY p.year_month")
// in
//     Source
