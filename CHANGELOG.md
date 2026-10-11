# Changelog

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
