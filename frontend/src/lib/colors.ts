/** Colours a SKU can be ordered in, read from GET /api/master/colors (model → colours). */

/* eslint-disable @typescript-eslint/no-explicit-any */
type Any = any;

/** The model is the SKU without its position prefix: FB-ARKA and RB-ARKA are both ARKA. */
export const modelOf = (sku: string) => sku.split("-").slice(1).join("-") || sku;

/** Accepts `{ARKA: [...]}` and the wrapped forms `{models|model_colors|colors: {...}}`, plus an optional `sku_colors` map. */
export function colorsFor(data: Any, sku: string): string[] {
  if (!data || !sku) return [];
  const bySku = data.sku_colors?.[sku];
  if (Array.isArray(bySku)) return bySku;
  const map = [data.model_colors, data.models, data.colors].find((m) => m && typeof m === "object" && !Array.isArray(m)) ?? data;
  const list = map?.[modelOf(sku)];
  return Array.isArray(list) ? list.map(String) : [];
}
