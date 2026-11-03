# Plane scoring

How AirWSJT-X decides which aircraft are candidates, how they are ranked, and when auto-lock and tracking act on the score. Source: [`airwsjtx/geo.py`](../airwsjtx/geo.py) and [`sidecar/airwsjtx_bridge.py`](../sidecar/airwsjtx_bridge.py).

## The score

The percentage shown in the UI and lock eligibility use `scatter_geometry()`. It returns a score from 0 to 1, displayed as 0–100%. Ranking starts with this score or one of the **Best-path ranking** indices below, then applies optional aircraft scatter preferences. The displayed Score is never modified by aircraft metadata. Score is not SNR or a success percentage.

With the same aircraft snapshot, filters and physical station altitudes, swapping MY and DX preserves candidate membership, scores, ranking and auto-lock selection. Station-specific azimuth, elevation and range readouts swap ends. Live lists can still differ with source coverage or update timing; check the reported active source, not just which sources are enabled.

**Hard gate:** if the plane is at or below the horizon from station A **or** station B (elevation ≤ 0°), the score is 0.

When MY and DX are different locations, the aircraft must be within the inward-facing half-plane at both endpoints: less than 90° off MY-to-DX as seen from MY, and less than 90° off DX-to-MY as seen from DX. Aircraft behind MY, beyond DX, or on either 90° boundary score 0. This applies to all aircraft, including a watched flight, and ends the selection's mutual QSO window. It does not apply when MY and DX are the same location (no path set).

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

## Selectable ranking

Desktop stores **Best-path ranking** with the existing station-pair filter settings and applies it in the backend, including candidate lists, Flight Data and auto-lock selection. Mobile has the same selector in Settings, stored with its device settings; it ranks its locally fetched aircraft. The legacy percentage stays visible under all methods.

| Method | Ordering / displayed Rank |
|---|---|
| **Legacy geometry (default)** | Descending legacy percentage with optional scatter preferences; unchanged values when metadata is unknown. |
| **Range product** | Descending `20 log10(250² / (range_A × range_B))`, displayed in **relative dB** against 250/250 km slant legs. Uses independently computed, unrounded slant ranges. Not absolute received power or SNR. |
| **Window-weighted range product** | Descending `range_relative_dB + 10 log10(remaining_seconds / 60)`, displayed as **index**. Equivalent to current linear range potential multiplied by remaining geometric minutes. Not integrated received energy or QSO-success probability. |

Both alternatives retain the legacy geometry gates. Nonpositive/invalid ranges have no estimate, not an endpoint infinity. A fixed reference makes relative values comparable between updates; its choice does not impose a radio threshold. The method is symmetric when the same physical station coordinates and heights swap.

The window method uses the same constant-altitude, ground-speed, great-circle first-exit model as **Use**, capped at 30 minutes. It subtracts report age from the predicted duration (it does not extrapolate the current range potential). Position age must be 0–45 seconds, matching the existing fresh-cache interval; timestamp, finite track and positive speed must be known. Missing, stale or future-dated position or unavailable motion produces an explicit **Range fallback**. Candidates with known positive windows rank ahead of fallback candidates; fallback candidates are ordered by range potential. Within each group, range potential breaks equal-index ties.

This heuristic trades instantaneous potential for geometric duration without inventing a decode threshold or a fixed complete-QSO duration. It does not evaluate future entry, apply optional filters throughout the future trajectory, predict changing RCS/gain or signal strength, or guarantee that a Q65 exchange can finish. The 30-minute cap is a forecast bound, not an exact exit. The separate Use display still describes the report-time geometric window and is not the age-reduced ranking duration.

Altitude, terrain clearance, antenna patterns, scattering aspect, Doppler spread and actual decoder performance can make the strongest usable path differ from the range-only favorite. Altitude helps visibility but is not an unconditional power bonus.

## Aircraft category and status preferences

**Aircraft scatter preferences** is enabled by default in desktop **Filters** and mobile **Settings**, and is persisted with the ranking policy. Disable it to use geometry-only ordering. It affects candidates, Flight Data, automatic selection and handover, not the displayed legacy Score, geometry gates, lock threshold, or an already viable selection.

These are modest heuristic preferences, **not measured radar cross-section or received power**:

| Input | Preference |
|---|---|
| A1 light (<7 tonnes), A6 high-performance | 0 dB |
| A2 medium (7–34 tonnes) | +0.2 dB |
| A3 medium (34–136 tonnes) | +0.4 dB |
| A4 high-vortex (e.g. B757) | +0.5 dB |
| A5 heavy (>136 tonnes) | +0.6 dB |
| A7 rotorcraft, B-class non-powered/special aircraft | −3 dB |
| Finite vertical rate with absolute value >2 m/s | −0.2 dB |
| Known position age >45 seconds | −0.6 dB |
| Missing/A0/B0/C0/unrecognized category, unavailable rate or age | Neutral for that input |
| On ground or known C-class surface target | Not eligible, even with preferences disabled or a watched flight |

Legacy ordering multiplies its geometric score by `10^(preference_dB/10)`; range/window indices add the preference. Range fallback uses the same adjustment without changing the pure `range_gain_db`. Stable altitude is preferred by a small climb/descent penalty, not by a bonus for having data. Missing data is never penalized. Constants are centralized in `airwsjtx/ranking.py` and `src/lib/aircraftMetadata.ts`, with exhaustive cross-language parity tests; changing weights requires updating both implementations and tests. Exact final ties use ascending ICAO address, not source arrival order.

