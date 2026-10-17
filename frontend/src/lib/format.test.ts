import { formatIdr, formatIdrCompact } from "./format";

describe("formatIdr", () => {
  it("groups thousands with dots", () => {
    expect(formatIdr(1450000)).toBe("Rp1.450.000");
    expect(formatIdr(-6800000)).toBe("-Rp6.800.000");
  });
  it("compacts millions and billions", () => {
    expect(formatIdrCompact(64_708_000)).toBe("Rp64.7 M");
    expect(formatIdrCompact(1_200_000_000)).toBe("Rp1.2 B");
    expect(formatIdrCompact(950_000)).toBe("Rp950.000");
  });
});
