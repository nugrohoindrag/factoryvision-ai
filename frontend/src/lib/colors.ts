/** Colours a SKU can be ordered in, read from GET /api/master/colors (model → colours). */

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

/** The model is the SKU without its position prefix: FB-ARKA and RB-ARKA are both ARKA. */
export const modelOf = (sku: string) => sku.split("-").slice(1).join("-") || sku;

/** The API answers `{models: {ARKA: [...]}, skus: {"FB-ARKA": [...]}, colors: [catalog]}`; `skus` wins, `models` is the fallback. */
export function colorsFor(data: Any, sku: string): string[] {
  if (!data || !sku) return [];
  const bySku = data.skus?.[sku] ?? data.sku_colors?.[sku];
  if (Array.isArray(bySku)) return bySku;
  const map = [data.model_colors, data.models, data.colors].find((m) => m && typeof m === "object" && !Array.isArray(m)) ?? data;
  const list = map?.[modelOf(sku)];
  return Array.isArray(list) ? list.map(String) : [];
}
