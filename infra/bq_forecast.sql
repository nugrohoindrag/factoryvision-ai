-- BigQuery ML demand forecast (DF-1): one ARIMA_PLUS model over daily demand per SKU.
-- Same rules as the local fallback in backend/app/tools/forecast.py: cancelled orders excluded,
-- days without orders are zero, history ends the day before the demo "today" (2027-02-01).
-- Run: bq query --use_legacy_sql=false < infra/bq_forecast.sql

CREATE OR REPLACE MODEL `fv.demand_arima`
OPTIONS (
  model_type = 'ARIMA_PLUS',
  time_series_timestamp_col = 'd',
  time_series_data_col = 'qty',
  time_series_id_col = 'sku',
  data_frequency = 'DAILY',
  holiday_region = 'ID',
  auto_arima = TRUE
) AS
WITH days AS (
  SELECT d FROM UNNEST(GENERATE_DATE_ARRAY(DATE '2026-11-03', DATE '2027-01-31')) AS d
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
