import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import { AppShell, type PageKey } from "./components/AppShell";
import { SectionPlaceholder } from "./pages/Placeholder";
import { ProcessingState } from "./components/ui";
import { AnalyzeFrame } from "./components/AnalyzeFrame";
import { getSession, signOut, type AuthUser } from "./api/auth";

const InspectionsPage = lazy(() => import("./pages/Inspections").then((module) => ({ default: module.InspectionsPage })));
const IncidentsPage = lazy(() => import("./pages/Incidents").then((module) => ({ default: module.IncidentsPage })));
const DispatchPage = lazy(() => import("./pages/Dispatch").then((module) => ({ default: module.DispatchPage })));
const MapPage = lazy(() => import("./pages/Map").then((module) => ({ default: module.MapPage })));
const OverviewPage = lazy(() => import("./pages/Overview").then((module) => ({ default: module.OverviewPage })));
const HistoryPage = lazy(() => import("./pages/History").then((module) => ({ default: module.HistoryPage })));
const SystemPage = lazy(() => import("./pages/System").then((module) => ({ default: module.SystemPage })));
const LandingPage = lazy(() => import("./pages/Landing").then((module) => ({ default: module.LandingPage })));
const LoginPage = lazy(() => import("./pages/Login").then((module) => ({ default: module.LoginPage })));
const ProfilePage = lazy(() => import("./pages/Profile").then((module) => ({ default: module.ProfilePage })));
const AdministrationPage = lazy(() => import("./pages/Administration").then((module) => ({ default: module.AdministrationPage })));

type PublicRoute = "landing" | "login" | "admin-login";

const protectedPages = new Set<PageKey>(["overview", "inspections", "incidents", "map", "dispatch", "history", "system", "profile", "administration"]);

