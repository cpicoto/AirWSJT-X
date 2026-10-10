import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import MobileApp from "./MobileApp";
import { isMobile } from "./lib/platform";
import "./index.css";

const RootApp = isMobile() ? MobileApp : App;

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <RootApp />
  </React.StrictMode>
);
