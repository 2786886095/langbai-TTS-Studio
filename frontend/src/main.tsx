import React from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { LongformWorkspace } from "./LongformWorkspace";
import "./styles.css";

const standaloneLongform = window.location.pathname.startsWith("/longform") || new URLSearchParams(window.location.search).get("longform") === "1";
document.documentElement.classList.toggle("is-longform-standalone", standaloneLongform);
document.body.classList.toggle("is-longform-standalone", standaloneLongform);

createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {standaloneLongform ? <LongformWorkspace standalone /> : <App />}
  </React.StrictMode>,
);
