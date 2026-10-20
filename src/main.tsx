import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import MobileApp from "./MobileApp";
import FloatView from "./FloatView";
import { isMobile } from "./lib/platform";
import { floatViewFromLocation } from "./lib/floatWindow";
import "./index.css";

const floatKind = isMobile() ? null : floatViewFromLocation();
const RootApp = isMobile() ? MobileApp : App;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    {floatKind ? <FloatView kind={floatKind} /> : <RootApp />}
  </React.StrictMode>
);
