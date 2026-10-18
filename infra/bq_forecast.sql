-- BigQuery ML demand forecast (DF-1): one ARIMA_PLUS model over daily demand per SKU.
-- Same rules as the local fallback in backend/app/tools/forecast.py: cancelled orders excluded,
-- days without orders are zero, history ends the day before the demo "today" (2027-02-01).
-- Trained on the last 42 days, the same window as the local model (FIT_DAYS): over all 90 days the flat first seven
-- weeks damp the six-week FB-ARKA ramp to about +10%; over 42 days the trend is the one the plant is living now.
-- DF-4: the model is registered in Vertex AI Model Registry as fv_demand_arima (registry only;
-- ARIMA_PLUS cannot be deployed to a Vertex AI endpoint, so nothing is served from there).
-- Whoever runs this needs Vertex AI model permissions (project Owner has them; setup.sh enables the API).
-- Run: bq query --use_legacy_sql=false < infra/bq_forecast.sql

CREATE OR REPLACE MODEL `fv.demand_arima`
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'd',
  time_series_data_col = 'qty',
  time_series_id_col = 'sku',
  data_frequency = 'DAILY',
  holiday_region = 'ID',
  auto_arima = TRUE,
  model_registry = 'VERTEX_AI',
  vertex_ai_model_id = 'fv_demand_arima'
) AS
WITH days AS (
  SELECT d FROM UNNEST(GENERATE_DATE_ARRAY(DATE '2026-12-21', DATE '2027-01-31')) AS d
),
skus AS (SELECT DISTINCT sku FROM `fv.dim_product`),
demand AS (
  SELECT requested_delivery_date AS d, sku, SUM(order_qty) AS qty
  FROM `fv.fact_customer_order`
  WHERE order_status != 'Cancelled' AND requested_delivery_date < DATE '2027-02-01'
  GROUP BY d, sku
)
SELECT TIMESTAMP(days.d) AS d, skus.sku, COALESCE(demand.qty, 0) AS qty
FROM days CROSS JOIN skus
LEFT JOIN demand ON demand.d = days.d AND demand.sku = skus.sku;

-- DF-R53 (optional, cloud only): a backtest twin of the model, trained up to 2027-01-03, so ML.EVALUATE can score
-- the 4 weeks 2027-01-04..2027-01-31 against what was ordered. The app's accuracy view (GET /api/forecasts/accuracy)
-- runs the same 4-week backtest with the local model, so it works without this model; this is the BQML counterpart.
-- CREATE OR REPLACE MODEL `fv.demand_arima_backtest`
-- OPTIONS (model_type = 'ARIMA_PLUS', time_series_timestamp_col = 'd', time_series_data_col = 'qty',
--          time_series_id_col = 'sku', data_frequency = 'DAILY', holiday_region = 'ID', auto_arima = TRUE) AS
-- <same SELECT as above, with GENERATE_DATE_ARRAY(DATE '2026-11-23', DATE '2027-01-03')>;
-- SELECT * FROM ML.EVALUATE(MODEL `fv.demand_arima_backtest`, (<same SELECT for 2027-01-04..2027-01-31>), STRUCT(TRUE AS perform_aggregation, 28 AS horizon));
--
-- Snapshots (DF-R20..R27) are not stored in BigQuery: the warehouse is read-only, so each generate copies the
-- ML.FORECAST weekly totals into the session store (Firestore forecast_snapshots), where they never change.
