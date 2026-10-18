import { lazy, Suspense, useState } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { RunPanel } from "./components/agents";
import { AppProvider, useApp } from "./lib/store";
import ActionCenter from "./pages/ActionCenter";
import BriefingPage from "./pages/Briefing";
import CapacityPage from "./pages/Capacity";
import { ForecastPage } from "./pages/Forecast";
import MasterDataPage, { MASTER_SECTIONS } from "./pages/MasterData";
import { MaterialPage } from "./pages/Material";
import { DeliveryPage } from "./pages/Modules";
import OrdersPage from "./pages/Orders";
import PlanWizard from "./pages/PlanWizard";
import { PurchasingPage } from "./pages/Purchasing";
import { PlanningPage } from "./pages/Planning";
import ProductionBoardPage from "./pages/ProductionBoard";
import QualityPage from "./pages/Quality";
import FactoryOverviewPage from "./pages/FactoryOverview";
import LiveBoardPage from "./pages/LiveBoard";
import LossDetailPage from "./pages/LossDetail";
import WorkOrdersPage from "./pages/WorkOrders";

// The 3D twin (three.js) loads only when the MES page opens.
const MesPage = lazy(() => import("./pages/Mes"));

const NAV: { group: string; links: [string, string][] }[] = [
  { group: "Overview", links: [["/", "Plant Briefing"], ["/actions", "Action Center"], ["/factory-overview", "Factory Overview"]] },
  { group: "Plan", links: [["/orders", "Customer Orders"], ["/forecast", "Demand Forecast"], ["/planning", "Production Planning"], ["/capacity", "Capacity Plan"]] },
  { group: "Make", links: [["/mes", "MES / Shop Floor"], ["/live-board", "Live Board"], ["/production-board", "Production Board"], ["/work-orders", "Work Orders"], ["/material", "Material Supply"], ["/purchasing", "Purchasing"], ["/quality", "Quality Control"]] },
  { group: "Deliver", links: [["/delivery", "Delivery"]] },
];

/** Master Data is one sidebar group whose links sit under classification headings, folded by default. */
const MASTER = { group: "Master Data", sections: MASTER_SECTIONS.map(({ section, links }) => ({ section, links: links.map(([k, label]) => [`/master/${k}`, label] as [string, string]) })) };

const FOLD_KEY = "fv-nav-folded";

function readFolded(): string[] {
  try {
    const v = JSON.parse(localStorage.getItem(FOLD_KEY) ?? "null");
    return Array.isArray(v) ? v : [MASTER.group];
  } catch {
    return [MASTER.group];
  }
}

function SideNav({ open, onNavigate }: { open: boolean; onNavigate: () => void }) {
  const { pathname } = useLocation();
  const [folded, setFolded] = useState<string[]>(readFolded);
  const toggle = (group: string) => {
    const next = folded.includes(group) ? folded.filter((g) => g !== group) : [...folded, group];
    setFolded(next);
    try {
      localStorage.setItem(FOLD_KEY, JSON.stringify(next));
    } catch {
      /* storage blocked: the fold lasts for this page only */
    }
  };
  const isActive = (to: string) => (to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(`${to}/`));
  // the group holding the current page never folds it away
  const isOpen = (group: string, paths: string[]) => !folded.includes(group) || paths.some(isActive);
  const link = ([to, label]: [string, string]) => (
    <NavLink key={to} to={to} end={to === "/"} onClick={onNavigate}>
      {label}
    </NavLink>
  );
  const groupHead = (group: string, expanded: boolean, id: string) => (
    <button type="button" className="nav-group" aria-expanded={expanded} aria-controls={id} onClick={() => toggle(group)}>
      {group}
      <span className="chev" aria-hidden>▾</span>
    </button>
  );
  const masterPaths = MASTER.sections.flatMap((s) => s.links.map(([to]) => to));
  const masterOpen = isOpen(MASTER.group, masterPaths);
  return (
    <nav className={`sidenav${open ? " open" : ""}`} aria-label="Modules">
      <div className="brand">
        <img src="/brand/factory-vision-logo-white.svg" alt="Factory Vision" className="brand-logo" />
        <span className="brand-ai">AI</span>
      </div>
      {NAV.map(({ group, links }) => {
        const expanded = isOpen(group, links.map(([to]) => to));
        const id = `nav-${group.toLowerCase()}`;
        return (
          <div key={group} className="nav-section">
            {groupHead(group, expanded, id)}
            {expanded && <div id={id} className="nav-links">{links.map(link)}</div>}
          </div>
        );
      })}
      <div className="nav-section">
        {groupHead(MASTER.group, masterOpen, "nav-master")}
        {masterOpen && (
          <div id="nav-master" className="nav-links">
            {MASTER.sections.map(({ section, links }) => (
              <div key={section} className="nav-links">
                <div className="nav-sub">{section}</div>
                {links.map(link)}
              </div>
            ))}
          </div>
        )}
      </div>
    </nav>
  );
}

function TopBar({ onMenu }: { onMenu: () => void }) {
  const { live, setLive, mode, reset, pending, run, setPanelOpen } = useApp();
  return (
    <header className="topbar">
      <button type="button" className="ghost menu-btn" onClick={onMenu} aria-label="Open menu">☰ Menu</button>
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
  const [menuOpen, setMenuOpen] = useState(false);
  return (
    <div className="app">
      <TopBar onMenu={() => setMenuOpen(true)} />
      <SideNav open={menuOpen} onNavigate={() => setMenuOpen(false)} />
      {menuOpen && <div className="nav-scrim" onClick={() => setMenuOpen(false)} aria-hidden />}
      <main className="content">
        <Routes>
          <Route path="/" element={<BriefingPage />} />
          <Route path="/losses/:lossId" element={<LossDetailPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/forecast" element={<ForecastPage />} />
          <Route path="/planning" element={<PlanningPage />} />
          <Route path="/planning/:planId" element={<PlanWizard />} />
          <Route path="/capacity" element={<CapacityPage />} />
          <Route path="/live-board" element={<LiveBoardPage />} />
          <Route path="/production-board" element={<ProductionBoardPage />} />
          <Route path="/work-orders" element={<WorkOrdersPage />} />
          <Route path="/factory-overview" element={<FactoryOverviewPage />} />
          <Route path="/mes" element={<Suspense fallback={<p className="muted">Loading the digital twin…</p>}><MesPage /></Suspense>} />
          <Route path="/material" element={<MaterialPage />} />
          <Route path="/purchasing" element={<PurchasingPage />} />
          <Route path="/quality" element={<QualityPage />} />
          <Route path="/delivery" element={<DeliveryPage />} />
          <Route path="/actions" element={<ActionCenter />} />
          <Route path="/master" element={<Navigate to="/master/customers" replace />} />
          <Route path="/master/:entity" element={<MasterDataPage />} />
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
