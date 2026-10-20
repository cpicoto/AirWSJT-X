import { isMobile } from "./platform";
import type { SkyDomeAircraft } from "../SkyDome";

export type FloatKind = "skydome" | "overlay";

export const FLOAT_STATE_EVENT = "airwsjtx-float-state";
export const FLOAT_READY_EVENT = "airwsjtx-float-ready";
export const FLOAT_ACTION_EVENT = "airwsjtx-float-action";
export const FLOAT_CLOSED_EVENT = "airwsjtx-float-closed";

export interface FloatState {
  aircraft: SkyDomeAircraft[];
  selectedAircraftId: string | null;
  stationLocator: string | null;
  stationAltitudeM: number;
  rotatorAz: number;
  rotatorEl: number;
  targetAz: number | null;
  targetEl: number | null;
  pathBearing: number | null;
  dxLabel: string | null;
  myLabel: string | null;
  readoutAz: number;
  readoutEl: number;
  readoutInvalid: boolean;
}

export type FloatAction =
  | { type: "lock"; icao24: string }
  | { type: "sky"; az: number; el: number };

const TITLES: Record<FloatKind, string> = { skydome: "Sky Dome", overlay: "Az / El" };
const DEFAULT_SIZES: Record<FloatKind, { w: number; h: number }> = {
  skydome: { w: 520, h: 600 },
  overlay: { w: 600, h: 200 },
};

interface SavedRect { x: number; y: number; w: number; h: number }

export function floatRectKey(kind: FloatKind): string {
  return `airwsjtx.floatRect.${kind}`;
}

export function readFloatRect(kind: FloatKind): SavedRect | null {
  try {
    const p = JSON.parse(localStorage.getItem(floatRectKey(kind)) ?? "null");
    if (p && [p.x, p.y, p.w, p.h].every(Number.isFinite) && p.w >= 100 && p.h >= 80) return p;
  } catch { /* ignore corrupt value */ }
  return null;
}

// Real native windows exist only inside the desktop Tauri shell (never mobile or a plain browser).
export function nativeFloatSupported(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window && !isMobile();
}

export function floatViewFromLocation(): FloatKind | null {
  const view = new URLSearchParams(window.location.search).get("view");
  return view === "skydome" || view === "overlay" ? view : null;
}

// Open/close requests for one window run strictly in order so a quick Hide→Show never races a half-destroyed window.
const queues = new Map<FloatKind, Promise<void>>();

export function syncFloatWindow(kind: FloatKind, wanted: boolean): Promise<void> {
  const prev = queues.get(kind) ?? Promise.resolve();
  const next = prev.catch(() => {}).then(() => (wanted ? createFloatWindow(kind) : closeFloatWindow(kind)));
  queues.set(kind, next);
  return next;
}

async function createFloatWindow(kind: FloatKind): Promise<void> {
  const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
  if (await WebviewWindow.getByLabel(kind)) return;
  const saved = readFloatRect(kind);
  const size = DEFAULT_SIZES[kind];
  const win = new WebviewWindow(kind, {
    url: `index.html?view=${kind}`,
    title: TITLES[kind],
    parent: "main",
    width: saved?.w ?? size.w,
    height: saved?.h ?? size.h,
    ...(saved ? { x: saved.x, y: saved.y } : { center: true }),
    minWidth: 220,
    minHeight: 120,
    resizable: true,
    focus: false,
  });
  // "tauri://created" can fire before the listener below is attached, so never wait on it indefinitely.
  await new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(resolve, 1500);
    void win.once("tauri://created", () => { window.clearTimeout(timer); resolve(); });
    void win.once("tauri://error", (e) => { window.clearTimeout(timer); reject(new Error(String(e.payload))); });
  });
}

async function closeFloatWindow(kind: FloatKind): Promise<void> {
  const { WebviewWindow } = await import("@tauri-apps/api/webviewWindow");
  const win = await WebviewWindow.getByLabel(kind);
  if (!win) return;
  // destroy() skips the close-requested event so it isn't mistaken for the user clicking X.
  await win.destroy();
  for (let i = 0; i < 20 && (await WebviewWindow.getByLabel(kind)); i++) {
    await new Promise((r) => setTimeout(r, 50));
  }
}
