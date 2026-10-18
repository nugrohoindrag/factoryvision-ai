import { LINE_TOP, dryerParts, immParts } from "./LineAsset";

it("keeps every IMM part inside the machine footprint and under the line top", () => {
  const w = 4;
  const h = 2.6;
  const d = 2.4;
  for (const p of immParts(w, h, d)) {
    const [x, y] = p.pos;
    const halfX = p.kind === "box" ? p.args[0] / 2 : p.rot ? p.args[2] / 2 : p.args[0];
    expect(Math.abs(x) + halfX).toBeLessThanOrEqual(0.75 * w);
    expect(y).toBeLessThanOrEqual(LINE_TOP * h);
    expect(y).toBeGreaterThanOrEqual(0);
  }
});

it("builds the injection machine from the parts the line image shows", () => {
  const keys = immParts(4, 2.6, 2.4).map((p) => p.key);
  expect(keys).toEqual(expect.arrayContaining(["barrel", "hopper", "clamp", "control-cabinet", "robot-arm"]));
});

it("builds the dryer with a frame, a body and a feeder above it", () => {
  const parts = dryerParts(2.4, 3, 2.4);
  expect(parts.map((p) => p.key)).toEqual(expect.arrayContaining(["frame", "body", "feeder"]));
  for (const p of parts) expect(p.pos[1]).toBeLessThanOrEqual(1.1 * 3);
});
