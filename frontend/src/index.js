import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { logAppMetadataOnce } from "@/lib/appMetadata";

// Log de identidad del bundle (boot único) — Fase 2 Auditoría Drift Producción.
logAppMetadataOnce();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
