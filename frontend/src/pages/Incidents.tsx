import { useState } from "react";
import { Check, ChevronLeft, ChevronRight, Clock3, ExternalLink, MapPin, Search, Send } from "lucide-react";
import { Button, EmptyState, ErrorState, PageHeader, ProcessingState, SeverityLabel, StatusIndicator } from "../components/ui";
import { EvidencePlaceholder } from "../components/EvidencePlaceholder";
import { type Incident, closeIncident, fetchIncident, fetchIncidents, setIncidentState } from "../api/operations";
import { ApiError } from "../api/client";
import { useAsync } from "../api/useAsync";
import { fmtDateTime, fmtSla, fmtTime } from "../lib/format";

const INCIDENT_STATES = ["Ready for dispatch", "Dispatched", "On site", "Mitigated", "Closed"];

function IncidentTable({ records, onOpen }: { records: Incident[]; onOpen: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const shown = records.filter((r) => `${r.id} ${r.source_id} ${r.asset_name} ${r.sector}`.toLowerCase().includes(query.toLowerCase()));
  if (!records.length) return <EmptyState title="No incidents" description="Verify a detection and create an incident to populate this list." />;
  return (
    <section className="table-section">
      <div className="filter-bar">
        <label className="table-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search incidents..." /></label>
        {["Severity", "Response state", "Owner", "Sector", "SLA state"].map((f) => <button key={f}>{f}<span>⌄</span></button>)}
        <button className="clear-filter" onClick={() => setQuery("")}>Clear all</button><strong className="result-count">{shown.length} incident{shown.length === 1 ? "" : "s"}</strong>
      </div>
      <div className="data-table-wrap">
        <table className="data-table incident-table">
          <thead><tr><th>Severity</th><th>Incident</th><th>Source / detection</th><th>Asset / location</th><th>Blockage</th><th>Opened</th><th>Owner</th><th>Incident state</th><th>SLA</th><th><span className="sr-only">Action</span></th></tr></thead>
          <tbody>{shown.map((item) => (
            <tr key={item.id}>
              <td><SeverityLabel severity={item.severity} value={`${item.blockage_percentage?.toFixed(1) ?? "—"}%`} /></td>
              <td><code>{item.id}</code></td>
              <td><code>{item.source_id}</code><small>{item.inspection_id}</small></td>
              <td><strong>{item.asset_name}</strong><small>{item.sector ?? "—"}</small></td>
              <td><code className="measurement">{item.blockage_percentage?.toFixed(1) ?? "—"}%</code></td>
              <td><time>{fmtDateTime(item.opened_at)}</time></td>
              <td>{item.owner ?? "Unassigned"}</td>
              <td><StatusIndicator tone="info">{item.state}</StatusIndicator><small>Work order: {item.work_order_id ?? "Not assigned"}</small></td>
              <td><code className={item.sla.state === "Overdue" ? "critical-text" : ""}>{fmtSla(item.sla)}</code></td>
              <td><div className="row-actions"><Button onClick={() => onOpen(item.id)}>Open</Button></div></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

function IncidentDetail({ id, onBack, onNavigate }: { id: string; onBack: () => void; onNavigate: (page: string) => void }) {
  const { data: incident, error, loading, reload } = useAsync(() => fetchIncident(id), [id]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [dismissOpen, setDismissOpen] = useState(false);
  const [dismissReason, setDismissReason] = useState("");

  if (loading) return <div className="page detail-page"><ProcessingState /></div>;
  if (error || !incident) return <div className="page detail-page"><button className="back-link" onClick={onBack}><ChevronLeft size={15} /> Back</button><ErrorState title="Incident could not be loaded" description={error ?? "Not found."} reference={id}><Button onClick={onBack}>Back</Button></ErrorState></div>;

  const currentIndex = INCIDENT_STATES.indexOf(incident.state);
  const terminal = incident.state === "Closed" || incident.state === "Dismissed";

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true); setActionError(null);
    try { await action(); reload(); after?.(); }
    catch (e) { setActionError(e instanceof ApiError ? e.message : "The action could not be completed."); }
    finally { setBusy(false); }
  }

  return (
    <div className="page detail-page">
      <button className="back-link" onClick={onBack}><ChevronLeft size={15} /> Back to active incidents</button>
      <PageHeader
        eyebrow={`INCIDENT ${incident.id}`}
        title={`${incident.asset_name} · ${incident.sector ?? "—"}`}
        description={`Created ${fmtDateTime(incident.opened_at)} · ${incident.asset_type ?? "Open drainage channel"}`}
        status={<div className="header-status-row"><SeverityLabel severity={incident.severity} value={`${incident.blockage_percentage?.toFixed(1) ?? "—"}%`} /><StatusIndicator tone="info">{incident.state}</StatusIndicator><span className="sla-compact"><Clock3 size={13} /> SLA <code>{fmtSla(incident.sla)}</code></span></div>}
        action={<div className="next-action"><span>NEXT REQUIRED ACTION</span><p>{incident.work_order_id ? "Work order issued. Track field response in dispatch." : terminal ? "This incident is closed." : "Assign a field unit to begin response."}</p>{!terminal && <Button variant="primary" icon={<Send size={15} />} onClick={() => onNavigate("dispatch")}>{incident.work_order_id ? "View dispatch" : "Send to dispatch"}</Button>}</div>}
      />
      <div className="incident-lineage"><span><small>CAMERA / SOURCE</small><code>{incident.source_id}</code></span><i>→</i><span><small>DETECTION</small><code>{incident.inspection_id}</code></span><i>→</i><span><small>VERIFICATION</small><b>OPERATOR-CONFIRMED</b></span><i>→</i><span><small>INCIDENT</small><code>{incident.id}</code></span><i>→</i><span><small>WORK ORDER</small><b>{incident.work_order_id ?? "NOT ASSIGNED"}</b></span></div>
      <section className="state-progression">{INCIDENT_STATES.map((state, index) => <div className={index < currentIndex ? "completed" : index === currentIndex ? "current" : ""} key={state}><i>{index < currentIndex ? <Check size={12} /> : index + 1}</i><span>{state}</span>{index < INCIDENT_STATES.length - 1 && <b />}</div>)}</section>

      {actionError && <p className="warn">{actionError}</p>}

      <div className="incident-layout">
        <div className="incident-main">
          <section className="plain-section incident-evidence">
            <div className="section-heading"><div><span>INCIDENT EVIDENCE</span><h2>Verified detection</h2></div><Button variant="quiet" icon={<ExternalLink size={14} />} onClick={() => onNavigate("inspections")}>Open inspection</Button></div>
            <div className="incident-evidence-content">
              <div className="incident-image"><EvidencePlaceholder sourceId={incident.source_id ?? "SOURCE"} label="VERIFIED EVIDENCE" /><span>VERIFIED EVIDENCE · {incident.inspection_id}</span></div>
              <div className="evidence-reading"><span>BLOCKED CHANNEL</span><strong>{incident.blockage_percentage?.toFixed(1) ?? "—"}<small>%</small></strong><SeverityLabel severity={incident.severity} value={`${incident.blockage_percentage?.toFixed(1) ?? "—"}%`} /><dl><div><dt>Camera source</dt><dd><code>{incident.source_id}</code></dd></div><div><dt>Detection</dt><dd><code>{incident.inspection_id}</code></dd></div></dl></div>
            </div>
          </section>
          <section className="plain-section">
            <div className="section-heading"><div><span>ACTIVITY</span><h2>Incident history</h2></div></div>
            <ol className="activity-list incident-activity">{(incident.audit ?? []).map((event) => <li key={event.id}><time>{fmtTime(event.timestamp)}</time><div><strong>{event.actor}</strong><span>{event.action}</span><small>{event.reason}</small></div></li>)}</ol>
          </section>
        </div>
        <aside className="incident-aside">
          <section className="plain-section">
            <div className="section-heading"><div><span>LOCATION</span><h2>{incident.asset_name}</h2></div></div>
            <div className="mini-map"><span className="map-road r1" /><span className="map-road r2" /><span className="map-drain" /><i><MapPin size={18} /></i></div>
            <dl className="detail-list compact"><div><dt>Type</dt><dd>{incident.asset_type ?? "Open drainage channel"}</dd></div><div><dt>Sector</dt><dd>{incident.sector ?? "—"}</dd></div><div><dt>Coordinates</dt><dd><code>{incident.latitude != null ? `${incident.latitude.toFixed(4)}, ${incident.longitude?.toFixed(4)}` : "—"}</code></dd></div></dl>
            <Button variant="secondary" onClick={() => onNavigate("map")}>Open in map</Button>
          </section>
          <section className="plain-section sla-panel">
            <div className="section-heading"><div><span>RESPONSE SLA</span><h2>{incident.sla.state}</h2></div></div>
            <code className="sla-time">{fmtSla(incident.sla)}</code><span>target <code>{incident.sla.target_minutes} min</code></span><div className="sla-bar"><i /></div>
          </section>
          <section className="plain-section work-order-panel">
            <div className="section-heading"><div><span>WORK ORDER</span><h2>{incident.work_order_id ?? "No work order assigned"}</h2></div></div>
            {incident.work_order_id ? <dl className="detail-list compact"><div><dt>Assigned crew</dt><dd>{incident.work_order_crew ?? "—"}</dd></div><div><dt>State</dt><dd>{incident.work_order_state ?? "—"}</dd></div></dl> : terminal ? <p>No work order was assigned.</p> : <><p>This incident is ready for dispatch.</p><Button variant="primary" onClick={() => onNavigate("dispatch")}>Send to dispatch</Button></>}
          </section>
          {(incident.work_order_state === "Completed" || incident.state === "Mitigated") && !terminal && <section className="plain-section"><div className="section-heading"><div><span>CLOSURE</span><h2>Verify mitigation</h2></div></div>{incident.state === "Mitigated" ? <><p>Mitigation has been confirmed. Closing the incident is the final step and is recorded separately from the field work.</p><Button variant="primary" disabled={busy} onClick={() => run(() => closeIncident(incident.id, "Mitigation verified; drain confirmed clear."))}>Close incident</Button></> : <><p>The crew reported the work complete. Completion is a field claim — confirm the mitigation from the evidence before the incident can be closed.</p><Button variant="primary" disabled={busy} onClick={() => run(() => setIncidentState(incident.id, "Mitigated", "Mitigation confirmed from completion evidence."))}>Confirm mitigation</Button></>}</section>}
          {!terminal && <div className="record-actions"><button className="danger-link" onClick={() => setDismissOpen(true)}>Dismiss as invalid</button></div>}
        </aside>
      </div>

      {dismissOpen && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true"><h2>Dismiss incident?</h2><p>Incident <code>{incident.id}</code> will be marked dismissed and retained in audit history.</p><label>Reason<textarea value={dismissReason} onChange={(e) => setDismissReason(e.target.value)} placeholder="Explain why this incident is invalid." /></label><div className="dialog-actions"><Button onClick={() => setDismissOpen(false)}>Cancel</Button><Button variant="danger" disabled={!dismissReason.trim() || busy} onClick={() => run(() => setIncidentState(incident.id, "Dismissed", dismissReason), () => setDismissOpen(false))}>Dismiss incident</Button></div></div></div>}
    </div>
  );
}

export function IncidentsPage({ onNavigate, initialIncidentId }: { onNavigate: (page: string) => void; initialIncidentId?: string | null }) {
  const [tab, setTab] = useState<"active" | "resolved">("active");
  const [selected, setSelected] = useState<string | null>(initialIncidentId ?? null);
  const list = useAsync(fetchIncidents, []);

  if (selected) return <IncidentDetail id={selected} onBack={() => { setSelected(null); list.reload(); }} onNavigate={onNavigate} />;
  if (list.loading) return <div className="page"><PageHeader title="Incidents" description="Track verified drainage conditions from acknowledgement through resolution." /><ProcessingState /></div>;
  if (list.error || !list.data) return <div className="page"><PageHeader title="Incidents" description="Track verified drainage conditions from acknowledgement through resolution." /><ErrorState title="Incidents could not be loaded" description={list.error ?? ""} reference="INCIDENTS"><Button variant="primary" onClick={list.reload}>Retry</Button></ErrorState></div>;

  const terminal = (s: string) => s === "Closed" || s === "Dismissed";
  const active = list.data.filter((i) => !terminal(i.state));
  const resolved = list.data.filter((i) => terminal(i.state));
  const records = tab === "active" ? active : resolved;

  return (
    <div className="page">
      <PageHeader title="Incidents" description="Track verified drainage conditions from acknowledgement through resolution." action={active[0] && <Button variant="primary" onClick={() => setSelected(active[0].id)}>Review highest-priority incident</Button>} />
      <div className="subnav"><button className={tab === "active" ? "active" : ""} onClick={() => setTab("active")}>Active <b>{active.length.toString().padStart(2, "0")}</b></button><button className={tab === "resolved" ? "active" : ""} onClick={() => setTab("resolved")}>Resolved <b>{resolved.length.toString().padStart(2, "0")}</b></button></div>
      <IncidentTable records={records} onOpen={setSelected} />
    </div>
  );
}