**Both ends:** the preference has no MY/DX-specific term. Identical aircraft data, ranking settings, station geometry and evaluation time produce identical ordering when the stations swap. There is no station-to-station exchange of aircraft category/status in the existing QSO control protocol. Different source coverage, missing categories, stale reports, clocks or mobile DX-altitude assumptions can still yield different choices. For coordinated operation, agree on the same source and policy and confirm the selected ICAO at both ends; disable preferences for geometry-only coordination when metadata differs. Disabling preferences cannot make differing geometry snapshots identical. This feature does not add an unnegotiated extension to WSJT-X/Q65 messages.

Size/status preferences cannot admit a plane outside the mutual geometry window. Ground/surface changes release a selection and disarm QSO controls; ordinary missing-position retention and source-specific pointing expiry are unchanged. Emergency squawks, emergency text, military/interesting/PIA/LADD flags, registration and ICAO type codes are **display-only** and never influence ranking.

Emitter categories come from validated DF17 identification messages (TC4=A, TC3=B, TC2=C; CA0/TC1 unknown) in both Python and Android native decoders, or readsb-format ADS-B Exchange JSON. The existing dump1090/readsb connection uses SBS TCP, which does not transmit these fields; it remains compatible and unknown-neutral rather than guessing categories from callsigns.

Sky Dome, candidate and mobile icons use original silhouettes for light, small/medium/heavy jets, high-performance aircraft, helicopters, gliders, balloons and drones, or a generic aircraft when unknown. A6 does not imply military. Type codes may refine a medium/unknown icon into a widebody, but never a ranking bonus. Silhouettes follow reported heading; climb/descent arrows remain upright. A red outline marks 7500/7600/7700 or an emergency report; database badges are MIL (bit 1), INT (bit 2), PIA (bit 4), LADD (bit 8), per the [ADS-B Exchange schema](https://www.adsbexchange.com/version-2-api/). Tooltips include category, type, registration, description, vertical rate, emergency/flags and source. Report/position age fades icons after 15 seconds, down to 35% opacity; unknown age does not fade. Ground/surface targets are hidden from scatter lists. No airline logos or third-party SVGs are used.

## How the score and ranking are used

- **Auto-lock:** runs only from idle, completed or aborted states. Picks the highest-ranked aircraft among planes passing the filters with a **legacy score** of at least 0.05. Relative dB/index values are never compared with 0.05.
- **Automatic tracking:** every update checks the same mutual visibility and inward-bearing gates. Leaving the window emits `plane_out_of_window`, releases the selection and permits automatic handover. A watched flight does not bypass these gates. Ground/surface reports also release the selection.
- **Manual selection:** identity survives score/filter changes and temporary missing positions, up to 180 seconds without a local ADS-B record (120 seconds for network sources). Stale positions do not drive DDE/Hamlib. Explicit unlock, source change, ground report and retention expiry still release it.
- **Sorting:** candidates and Flight Data follow the selected method. Aircraft outside geometry gates remain behind eligible geometry in alternative rankings, even when a watched flight bypasses optional filters.
- **Active locks:** selecting another method does not choose a different aircraft or change watched-flight exemptions. Automatic locks retain their geometry policy; manual selection remains independent of ranking.

## Limitations

Flight Data's **Use** column is a separate report-time prediction of remaining time inside the geometry hard gates (mutual radio visibility and inward-facing endpoint bearings). It does not use optional filters or the score threshold. Legacy and range-product methods ignore duration; window-weighted ranking uses the same geometric model with the age and availability rules above. See [the user guide](user-guide.md#flight-data-and-candidate-highlights). Implementations: [`airwsjtx/ranking.py`](../airwsjtx/ranking.py), [`src/lib/aircraftRanking.ts`](../src/lib/aircraftRanking.ts), [`src/lib/usableTime.ts`](../src/lib/usableTime.ts).

Use is not an aircraft-retention countdown. Each position/heading/speed/altitude report recalculates it under constant-motion assumptions: a turn or descent can change a 20-minute estimate to 5 minutes at the next update. Missing fresh velocity shows an unavailable estimate, not a deleted aircraft. Local positions remain visible for less than 120 seconds after their last fix; the underlying record is retained for 180 seconds after its last valid message. Retained coordinates are not live or extrapolated positions.

- **Station altitudes:** the bridge passes both MY (station A) and DX (station B) altitudes to `scatter_geometry`. Each defaults to 0 m unless entered in the Stations panel.
- **Altitude:** enters both elevation angles and slant ranges, in addition to the optional minimum-altitude filter.
- **Speed, heading, vertical rate:** speed/heading affect window-weighted ranking, not the legacy score. Heading also acts as an optional pass/fail filter. Vertical rate contributes a small optional selection preference, but altitude change is not projected into the geometry prediction.
- **Mobile:** uses MY altitude and assumes DX altitude is zero, as before. OpenSky, ADS-B Exchange and native dump1090 provide position age when known; unavailable age is not silently treated as fresh.
- **Separate ranking:** [`airwsjtx/tracker.py`](../airwsjtx/tracker.py) has a different ranking based on average elevation, range and track stability. Only the `rank_candidates` RPC and the supervisor use it. The UI and auto-lock do not.
