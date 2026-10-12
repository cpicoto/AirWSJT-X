import { test, expect } from "@playwright/test";
import {
  HeadingSmoother,
  headingFromOrientation,
  normalizeDegrees,
  signedHeadingDelta,
} from "../src/lib/compass";

test.describe("compass helpers", () => {
  test("signedHeadingDelta takes the shortest path across north", () => {
    expect(signedHeadingDelta(1, 359)).toBeCloseTo(2, 6);
    expect(signedHeadingDelta(359, 1)).toBeCloseTo(-2, 6);
    expect(signedHeadingDelta(90, 0)).toBeCloseTo(90, 6);
    expect(Math.abs(signedHeadingDelta(180, 0))).toBeCloseTo(180, 6);
    expect(normalizeDegrees(-10)).toBeCloseTo(350, 6);
  });

  test("flat phone: heading is the screen-top direction (alpha is counter-clockwise)", () => {
    expect(headingFromOrientation(0, 0, 0)).toBeCloseTo(0, 6);
    expect(headingFromOrientation(90, 0, 0)).toBeCloseTo(270, 6);
    expect(headingFromOrientation(270, 0, 0)).toBeCloseTo(90, 6);
  });

  test("upright phone: heading is the rear-camera direction and ignores gimbal alpha swing", () => {
    // Held upright (beta ~ 90) facing east: alpha 270.
    expect(headingFromOrientation(270, 90, 0)).toBeCloseTo(90, 6);
    // Tilting the phone slightly towards/away from vertical keeps the heading steady.
    for (const beta of [60, 75, 90, 105, 120, 135, 140]) {
      expect(headingFromOrientation(270, beta, 0)).toBeCloseTo(90, 6);
    }
    // Small roll must not move the heading much.
    expect(Math.abs(signedHeadingDelta(headingFromOrientation(270, 90, 5)!, 90))).toBeLessThan(6);
  });

  test("landscape compensation rotates the screen-top axis", () => {
    // Flat, phone rotated 90 deg (landscape): screen top is device +x.
    expect(headingFromOrientation(0, 0, 0, 90)).toBeCloseTo(90, 6);
  });

  test("smoother crosses 359 -> 1 without sweeping the long way", () => {
    const smoother = new HeadingSmoother({ minIntervalMs: 0, deadbandDeg: 0 });
    smoother.push(359, 0);
    const out = smoother.push(1, 100)!;
    const travelled = Math.abs(signedHeadingDelta(out, 359));
    expect(travelled).toBeLessThanOrEqual(2);
    expect(out < 5 || out > 355).toBe(true);
  });

  test("smoother attenuates jitter around the 0/360 seam", () => {
    const smoother = new HeadingSmoother({ minIntervalMs: 0, deadbandDeg: 0 });
    let t = 0;
    smoother.push(0, t);
    const outputs: number[] = [];
    for (let i = 0; i < 100; i++) {
      t += 16;
      const noisy = i % 2 === 0 ? 357 : 3;
      outputs.push(smoother.push(noisy, t)!);
    }
    for (const value of outputs) {
      expect(Math.abs(signedHeadingDelta(value, 0))).toBeLessThan(3);
    }
  });

  test("smoother deadband and rate limit suppress tiny/too-frequent updates", () => {
    const smoother = new HeadingSmoother({ minIntervalMs: 50, deadbandDeg: 0.6 });
    expect(smoother.push(100, 0)).toBe(100);
    expect(smoother.push(100.2, 10)).toBeNull(); // rate limited
    expect(smoother.push(100.2, 200)).toBeNull(); // within deadband
    expect(smoother.push(110, 400)).not.toBeNull();
  });

  test("smoother follows a real turn quickly", () => {
    const smoother = new HeadingSmoother({ minIntervalMs: 0, deadbandDeg: 0 });
    smoother.push(0, 0);
    let last = 0;
    for (let t = 16; t <= 400; t += 16) last = smoother.push(90, t)!;
    expect(last).toBeGreaterThan(85);
  });
});
