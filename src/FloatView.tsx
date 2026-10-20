import { useEffect, useState } from "react";
import { emit, listen } from "@tauri-apps/api/event";
import SkyDome from "./SkyDome";
import AzElReadout from "./AzElReadout";
import {
  FLOAT_ACTION_EVENT, FLOAT_CLOSED_EVENT, FLOAT_READY_EVENT, FLOAT_STATE_EVENT,
  floatRectKey, type FloatAction, type FloatKind, type FloatState,
} from "./lib/floatWindow";

// Content of a native floating window (Sky Dome or Az/El readout). All data comes from the main window.
export default function FloatView({ kind }: { kind: FloatKind }) {
  const [state, setState] = useState<FloatState | null>(null);

  useEffect(() => {
    document.documentElement.classList.add("float-view", `float-view-${kind}`);
    const unlisten = listen<FloatState>(FLOAT_STATE_EVENT, (e) => setState(e.payload));
    void emit(FLOAT_READY_EVENT, { kind });
    return () => { void unlisten.then((f) => f()); };
  }, [kind]);

  // Remember this window's geometry and tell the main window when the user closes it.
  useEffect(() => {
    let cleanup: Array<() => void> = [];
    let disposed = false;
    void (async () => {
      const { getCurrentWindow } = await import("@tauri-apps/api/window");
      const win = getCurrentWindow();
      const save = async () => {
        const [pos, size, scale] = await Promise.all([win.outerPosition(), win.innerSize(), win.scaleFactor()]);
        localStorage.setItem(floatRectKey(kind), JSON.stringify({
          x: Math.round(pos.x / scale), y: Math.round(pos.y / scale),
          w: Math.round(size.width / scale), h: Math.round(size.height / scale),
        }));
      };
      const offs = await Promise.all([
        win.onMoved(() => void save()),
        win.onResized(() => void save()),
        win.onCloseRequested(() => { void emit(FLOAT_CLOSED_EVENT, { kind }); }),
      ]);
      if (disposed) offs.forEach((f) => f()); else cleanup = offs;
    })();
    return () => { disposed = true; cleanup.forEach((f) => f()); };
  }, [kind]);

  if (!state) return null;
  const send = (action: FloatAction) => { void emit(FLOAT_ACTION_EVENT, action); };
  const grip = (
    <div
      className="float-grip"
      title="Drag to resize"
      onMouseDown={(e) => {
        e.preventDefault();
        void import("@tauri-apps/api/window").then(({ getCurrentWindow }) => getCurrentWindow().startResizeDragging("SouthEast"));
      }}
    />
  );

  if (kind === "overlay") {
    return (
      <div className="float-readout-wrap">
        <AzElReadout
          className="portable-overlay float-readout"
          az={state.readoutAz} el={state.readoutEl}
          invalid={state.readoutInvalid}
        />
        {grip}
      </div>
    );
  }
  return (
    <section className="skydome-panel float-skydome" aria-label="Sky Dome">
      {grip}
      <SkyDome
        aircraft={state.aircraft}
        selectedAircraftId={state.selectedAircraftId}
        stationLocator={state.stationLocator}
        stationAltitudeM={state.stationAltitudeM}
        rotatorAz={state.rotatorAz}
        rotatorEl={state.rotatorEl}
        targetAz={state.targetAz}
        targetEl={state.targetEl}
        pathBearing={state.pathBearing}
        dxLabel={state.dxLabel}
        myLabel={state.myLabel}
        onClickAircraft={(icao24) => send({ type: "lock", icao24 })}
        onClickSky={(az, el) => send({ type: "sky", az, el })}
      />
    </section>
  );
}
