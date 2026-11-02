/**
 * Cross-platform speech synthesis utility.
 *
 * Platform notes:
 * - Windows (WebView2):  Works. Voices load asynchronously after page load;
 *                        the default voice is selected automatically even before
 *                        the voice list is populated.
 * - macOS (WKWebView):   Works. Voices are available synchronously.
 * - Linux (WebKitGTK):   Uses the bundled Piper US-English voice through the
 *                        persistent Python sidecar when the Web Speech API is
 *                        absent. spd-say / espeak-ng / espeak remain fallback.
 * - Android WebView:     speechSynthesis is typically absent; MainActivity exposes
 *                        native TextToSpeech as window.AndroidTts, used first.
 * - iOS WKWebView:       Works when triggered from a user gesture; background
 *                        utterances may be blocked by the browser.
 */

import { invoke } from "@tauri-apps/api/core";
import { rpc } from "./sidecarRpc";

interface AndroidTtsBridge {
  isAvailable(): boolean;
  speak(text: string): void;
  stop(): void;
}

let linuxBackend: "piper" | "native" | null = null;
let linuxSpeechOperation: Promise<void> = Promise.resolve();

function enqueueLinuxSpeech(operation: () => Promise<void>): Promise<void> {
  const current = linuxSpeechOperation.then(operation, operation);
  // Keep later callouts usable after a failed backend operation while returning
  // the original result to the caller that needs to log it.
  linuxSpeechOperation = current.catch(() => undefined);
  return current;
}

/** Native Android text-to-speech, injected by MainActivity as `window.AndroidTts`. */
function androidTts(): AndroidTtsBridge | null {
  if (typeof window === "undefined") return null;
  const bridge = (window as unknown as { AndroidTts?: AndroidTtsBridge }).AndroidTts;
  return bridge && typeof bridge.speak === "function" ? bridge : null;
}

/**
 * Linux desktop Tauri (WebKitGTK) may be built without the Web Speech API.
 * The Python sidecar speaks through bundled Piper; Rust keeps the legacy
 * spd-say / espeak-ng / espeak fallback.
 */
function linuxNativeSpeech(): boolean {
  if (typeof window === "undefined" || "speechSynthesis" in window) return false;
  const tauri = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
  return !!tauri && /Linux/.test(navigator.userAgent) && !/Android/.test(navigator.userAgent);
}

/** Platform support only; Linux native engine availability is checked on speak. */
export function isSpeechSupported(): boolean {
  return androidTts() !== null || linuxNativeSpeech() || (typeof window !== "undefined" && "speechSynthesis" in window);
}

/** Stops any speech in progress on whichever backend is active. */
export async function cancelSpeech(): Promise<void> {
  const native = androidTts();
  if (native) { native.stop(); return; }
  if (linuxNativeSpeech()) {
    return enqueueLinuxSpeech(async () => {
      const backend = linuxBackend;
      linuxBackend = null;
      if (backend === "piper") await rpc("speech_stop");
      else if (backend === "native") await invoke("stop_native_speech");
    });
  }
  if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
}

/**
 * Speak `text` via native TTS or the Web Speech API. Callers must report errors.
 *
 * Applies the following cross-platform workarounds:
 * - Calls `cancel()` first to clear any stuck utterance queue.
 * - Calls `resume()` when synthesis is paused — Chrome and WebView2 can enter
 *   a paused state when the page loses focus and never auto-resume.
 * - Sets explicit rate/pitch/volume so defaults cannot vary silently across
 *   platform WebView versions.
 */
export async function speak(text: string): Promise<void> {
  if (!isSpeechSupported()) return;
  const native = androidTts();
  if (native) { native.speak(text); return; }
  if (linuxNativeSpeech()) {
    const spoken = text.replace(/\b(Azimuth|Elevation) (?=-?\d)/g, "$1, ");
    return enqueueLinuxSpeech(async () => {
      const backend = linuxBackend;
      linuxBackend = null;
      if (backend === "piper") await rpc("speech_stop");
      else if (backend === "native") await invoke("stop_native_speech");
      try {
        await rpc("speech_speak", { text: spoken });
        linuxBackend = "piper";
      } catch (piperError) {
        try {
          await invoke("speak_native", { text: spoken });
          linuxBackend = "native";
        } catch (fallbackError) {
          throw new Error(`Piper failed (${String(piperError)}); Linux speech fallback failed (${String(fallbackError)})`);
        }
      }
    });
  }
  const ss = window.speechSynthesis;
  ss.cancel();
  if (ss.paused) ss.resume();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.volume = 1.0;
  ss.speak(utterance);
}
