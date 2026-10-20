/**
 * Cross-platform speech synthesis utility.
 *
 * Platform notes:
 * - Windows (WebView2):  Works. Voices load asynchronously after page load;
 *                        the default voice is selected automatically even before
 *                        the voice list is populated.
 * - macOS (WKWebView):   Works. Voices are available synchronously.
 * - Linux (WebKitGTK):   API is present but speak() silently does nothing if
 *                        speech-dispatcher or espeak is not installed.
 *                        No reliable runtime detection of missing backends.
 * - Android WebView:     speechSynthesis is typically absent; MainActivity exposes
 *                        native TextToSpeech as window.AndroidTts, used first.
 * - iOS WKWebView:       Works when triggered from a user gesture; background
 *                        utterances may be blocked by the browser.
 */

import { invoke } from "@tauri-apps/api/core";

interface AndroidTtsBridge {
  isAvailable(): boolean;
  speak(text: string): void;
  stop(): void;
}

/** Native Android text-to-speech, injected by MainActivity as `window.AndroidTts`. */
function androidTts(): AndroidTtsBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as unknown as { AndroidTts?: AndroidTtsBridge }).AndroidTts;
  return bridge && typeof bridge.speak === "function" ? bridge : null;
}

/**
 * Linux desktop Tauri (WebKitGTK) may be built without the Web Speech API.
 * The Rust side then speaks through spd-say / espeak-ng / espeak.
 */
function linuxNativeSpeech(): boolean {
  if (typeof window === "undefined" || "speechSynthesis" in window) return false;
  const tauri = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  return !!tauri && /Linux/.test(navigator.userAgent) && !/Android/.test(navigator.userAgent);
}

/** Returns true when the platform exposes native TTS or the speechSynthesis API. */
export function isSpeechSupported(): boolean {
  return androidTts() !== null || linuxNativeSpeech() || (typeof window !== "undefined" && "speechSynthesis" in window);
}

/** Stops any speech in progress on whichever backend is active. */
export function cancelSpeech(): void {
  const native = androidTts();
  if (native) { native.stop(); return; }
  if (linuxNativeSpeech()) { void invoke("stop_native_speech").catch(() => {}); return; }
  if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
}

/**
 * Speak `text` via the Web Speech API.
 *
 * Applies the following cross-platform workarounds:
 * - Calls `cancel()` first to clear any stuck utterance queue.
 * - Calls `resume()` when synthesis is paused — Chrome and WebView2 can enter
 *   a paused state when the page loses focus and never auto-resume.
 * - Sets explicit rate/pitch/volume so defaults cannot vary silently across
 *   platform WebView versions.
 */
export function speak(text: string): void {
  if (!isSpeechSupported()) return;
  const native = androidTts();
  if (native) { native.speak(text); return; }
  if (linuxNativeSpeech()) { void invoke("speak_native", { text: text.replace(/\b(Azimuth|Elevation) (?=-?\d)/g, "$1, ") }).catch(() => {}); return; }
  const ss = window.speechSynthesis;
  ss.cancel();
  if (ss.paused) ss.resume();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.volume = 1.0;
  ss.speak(utterance);
}
