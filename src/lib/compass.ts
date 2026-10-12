// Pure helpers for turning phone orientation sensor data into a steady pointing heading.

export function normalizeDegrees(value: number): number {
  return ((value % 360) + 360) % 360;
}

/** Shortest signed rotation (-180..180) that takes `current` to `target`. */
export function signedHeadingDelta(target: number, current: number): number {
  return ((((target - current) % 360) + 540) % 360) - 180;
}

const DEG = Math.PI / 180;

/**
 * Compass heading (0..360, clockwise from north) of the direction the phone is
 * pointing, from W3C alpha/beta/gamma (alpha is counter-clockwise from north).
 *
 * `alpha - screenAngle` is only valid with the phone lying flat; when it is held
 * upright to aim at the sky, alpha is dominated by gimbal coupling with beta/gamma
 * and swings wildly. Instead the device axes are rotated into the world frame and
 * the horizontal parts of the screen-top axis (flat) and the rear camera axis
 * (upright) are blended across a small transition around vertical.
 */
export function headingFromOrientation(
  alphaDeg: number,
  betaDeg: number,
  gammaDeg: number,
  screenAngleDeg = 0,
): number | null {
  const a = alphaDeg * DEG;
  const b = betaDeg * DEG;
  const g = gammaDeg * DEG;
  const cA = Math.cos(a), sA = Math.sin(a);
  const cB = Math.cos(b), sB = Math.sin(b);
  const cG = Math.cos(g), sG = Math.sin(g);

  // World-frame (x = east, y = north) components of the device axes.
  const xAxis = [cA * cG - sA * sB * sG, cG * sA + cA * sB * sG];
  const yAxis = [-cB * sA, cA * cB];
  const zAxis = [cG * sA * sB + cA * sG, sA * sG - cA * cG * sB];

  const s = Math.sin(screenAngleDeg * DEG);
  const c = Math.cos(screenAngleDeg * DEG);
  const topEast = s * xAxis[0] + c * yAxis[0];
  const topNorth = s * xAxis[1] + c * yAxis[1];
  const screenNormalVertical = cB * cG;
  const transition = Math.max(0, Math.min(1, (0.15 - screenNormalVertical) / 0.3));
  const cameraWeight = transition * transition * (3 - 2 * transition);
  const east = (1 - cameraWeight) * topEast - cameraWeight * zAxis[0];
  const north = (1 - cameraWeight) * topNorth - cameraWeight * zAxis[1];

  if (Math.hypot(east, north) < 0.1) return null;
  return normalizeDegrees(Math.atan2(east, north) / DEG);
}

export interface HeadingSmootherOptions {
  /** Low-pass time constant for small movements (ms). */
  tauMs?: number;
  /** Time constant used when turning quickly, so aiming stays responsive (ms). */
  fastTauMs?: number;
  /** Delta (deg) above which the fast time constant is used. */
  fastThresholdDeg?: number;
  /** Minimum change (deg) from the last published value before publishing. */
  deadbandDeg?: number;
  /** Minimum interval between published updates (ms). */
  minIntervalMs?: number;
}

/**
 * Time-based circular exponential low-pass filter with a publish deadband and
 * rate limit. Filtering works on the shortest signed delta, so 359° -> 1° never
 * sweeps through 180°.
 */
export class HeadingSmoother {
  private readonly tauMs: number;
  private readonly fastTauMs: number;
  private readonly fastThresholdDeg: number;
  private readonly deadbandDeg: number;
  private readonly minIntervalMs: number;
  private filtered: number | null = null;
  private lastAt = 0;
  private published: number | null = null;
  private publishedAt = 0;

  constructor(options: HeadingSmootherOptions = {}) {
    this.tauMs = options.tauMs ?? 180;
    this.fastTauMs = options.fastTauMs ?? 50;
    this.fastThresholdDeg = options.fastThresholdDeg ?? 20;
    this.deadbandDeg = options.deadbandDeg ?? 0.6;
    this.minIntervalMs = options.minIntervalMs ?? 50;
  }

  reset(): void {
    this.filtered = null;
    this.published = null;
  }

  /** Feed a raw heading; returns a value to publish, or null if nothing should change. */
  push(rawHeading: number, nowMs: number): number | null {
    const raw = normalizeDegrees(rawHeading);
    if (this.filtered === null) {
      this.filtered = raw;
      this.lastAt = nowMs;
      this.published = raw;
      this.publishedAt = nowMs;
      return raw;
    }

    const dt = Math.max(0, nowMs - this.lastAt);
    this.lastAt = nowMs;
    const delta = signedHeadingDelta(raw, this.filtered);
    const tau = Math.abs(delta) > this.fastThresholdDeg ? this.fastTauMs : this.tauMs;
    const gain = 1 - Math.exp(-dt / tau);
    this.filtered = normalizeDegrees(this.filtered + delta * gain);

    if (this.published !== null) {
      if (nowMs - this.publishedAt < this.minIntervalMs) return null;
      if (Math.abs(signedHeadingDelta(this.filtered, this.published)) < this.deadbandDeg) return null;
    }
    this.published = this.filtered;
    this.publishedAt = nowMs;
    return this.filtered;
  }
}
