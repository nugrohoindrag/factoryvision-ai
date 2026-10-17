/** Rupiah without decimals, e.g. 1450000 -> "Rp1.450.000" (Indonesian grouping). */
export function formatIdr(value: number): string {
  const grouped = Math.round(Math.abs(value)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  return `${value < 0 ? "-" : ""}Rp${grouped}`;
}

/** Compact Rupiah for KPI tiles: 64_708_000 -> "Rp64.7 M" (juta), 1.2e9 -> "Rp1.2 B" (miliar). */
export function formatIdrCompact(value: number): string {
  const abs = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (abs >= 1e9) return `${sign}Rp${(abs / 1e9).toFixed(1)} B`;
  if (abs >= 1e6) return `${sign}Rp${(abs / 1e6).toFixed(1)} M`;
  return formatIdr(value);
}
