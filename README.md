# AirWSJT-X
Merging Airscout and WSJT-X for AirScatter Hamradio contacts.

## Architecture

AirWSJT-X is implemented as a supervisor layer (recommended Option B) that keeps WSJT-X mostly untouched and coordinates:

- Airplane tracking/selection (`airwsjtx.tracker`)
- Contact state machine (`airwsjtx.state_machine`)
- Rotator lock control (`airwsjtx.rotator`)
- WSJT-X control adapter for 23cm+ (`airwsjtx.wsjtx`)
- PSK Reporter interoperability profile (`airwsjtx.psk`)
- End-to-end orchestration (`airwsjtx.supervisor`)

This treats the selected airplane as a low-orbit dynamic target and only enables Q65 15s operation when:

1. Two stations are configured.
2. A viable aircraft is selected and locked.
3. Rotator lock remains stable.
4. WSJT-X is configured for a supported 23cm+ band.
5. The operator explicitly starts the QSO.

When multiple WSJT-X instances are on the same network, use the **Rig** filter in the top bar to select a specific WSJT-X source by rig/config name.

## PSK Reporter strategy

AirWSJT-X uses WSJT-X native reporting as the primary PSK Reporter path.
The AirScatter profile provides operating conventions for discoverability while avoiding non-standard reporter protocol hacks.

## Running tests

```bash
python -m unittest discover -s tests
```

## WSJT-X status strip TX indicator

- `TX○` means WSJT-X is idle (not transmitting).
- Red `TX●` means WSJT-X is currently transmitting (PTT ON).
- `TX <value>Hz` shows the current TX frequency offset (`tx_df`) reported by WSJT-X.