function routeFromHash(): PublicRoute | PageKey {
  const value = window.location.hash.replace(/^#\/?/, "");
  if (value === "login" || value === "admin-login") return value;
  if (protectedPages.has(value as PageKey)) return value as PageKey;
  return "landing";
}

export default function App() {
  const [page, setPage] = useState<PageKey>("overview");
  const [route, setRoute] = useState<PublicRoute | PageKey>(routeFromHash);
  const [user, setUser] = useState<AuthUser | null>(null);
  const [sessionState, setSessionState] = useState<"checking" | "ready">("checking");
  const [sessionExpired, setSessionExpired] = useState(false);
  const [analyzeOpen, setAnalyzeOpen] = useState(false);
  // One-shot focus: after an analysis the operator lands on the inspection that was just
  // created. Cleared immediately after the page consumes it so manually returning to the
  // page later does not re-force the stale selection.
  const [focus, setFocus] = useState<{ inspection?: string; incident?: string }>({});

  // Keep a ref so event listeners and hashchange always see the latest user without
  // re-subscribing (which was causing the login/overview loop via stale closures).
  const userRef = useRef(user);
  userRef.current = user;

  useEffect(() => {
    if (!focus.inspection && !focus.incident) return;
    const raf = requestAnimationFrame(() => setFocus({}));
    return () => cancelAnimationFrame(raf);
  }, [focus]);

  // Session check — runs ONCE on mount, never again.
  useEffect(() => {
    let active = true;
    getSession().then((session) => {
      if (!active) return;
      setUser(session);
      const requested = routeFromHash();
      if (!session && protectedPages.has(requested as PageKey)) {
        setSessionExpired(sessionStorage.getItem("drainwatch-authenticated") === "true");
        setRoute("login");
        window.history.replaceState(null, "", "#/login");
      } else if (session && (requested === "login" || requested === "admin-login")) {
        setPage("overview");
        setRoute("overview");
        window.history.replaceState(null, "", "#/overview");
      }
    }).catch(() => {
      const requested = routeFromHash();
      if (protectedPages.has(requested as PageKey)) {
        setRoute("login");
        window.history.replaceState(null, "", "#/login");
      }
    }).finally(() => active && setSessionState("ready"));
    return () => { active = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Hash navigation — uses userRef so the listener never goes stale.
  useEffect(() => {
    const syncRoute = () => {
      const requested = routeFromHash();
      if (!userRef.current && protectedPages.has(requested as PageKey)) {
        setRoute("login");
        window.history.replaceState(null, "", "#/login");
      } else {
        setRoute(requested);
        if (protectedPages.has(requested as PageKey)) setPage(requested as PageKey);
      }
    };
    window.addEventListener("hashchange", syncRoute);
    return () => window.removeEventListener("hashchange", syncRoute);
  }, []);

  useEffect(() => {
    const expireSession = () => {
      setUser(null);
      setSessionExpired(true);
      setAnalyzeOpen(false);
      setRoute("login");
      window.history.replaceState(null, "", "#/login");
    };
    window.addEventListener("drainwatch:session-expired", expireSession);
    return () => window.removeEventListener("drainwatch:session-expired", expireSession);
  }, []);

  const setPublicRoute = (next: PublicRoute) => {
    setRoute(next);
    window.location.hash = `/${next === "landing" ? "" : next}`;
  };

  const navigate = (nextPage: string) => {
    if (!user) {
      setPublicRoute("login");
      return;
    }
    setPage(nextPage as PageKey);
    setRoute(nextPage as PageKey);
    window.location.hash = `/${nextPage}`;
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  const authenticated = (nextUser: AuthUser) => {
    sessionStorage.setItem("drainwatch-authenticated", "true");
    setUser(nextUser);
    setSessionExpired(false);
    setPage("overview");
    setRoute("overview");
    window.location.hash = "/overview";
  };

  const logout = async () => {
    try { await signOut(); } catch { /* Clear local authenticated state even if the server session is already gone. */ }
    setUser(null);
    sessionStorage.removeItem("drainwatch-authenticated");
    setAnalyzeOpen(false);
    setFocus({});
    setPublicRoute("login");
  };

  if (sessionState === "checking") {
    return <div className="session-loading"><ProcessingState /></div>;
  }
  if (route === "landing") {
    return <Suspense fallback={<div className="page"><ProcessingState /></div>}><LandingPage onOpenDashboard={() => user ? navigate("overview") : setPublicRoute("login")} /></Suspense>;
  }
  if (route === "login" || route === "admin-login") {
    return <Suspense fallback={<div className="session-loading"><ProcessingState /></div>}><LoginPage admin={route === "admin-login"} expired={sessionExpired} onAuthenticated={authenticated} onBack={() => setPublicRoute(route === "admin-login" ? "login" : "landing")} onAdmin={() => setPublicRoute("admin-login")} /></Suspense>;
  }
  if (!user) {
    setPublicRoute("login");
    return null;
  }

  const content = (() => {
    if (page === "inspections") return <InspectionsPage onNavigate={navigate} initialInspectionId={focus.inspection} />;
    if (page === "incidents") return <IncidentsPage onNavigate={navigate} initialIncidentId={focus.incident} />;
    if (page === "dispatch") return <DispatchPage onNavigate={navigate} />;
    if (page === "map") return <MapPage onNavigate={navigate} />;
    if (page === "overview") return <OverviewPage onNavigate={navigate} onAnalyzeFrame={() => setAnalyzeOpen(true)} />;
    if (page === "history") return <HistoryPage onNavigate={navigate} />;
    if (page === "system") return <SystemPage onNavigate={navigate} canManageSources={user.role === "ADMIN"} />;
    if (page === "profile") return <ProfilePage user={user} onUpdated={setUser} />;
    if (page === "administration") return <AdministrationPage user={user} onReturn={() => navigate("overview")} />;
    return <SectionPlaceholder page={page} />;
  })();

  return (
    <AppShell page={page} onNavigate={navigate} onHome={() => setPublicRoute("landing")} onAnalyzeFrame={() => setAnalyzeOpen(true)} onSignOut={() => void logout()} user={user}>
      <Suspense fallback={<div className="page"><ProcessingState /></div>}>{content}</Suspense>
      {analyzeOpen && <AnalyzeFrame onClose={() => setAnalyzeOpen(false)} onReview={(record) => { setAnalyzeOpen(false); setFocus({ inspection: record.inspection_id }); navigate("inspections"); }} />}
    </AppShell>
  );
}
