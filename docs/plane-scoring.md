# Plane scoring

How AirWSJT-X decides which aircraft are candidates, how they are ranked, and when auto-lock and tracking act on the score. Source: [`airwsjtx/geo.py`](../airwsjtx/geo.py) and [`sidecar/airwsjtx_bridge.py`](../sidecar/airwsjtx_bridge.py).

## The score

The candidate list, auto-lock and the percentage shown in the UI all use `scatter_geometry()`. It returns a score from 0 to 1, displayed as 0–100%.

With the same aircraft snapshot, filters and physical station altitudes, swapping MY and DX preserves candidate membership, scores, ranking and auto-lock selection. Station-specific azimuth, elevation and range readouts swap ends. Live lists can still differ with source coverage or update timing; check the reported active source, not just which sources are enabled.

**Hard gate:** if the plane is at or below the horizon from station A **or** station B (elevation ≤ 0°), the score is 0.

When MY and DX are different locations, the aircraft must be within the inward-facing half-plane at both endpoints: less than 90° off MY-to-DX as seen from MY, and less than 90° off DX-to-MY as seen from DX. Aircraft behind MY, beyond DX, or on either 90° boundary score 0. This applies to all aircraft, including a watched flight; watched flights can still appear and remain locked at 0%. It does not apply when MY and DX are the same location (no path set).

Otherwise the score is a weighted sum of four parts:

| Weight | Part | Computation |
|---|---|---|
| 30% | Elevation from A | `el / 20` up to 20°, then falls linearly to 0 at 40°. Best at 20°. |
| 30% | Elevation from B | Same curve, using B's elevation. |
| 25% | Path alignment | `1 − off_path / 45°`, where `off_path` is the larger of the A→plane versus A→B and B→plane versus B→A bearing deviations. 0 at 45° or more off at either end. The UI's Off° uses this same worse-end value. |
| 15% | Range | `1 − max(slant range A, slant range B) / 800 km`. Closer is better. |

## Filters

`_aircraft_matches_filters` decides whether a plane is shown or auto-locked at all. These apply in addition to the score:

- **Lat/lon box:** optional minimum and maximum values.
- **Minimum altitude:** optional, in feet.
- **Position tolerance:** default 70°. At both endpoints, the plane's bearing must be within this of the bearing toward the other station. Skipped when A and B are the same point.
- **Heading tolerance:** default 30°. If the plane reports a heading and nonzero speed, its heading must be within this of both endpoint path axes, accepting travel in either direction. A value of 0 means exact alignment, not the default tolerance.
- **Flight number:** a matching flight bypasses these filters, but uses the same geometry score. **Only** restricts candidates to that flight; otherwise it is included alongside normally filtered aircraft.

## How the score is used

- **Auto-lock:** runs only from idle, completed or aborted states. Picks the highest score of at least 0.05 among planes that pass the filters.
- **Tracking:** every update recomputes the score. Below 0.05 the plane is out of window: `plane_out_of_window` is emitted and the plane is unlocked, unless it matches the watched flight number. The watched flight remains locked even at 0%.
- **Sorting:** the candidate list is sorted by score, highest first.

## Limitations

Flight Data's **Use** column is a separate, display-only prediction of remaining time inside the geometry hard gates (mutual radio visibility and inward-facing endpoint bearings). It does not use optional filters or the score threshold and has no effect on ranking or selection. See [the user guide](user-guide.md#flight-data-and-candidate-highlights) for assumptions, unavailable values and the 30-minute cap. Implementation: [`src/lib/usableTime.ts`](../src/lib/usableTime.ts).

- **Station altitudes:** the bridge passes both MY (station A) and DX (station B) altitudes to `scatter_geometry`. Each defaults to 0 m unless entered in the Stations panel.
- **Altitude:** apart from the optional minimum-altitude filter, altitude only enters through the elevation angles.
- **Speed, heading, vertical rate:** these do not affect the score. Heading acts only as a pass/fail filter.
- **Separate ranking:** [`airwsjtx/tracker.py`](../airwsjtx/tracker.py) has a different ranking based on average elevation, range and track stability. Only the `rank_candidates` RPC and the supervisor use it. The UI and auto-lock do not.
