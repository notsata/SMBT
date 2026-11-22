// Entry point of the single-page snapshot build (the mobile artifact). Same pages and components as the PWA.
import { type ComponentType, useState } from "react";
import { createRoot } from "react-dom/client";
import AlertsPage from "@/app/alerts/page";
import AnalystPage from "@/app/analyst/page";
import AnalyzePage from "@/app/analyze/page";
import ChartsPage from "@/app/charts/page";
import HistoryPage from "@/app/history/page";
import LoginPage from "@/app/login/page";
import Dashboard from "@/app/page";
import ScannerPage from "@/app/scanner/page";
import SettingsPage from "@/app/settings/page";
import ValidatePage from "@/app/validate/page";
import { Shell } from "@/components/Shell";
import { setTransport } from "@/lib/api";
import { usePathname } from "./router";
import { snapshotTransport } from "./transport";

setTransport(snapshotTransport);

const PAGES: Record<string, ComponentType> = {
  "/": Dashboard,
  "/scanner/": ScannerPage,
  "/analyze/": AnalyzePage,
  "/alerts/": AlertsPage,
  "/validate/": ValidatePage,
  "/history/": HistoryPage,
  "/charts/": ChartsPage,
  "/analyst/": AnalystPage,
  "/settings/": SettingsPage,
  "/login/": LoginPage,
};

function Banner() {
  const [hidden, setHidden] = useState(() => {
    try {
      return localStorage.getItem("smbt-snapshot-note") === "hidden";
    } catch {
      return false;
    }
  });
  if (hidden) return null;
  return (
    <div className="mb-3 flex items-start gap-3 rounded-lg border border-accent/30 bg-accent/10 px-3 py-2 text-xs leading-relaxed">
      <p className="flex-1">
        <b>Snapshot.</b> Every number here was computed by the SMBT engine on its bundled real history (S&amp;P 500 and NASDAQ, 1999–2018).
        Live prices, any ticker, custom backtests, alerts and push notifications run in the SMBT PC app. The AI analyst works here and asks to use your Claude account.
      </p>
      <button
        aria-label="Hide this note"
        onClick={() => {
          setHidden(true);
          try {
            localStorage.setItem("smbt-snapshot-note", "hidden");
          } catch {
            /* storage unavailable */
          }
        }}
        className="text-muted hover:text-fg"
      >
        ✕
      </button>
    </div>
  );
}

function App() {
  const path = usePathname();
  const Page = PAGES[path] ?? Dashboard;
  return (
    <Shell>
      <Banner />
      <Page key={path} />
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
