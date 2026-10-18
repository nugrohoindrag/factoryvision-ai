import { lazy, Suspense } from "react";
import { BrowserRouter, NavLink, Route, Routes } from "react-router-dom";
import { RunPanel } from "./components/agents";
import { AppProvider, useApp } from "./lib/store";
import ActionCenter from "./pages/ActionCenter";
import BriefingPage from "./pages/Briefing";
import { DeliveryPage, ForecastPage, MaterialPage, PlanningPage, PurchasingPage } from "./pages/Modules";
import QualityPage from "./pages/Quality";

// The 3D twin (three.js) loads only when the MES page opens.
const MesPage = lazy(() => import("./pages/Mes"));

const NAV = [
  ["/", "Plant Briefing"],
  ["/forecast", "1 Demand Forecast"],
  ["/planning", "2 Production Planning"],
  ["/mes", "3 MES / Shop Floor"],
  ["/material", "4 Material Supply"],
  ["/purchasing", "5 Purchasing"],
  ["/quality", "6 Quality Control"],
  ["/delivery", "7 Delivery"],
] as const;

function TopBar() {
  const { live, setLive, mode, reset, pending, run, setPanelOpen } = useApp();
  return (
    <header className="topbar">
      <div className="brand">
        <span className="logo" aria-hidden>◆</span> Factory Vision AI
      </div>
      <div className="row">
        <div className="toggle" role="group" aria-label="Mode">
          <button className={!live ? "on" : ""} onClick={() => setLive(false)}>Replay</button>
          <button className={live ? "on" : ""} onClick={() => setLive(true)} disabled={!mode?.ai_enabled} title={mode?.ai_enabled ? "Gemini on Vertex AI, limited per session" : "AI is not enabled on this server"}>
            Live with AI
          </button>
        </div>
        {run.status !== "idle" && <button className="ghost" onClick={() => setPanelOpen(true)}>Agent timeline</button>}
        <button className="ghost" onClick={reset} title="New plant day for this tab">Reset</button>
      </div>
      <NavLink to="/actions" className="ac-link">
        Action Center {pending > 0 && <span className="count">{pending}</span>}
      </NavLink>
    </header>
  );
}

export function Shell() {
  return (
    <div className="app">
      <TopBar />
      <nav className="sidenav" aria-label="Modules">
        {NAV.map(([to, label]) => (
          <NavLink key={to} to={to} end={to === "/"}>
            {label}
          </NavLink>
        ))}
        <NavLink to="/actions">Action Center</NavLink>
      </nav>
      <main className="content">
        <Routes>
          <Route path="/" element={<BriefingPage />} />
          <Route path="/forecast" element={<ForecastPage />} />
          <Route path="/planning" element={<PlanningPage />} />
          <Route path="/mes" element={<Suspense fallback={<p className="muted">Loading the digital twin…</p>}><MesPage /></Suspense>} />
          <Route path="/material" element={<MaterialPage />} />
          <Route path="/purchasing" element={<PurchasingPage />} />
          <Route path="/quality" element={<QualityPage />} />
          <Route path="/delivery" element={<DeliveryPage />} />
          <Route path="/actions" element={<ActionCenter />} />
        </Routes>
      </main>
      <RunPanel />
    </div>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AppProvider>
        <Shell />
      </AppProvider>
    </BrowserRouter>
  );
}
