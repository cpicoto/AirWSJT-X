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
 * - Android WebView:     speechSynthesis is typically absent; isSpeechSupported()
 *                        returns false and all calls become no-ops.
 * - iOS WKWebView:       Works when triggered from a user gesture; background
 *                        utterances may be blocked by the browser.
 */

/** Returns true when the platform exposes a speechSynthesis API. */
export function isSpeechSupported(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window;
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
  const ss = window.speechSynthesis;
  ss.cancel();
  if (ss.paused) ss.resume();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.0;
  utterance.pitch = 1.0;
  utterance.volume = 1.0;
  ss.speak(utterance);
}
