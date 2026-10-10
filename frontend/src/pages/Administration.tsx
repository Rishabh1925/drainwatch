import { useEffect, useState } from "react";
import { MapPin, Plus, ShieldAlert, UsersRound, Video } from "lucide-react";
import { createSource, getAdminSources, getAdminUsers, setSourceEnabled, setUserEnabled, type AdminSource, type AdminUser, type AuthUser } from "../api/auth";
import { LocationPicker, type Coordinates } from "../components/LocationPicker";
import { Button, PageHeader, StatusIndicator } from "../components/ui";

type PendingAction = { kind: "user" | "source"; id: string; name: string } | null;

export function AdministrationPage({ user, onReturn }: { user: AuthUser; onReturn: () => void }) {
  const [view, setView] = useState<"users" | "roles" | "sources" | "configuration" | "audit">("users");
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [sources, setSources] = useState<AdminSource[]>([]);
  const [error, setError] = useState("");
  const [pending, setPending] = useState<PendingAction>(null);
  const [sourceOpen, setSourceOpen] = useState(false);
  const [coordinates, setCoordinates] = useState<Coordinates | null>(null);
  const [sourceId, setSourceId] = useState("");
  const [sourceType, setSourceType] = useState("CCTV");
  const [coverage, setCoverage] = useState("");

  useEffect(() => {
    if (user.role !== "ADMIN") return;
    Promise.all([getAdminUsers(), getAdminSources()]).then(([nextUsers, nextSources]) => {
      setUsers(nextUsers); setSources(nextSources);
    }).catch((nextError) => setError(nextError instanceof Error ? nextError.message : "Administrative records could not be loaded."));
  }, [user.role]);

  if (user.role !== "ADMIN") return <div className="page access-denied"><ShieldAlert size={24} /><span>ACCESS DENIED</span><h1>You do not have permission to access this area.</h1><p>Administrative controls are limited to authorized DRAINWATCH administrators.</p><Button variant="primary" onClick={onReturn}>Return to Overview</Button></div>;

  const disable = async () => {
    if (!pending) return;
    try {
      if (pending.kind === "user") {
        await setUserEnabled(pending.id, false);
        setUsers((current) => current.map((item) => item.id === pending.id ? { ...item, status: "Disabled" } : item));
      } else {
        await setSourceEnabled(pending.id, false);
        setSources((current) => current.map((item) => item.id === pending.id ? { ...item, status: "Disabled" } : item));
      }
      setPending(null);
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "The account could not be disabled.");
    }
  };

  const enableSource = async (id: string) => {
    try {
      await setSourceEnabled(id, true);
      setSources((current) => current.map((item) => item.id === id ? { ...item, status: "Active" } : item));
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "The source could not be enabled.");
    }
  };

  const registerSource = async () => {
    if (!coordinates) return;
    try {
      const created = await createSource({ id: sourceId, type: sourceType, coverage, latitude: coordinates.latitude, longitude: coordinates.longitude });
      setSources((current) => [...current, created]);
      setSourceOpen(false); setCoordinates(null); setSourceId(""); setCoverage("");
    } catch (nextError) {
      setError(nextError instanceof Error ? nextError.message : "The source could not be registered.");
    }
  };

  const labels = { users: "Users", roles: "Roles & Access", sources: "Sources", configuration: "System Configuration", audit: "Audit" };
  return <div className="page admin-page"><PageHeader eyebrow="ADMINISTRATION" title="Administration" description="Manage authorized users, roles, monitoring sources, and account controls." action={view === "sources" && <Button variant="primary" icon={<Plus size={14} />} onClick={() => setSourceOpen(true)}>Register Source</Button>} />
    <nav className="subnav admin-subnav" aria-label="Administration">{Object.entries(labels).map(([key, label]) => <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key as typeof view)}>{label}</button>)}</nav>
    {error && <div className="admin-api-error" role="alert">{error}</div>}
    {view === "users" && <section className="system-table-section"><div className="system-section-head"><div><span>USER MANAGEMENT</span><h2>Authorized accounts</h2></div><UsersRound size={18} /></div><div className="data-table-wrap"><table className="data-table admin-table"><thead><tr><th>Name</th><th>Email</th><th>Role</th><th>Status</th><th>Last sign-in</th><th /></tr></thead><tbody>{users.map((item) => <tr key={item.id}><td><strong>{item.name}</strong></td><td>{item.email}</td><td><code>{item.role}</code></td><td><StatusIndicator tone={item.status === "Disabled" ? "critical" : "normal"}>{item.status || "Active"}</StatusIndicator></td><td><time>{item.last_sign_in || "Never"}</time></td><td><div className="row-actions"><Button variant="quiet">View</Button><Button variant="quiet">Edit</Button><Button variant="quiet" disabled={item.status === "Disabled"} onClick={() => setPending({ kind: "user", id: item.id, name: item.name })}>Disable</Button></div></td></tr>)}</tbody></table>{!error && users.length === 0 && <p className="admin-empty">No user records were returned.</p>}</div></section>}
    {view === "sources" && <section className="system-table-section"><div className="system-section-head"><div><span>SOURCE MANAGEMENT</span><h2>Monitoring sources</h2></div><Video size={18} /></div><div className="data-table-wrap"><table className="data-table admin-table"><thead><tr><th>Source ID</th><th>Type</th><th>Status</th><th>Coverage</th><th>Last frame</th><th>Last analysis</th><th /></tr></thead><tbody>{sources.map((item) => <tr key={item.id}><td><code>{item.id}</code></td><td>{item.type}</td><td><StatusIndicator tone={item.status === "Disabled" ? "critical" : "normal"}>{item.status}</StatusIndicator></td><td>{item.coverage}</td><td><time>{item.last_frame || "No frame"}</time></td><td><time>{item.last_analysis || "No analysis"}</time></td><td><div className="row-actions"><Button variant="quiet">View</Button>{item.status === "Disabled" ? <Button variant="quiet" onClick={() => void enableSource(item.id)}>Enable</Button> : <Button variant="quiet" onClick={() => setPending({ kind: "source", id: item.id, name: item.id })}>Disable</Button>}</div></td></tr>)}</tbody></table>{!error && sources.length === 0 && <p className="admin-empty">No source records were returned.</p>}</div></section>}
    {(view === "roles" || view === "configuration" || view === "audit") && <section className="admin-feature"><span>{labels[view].toUpperCase()}</span><h2>{labels[view]}</h2><p>This protected administrative surface is ready for the corresponding backend capability. No configuration values or audit records are fabricated in the client.</p></section>}
    {pending && <div className="dialog-backdrop"><section className="dialog" role="dialog" aria-modal="true"><h2>Disable {pending.name}?</h2><p>{pending.kind === "source" ? "Disabling this source will stop it from contributing new monitoring frames." : "This user will lose access to DRAINWATCH operational workflows."}</p><div className="dialog-actions"><Button onClick={() => setPending(null)}>Cancel</Button><Button variant="danger" onClick={() => void disable()}>{pending.kind === "source" ? "Disable Source" : "Disable User"}</Button></div></section></div>}
    {sourceOpen && <div className="dialog-backdrop"><section className="dialog source-location-dialog" role="dialog" aria-modal="true"><div className="source-dialog-heading"><MapPin size={18} /><div><span>MONITORED ASSET LOCATION</span><h2>Register monitoring source</h2></div></div><div className="source-fields"><label>Source ID<input value={sourceId} onChange={(event) => setSourceId(event.target.value)} placeholder="CCTV-00" /></label><label>Type<select value={sourceType} onChange={(event) => setSourceType(event.target.value)}><option>CCTV</option><option>Inspection camera</option><option>Drone</option></select></label><label>Coverage / asset<input value={coverage} onChange={(event) => setCoverage(event.target.value)} placeholder="Drain D-0000 · Sector" /></label></div><LocationPicker value={coordinates} onChange={setCoordinates} /><div className="dialog-actions"><Button onClick={() => setSourceOpen(false)}>Cancel</Button><Button variant="primary" disabled={!coordinates || !sourceId || !coverage} onClick={() => void registerSource()}>Register Source</Button></div></section></div>}
  </div>;
}
