import { useRef, useState } from "react";
import appLicense from "../LICENSE?raw";
import dump1090License from "../scripts/dump1090-fa/COPYING?raw";
import dump1090Notices from "../scripts/dump1090-fa/UPSTREAM.txt?raw";
import { version } from "../package.json";

const repository = "https://github.com/cpicoto/AirWSJT-X";
const sourceUrl = `${repository}/archive/${__AIRWSJTX_SOURCE_REVISION__}.tar.gz`;

export default function OpenSourceNotices() {
  const dialog = useRef<HTMLDialogElement>(null);
  const [error, setError] = useState("");

  async function openLink(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault();
    const url = event.currentTarget.href;
    setError("");
    try {
      if ("__TAURI_INTERNALS__" in window) {
        const { open } = await import("@tauri-apps/plugin-shell");
        await open(url);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
    } catch (cause) {
      setError(`Cannot open link: ${String(cause)}. Copy the displayed URL into your browser.`);
    }
  }

  return (
    <>
      <button type="button" onClick={() => dialog.current?.showModal()}>
        Open-source notices
      </button>
      <dialog ref={dialog} aria-labelledby="open-source-title"
        style={{ background: "#161b22", color: "#c9d1d9", border: "1px solid #8b949e",
          borderRadius: 8, padding: 20, width: 640, maxWidth: "calc(100vw - 48px)",
          maxHeight: "80vh", overflowY: "auto", textAlign: "left", fontSize: 14 }}>
        <form method="dialog" style={{ float: "right" }}>
          <button type="submit" aria-label="Close open-source notices">Close</button>
        </form>
        <h2 id="open-source-title">AirWSJT-X {version}: open-source notices</h2>
        <p>
          AirWSJT-X is licensed under GNU GPL v3. Its local ADS-B receiver adapts
          FlightAware dump1090 (GPL-2.0-or-later), building on dump1090-mutability
          by Oliver Jowett and the original dump1090 by Salvatore Sanfilippo (antirez).
          Thank you to these authors and FlightAware LLC.
          The GPL-2.0-or-later portions are used under GPL v3 in this combined work.
          The retained two-clause BSD notice also applies to incorporated original work.
        </p>
        <p>Upstream projects:</p>
        <ul>
          {["https://github.com/flightaware/dump1090", "https://github.com/mutability/dump1090",
            "https://github.com/antirez/dump1090"].map(url => (
            <li key={url}><a href={url} onClick={openLink} style={{ color: "#58a6ff" }}>{url}</a></li>
          ))}
        </ul>
        <p>
          Corresponding source for this build (including local adaptations and build scripts):
          {" "}<a href={sourceUrl} onClick={openLink} style={{ color: "#58a6ff", overflowWrap: "anywhere" }}>{sourceUrl}</a>
        </p>
        <p style={{ overflowWrap: "anywhere" }}>Source revision: {__AIRWSJTX_SOURCE_REVISION__}.
          See README.md and CONTRIBUTING.md in the archive for build instructions.
          Release-specific build metadata and dependency staging are controlled by the included workflows.</p>
        {error && <p role="alert" style={{ color: "#f85149" }}>{error}</p>}
        {[
          ["dump1090 copyrights, BSD terms and adaptation details", dump1090Notices],
          ["GNU GPL v2 (upstream dump1090)", dump1090License],
          ["GNU GPL v3 (AirWSJT-X combined work)", appLicense],
        ].map(([title, text]) => (
          <details key={title}>
            <summary style={{ cursor: "pointer", padding: "8px 0" }}>{title}</summary>
            <pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere", fontSize: 12 }}>{text}</pre>
          </details>
        ))}
        <p>These notices and full license texts are included in the app and can be read offline.</p>
      </dialog>
    </>
  );
}
