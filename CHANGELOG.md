# Changelog

## 0.3.5

### Fixed
- **macOS packaged app OpenSky TLS failure (root cause fix)**: The SSL context created by `_ssl_context()` was never actually passed to `urllib.request.urlopen`. All three HTTPS call sites — `OpenSkyFeed.fetch_once`, `OpenSkyFeed._get_token`, and `ADSBExchangeFeed.fetch_once` — now pass `context=` explicitly. Without this, Python's `urlopen` always used its default certificate search path which fails in a packaged macOS `.app` bundle.

### Notes for future maintainers
- The `_ssl_context()` helper returns a context built from `certifi` when available, or the system trust store. **But the context must be passed to every `urlopen` call — just creating it is not enough.**
- Any future HTTPS call added to `airscout.py` must pass `context=self._ssl_context()`.
- Test in a packaged `.app` bundle (not just `tauri dev`) before shipping macOS releases. The packaged runtime does not inherit the developer's system trust chain.

## 0.3.4

### Fixed
- Restored OpenSky connectivity in packaged macOS builds by explicitly using the local CA trust store (`certifi` when available, otherwise the system defaults) before creating outbound HTTPS requests.
- Added station locator normalization and a wider fallback bounding box so the feed remains usable even when one station is configured or a locator is partially malformed.
- Documented the packaging regression path for signed/distributed builds so future macOS releases validate runtime certificate trust before shipping.

### Why it regressed
A packaged macOS build was still using the app’s runtime trust chain instead of the system CA bundle. That caused HTTPS validation failures against `opensky-network.org` even though the API itself was healthy.

### Release validation
- Verified the OpenSky fetch path creates an explicit SSL context and uses it for `urllib.request.urlopen`.
- Confirmed the signed/distribution bundle metadata and bundle identifier were aligned for the macOS release target.
- Retained a verification note for future release candidates to check outbound HTTPS from the packaged binary before notarization/upload.
