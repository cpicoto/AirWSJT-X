# User guide

Short how-to notes for the desktop app. This guide currently covers the Rotator panel's Auto and Manual modes and the Stations panel.

## Auto and Manual mode

The **Auto** and **Manual** buttons at the top of the rotator panel choose what drives the pointing target. The rotor on/off button next to them is separate and applies in both modes.

| | **Auto** | **Manual** |
|---|---|---|
| Target comes from | The locked aircraft (azimuth and elevation from your station to the plane) | You |
| How you set it | Lock a plane in the Plane Lock panel, or turn on **Auto-lock** so the best-scoring candidate is locked for you | Click the compass or the elevation arc, or type Az/El and press **Go** |
| Rotator commands | Sent automatically as the plane moves, when rotor control is on and the rotator is connected | Sent only when you click or press **Go** |
| Speech callouts | Announce the pointing for the locked plane | Announce the manual target when the rotor is on |
| **Sync** | Disabled while rotor control is on | Sets the target to the rotator's reported position without moving it |

### Auto mode details

- **Plane lock lost:** the last known Az and El stay on screen, greyed out to show they are no longer valid. No new rotator commands are sent, so the rotator stays where it was. The app announces "Lost plane lock" if speech is on.
- **Next plane locked:** the values return to normal and the new Az and El are sent to the rotator.
- **No rotator needed:** Auto mode speech works even when the rotator is off or not connected, so you can point an antenna by voice. Speech is silent in Auto while there is no valid plane lock.
- **Which plane:** candidates are ranked by a geometry score; see [Plane scoring](plane-scoring.md).
- **Watch a known flight:** in **Scatter Aircraft**, enter its flight number in **Flight number**. A matching aircraft bypasses the other aircraft filters and is scored like any other candidate. It still needs reported position and altitude data. Leave **Only** unchecked to include it with normally filtered aircraft, or check **Only** to show just that flight.
  Once locked, this flight remains locked even when its scatter score drops below 5%, so you can track its approach from outside the scatter window. Clearing or changing the flight number restores the normal low-score automatic release on the next tracking update. Manual unlock remains available.
  Press **Store Flight#** to save the current flight number and **Only** setting locally. The **Flight favorites** picker shows two columns: flight number and Only (Yes/No), with an explicitly dark popup for Linux webviews. Selecting an entry restores both settings immediately. Like DX favorites, the list holds the 20 most recently stored entries; storing the same flight/mode pair again moves it to the top without duplicating it. The same flight can be saved with either mode. Typing or selecting a flight does not add it to favorites until you press **Store Flight#**.

### Flight Data and candidate highlights

**Flight Data**, below the aircraft sources, shows all received aircraft with valid airborne position and altitude data, without candidate filters or the candidate payload limit. It is scrollable and sorted by scatter score, highest first. Rows matching any locally saved flight favorite have a grey background.

**Top Candidates** keeps its existing filters, ranking, and five-entry limit. The currently selected **Flight number** is underlined in its text color when it appears there; saved favorites do not change candidate selection.

The active flight starts blank with **Only** off at startup; saved flight favorites remain available. **Clear**, next to **Store Flight#**, disables the active flight filter without deleting favorites. Choose a favorite to activate its flight number and Only setting again.

Numeric aircraft filters use compact fields inside the **Filters** section, collapsed by default. Click **Filters** to expand or collapse them without changing their values. The Flight number controls remain outside this section and visible. The Flight number label sits above its input. Flight controls share a row below the numeric filters when space permits, and wrap onto another row on narrow windows so the favorites picker stays visible.

Drag the bottom-right corner grip of the Flight Data scroll area or Log card to resize it vertically. Each height is saved locally and restored at startup; widths continue to follow the window layout. The Log is scrollable and retains the latest 30 entries, newest first.

### Manual mode details

- Clicking the compass or elevation arc moves the rotator straight away when the rotor is on.
- If rotor control is off, clicks are blocked and **Stop** remains available to halt a move already in progress.
- Switching back to Auto returns the target to the locked plane.

### Rotor control and the position lines

- The rotor on/off button (green when on and connected, red otherwise) blocks all new movement commands when off.
- The blue current-position lines on the Az and El compasses turn grey when the rotator is not connected, and return to blue once there is valid position data.
## Stations panel

The Stations panel sets the two ends of the path: **MY** (your station) and **DX**.

The last-used DX callsign, grid, altitude and Local/WSJT-X source mode are restored on startup. In WSJT-X mode, new WSJT-X station data still takes precedence.

Each station has one line with a callsign box, a grid box and a source toggle (callsigns and grids are limited to 10 characters). The altitude box and its button sit on the line below.

### WSJT-X or Local

The toggle button shows which source is in use:

| Label | Meaning |
|-------|---------|
| **WSJT-X** | The station follows the call and valid grid WSJT-X reports. The callbook grid is used only when WSJT-X sends no valid grid. |
| **Local** | The station uses the call and grid typed in the boxes. |

- Click the button to switch between the two.
- For DX, typing in the callsign or grid box switches to **Local**, and the typed values take effect a moment after you stop typing, once the call and grid are complete and valid.
- Callsigns with markers such as `/P`, `/M` or a prefix (`VE7/W7GLF`) are accepted. The callbook lookup also tries the home callsign.
- Grids can be 4, 6 or 8 characters.

### DX altitude

Enter the DX station's altitude above sea level in metres (default 0). The sidecar uses it for elevation and scoring of aircraft on the path. Altitude is kept with a station only when you press **Store DX** (below).

### DX favorites

Favorites are DX stations you save for quick reuse.

**Store a station**

1. Set the DX call, grid and altitude (by WSJT-X or by typing in Local mode).
2. Press **Store DX**.

The station is saved with its call, grid and altitude. In WSJT-X mode, **Store DX** saves the current WSJT-X station without changing the mode.

**Use a favorite**

1. Open the **DX favorites** drop-down.
2. Pick an entry. Each shows aligned call, grid and altitude columns, for example `VE7NX  CN89oh  0 m`.

DX and Flight favorites use the same dark popup and monospace entry font. Their expanded lists contain only saved entries, without a duplicate title row.
Use the arrow keys to open and navigate a favorites list, Home/End to jump to its first/last entry, Enter/Space to select, or Escape to cancel.

Picking an entry sets the DX call, grid and altitude, and switches DX to **Local**.

**How the list works**

- It holds up to **20** stations, newest at the top. Storing a 21st drops the oldest.
- The same call can appear more than once if its grid or altitude differs.
- Storing an exact match (same call, grid and altitude) moves that entry to the top instead of adding a copy.
- Stations are saved only when you press **Store DX**. Calls from WSJT-X, typing, or **+ Other** do not add to the list.
- The list is kept between sessions. It starts with VE7NX and W7PUA.

**+ Other** looks up any callsign in the callbook and sets it as the DX station. It does not store it; press **Store DX** to keep it.

### Callsign database

**DB Check**, **DB Update** and **Contribute** are on their own row below the favorites. They check for, download and submit entries to the callsign grid database.
