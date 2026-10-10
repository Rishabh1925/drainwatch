import { useEffect, useState, type ReactNode } from "react";
import {
  Activity, AlertTriangle, ClipboardCheck, Clock3, FileClock, History,
  CircleHelp, ImagePlus, LayoutGrid, LogOut, Map, Menu, Search, Send, Settings, ShieldCheck, UserRound, X,
} from "lucide-react";
import { StatusIndicator } from "./ui";
import type { AuthUser } from "../api/auth";

export type PageKey = "overview" | "inspections" | "incidents" | "map" | "dispatch" | "history" | "system" | "profile" | "administration";

const items: Array<{ key: PageKey; label: string; icon: typeof LayoutGrid; count?: string }> = [
  { key: "overview", label: "Overview", icon: LayoutGrid },
  { key: "inspections", label: "Inspections", icon: ClipboardCheck, count: "08" },
  { key: "incidents", label: "Incidents", icon: AlertTriangle, count: "03" },
  { key: "map", label: "Map", icon: Map },
  { key: "dispatch", label: "Dispatch", icon: Send, count: "02" },
  { key: "history", label: "History", icon: History },
];

function NavigationItem({
  active,
  label,
  count,
  icon: Icon,
  onClick,
}: {
  active: boolean;
  label: string;
  count?: string;
  icon: typeof LayoutGrid;
  onClick: () => void;
}) {
  return (
    <button className={`nav-item ${active ? "selected" : ""}`} onClick={onClick} aria-current={active ? "page" : undefined}>
      <Icon size={16} strokeWidth={1.8} />
      <span>{label}</span>
      {count && <b>{count}</b>}
    </button>
  );
}

function initials(name: string) {
  return name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
}

function Sidebar({ page, onNavigate, onHome, open, onClose, user }: { page: PageKey; onNavigate: (page: PageKey) => void; onHome: () => void; open: boolean; onClose: () => void; user: AuthUser }) {
  const navigate = (key: PageKey) => { onNavigate(key); onClose(); };
  return (
    <>
      {open && <button className="drawer-backdrop" onClick={onClose} aria-label="Close navigation" />}
      <aside className={`sidebar ${open ? "drawer-open" : ""}`}>
        <div className="sidebar-brand">
          <button className="wordmark" onClick={onHome} aria-label="Return to DRAINWATCH landing page"><img src="/logo.png" alt="" className="wordmark-logo" /><span className="wm-drain">DRAIN</span><span className="wm-watch">WATCH</span></button>
          <button className="drawer-close" onClick={onClose} aria-label="Close navigation"><X size={18} /></button>
          <p>Municipal Drainage Operations<br /><strong>Delhi NCR</strong></p>
        </div>
        <nav className="sidebar-nav" aria-label="Primary navigation">
          <span className="nav-group-label">MONITORING</span>
          {items.map((item) => (
            <NavigationItem
              key={item.key}
              label={item.label}
              icon={item.icon}
              count={item.count}
              active={page === item.key}
              onClick={() => navigate(item.key)}
            />
          ))}
          <span className="nav-group-label system-label">SYSTEM</span>
          <NavigationItem label="System" icon={Settings} count="01" active={page === "system"} onClick={() => navigate("system")} />
          {user.role === "ADMIN" && <NavigationItem label="Administration" icon={ShieldCheck} active={page === "administration"} onClick={() => navigate("administration")} />}
        </nav>
        <div className="sidebar-bottom">
          <span className="sidebar-meta-label">SYSTEM STATUS</span>
          <StatusIndicator tone="warning">1 source delayed</StatusIndicator>
          <div className="operator-identity">
            <span>{initials(user.name)}</span>
            <div><strong>{user.name}</strong><small>{user.title || (user.role === "ADMIN" ? "System Administrator" : "Operations Operator")}</small></div>
          </div>
        </div>
      </aside>
    </>
  );
}

function GlobalSearch() {
  return (
    <label className="global-search">
      <Search size={16} />
      <input placeholder="Search assets, inspections, incidents or work orders..." />
      <kbd>⌘K</kbd>
    </label>
  );
}

export function AppShell({ page, onNavigate, onHome, onAnalyzeFrame, onSignOut, user, children }: { page: PageKey; onNavigate: (page: PageKey) => void; onHome: () => void; onAnalyzeFrame: () => void; onSignOut: () => void; user: AuthUser; children: ReactNode }) {
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const label = page === "profile" ? "Profile" : page === "administration" ? "Administration" : items.find((item) => item.key === page)?.label ?? "System";
  const userTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const tzLabel = new Intl.DateTimeFormat("en-GB", { timeZoneName: "short", timeZone: userTz }).formatToParts(now).find((p) => p.type === "timeZoneName")?.value ?? userTz;
  const datePart = new Intl.DateTimeFormat("en-GB", { day: "2-digit", month: "short", year: "numeric", timeZone: userTz }).format(now);
  const time = new Intl.DateTimeFormat("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: userTz }).format(now);

  return (
    <div className="application">
      <Sidebar page={page} onNavigate={onNavigate} onHome={onHome} open={drawerOpen} onClose={() => setDrawerOpen(false)} user={user} />
      <div className="application-body">
        <header className="application-header">
          <div className="header-context"><button onClick={() => setDrawerOpen(true)} className="menu-button" aria-label="Open navigation"><Menu size={19} /></button><span>{label}</span></div>
          <GlobalSearch />
          <div className="header-tools">
            <button className="header-analyze-action" onClick={onAnalyzeFrame}><ImagePlus size={14} />Analyze Frame</button>
            <div className="header-time"><Clock3 size={15} /><span>{datePart}<strong>{time} {tzLabel}</strong></span></div>
            <button className="critical-control" onClick={() => onNavigate("incidents")} aria-label="3 critical alerts"><AlertTriangle size={16} /><b>3</b></button>
            <button className="operator-menu" onClick={() => setAccountOpen(true)} aria-expanded={accountOpen}><span>{initials(user.name)}</span><strong>{user.name}</strong></button>
          </div>
        </header>
        <main className="main-workspace">{children}</main>
        <footer className="mobile-field-nav">
          <button onClick={() => onNavigate("dispatch")}><Send size={18} /><span>Work orders</span></button>
          <button onClick={() => onNavigate("incidents")}><AlertTriangle size={18} /><span>Incidents</span></button>
          <button onClick={() => onNavigate("map")}><Map size={18} /><span>Map</span></button>
          <button onClick={() => onNavigate("history")}><FileClock size={18} /><span>Activity</span></button>
        </footer>
      </div>
      {accountOpen && <><button className="account-menu-backdrop" onClick={() => setAccountOpen(false)} aria-label="Close account menu" /><section className="account-menu" aria-label="Account menu"><header><span>{initials(user.name)}</span><div><strong>{user.name}</strong><small>{user.email}</small><code>{user.role}</code></div></header><button onClick={() => { onNavigate("profile"); setAccountOpen(false); }}><UserRound size={15} />Profile</button><button onClick={() => { onNavigate("profile"); setAccountOpen(false); }}><Settings size={15} />Account Settings</button><button onClick={() => { onNavigate(user.role === "ADMIN" ? "administration" : "profile"); setAccountOpen(false); }}><ShieldCheck size={15} />Role / Access</button><button onClick={() => { onNavigate("system"); setAccountOpen(false); }}><CircleHelp size={15} />Help</button><button className="sign-out" onClick={() => { setAccountOpen(false); onSignOut(); }}><LogOut size={15} />Sign Out</button></section></>}
    </div>
  );
}
