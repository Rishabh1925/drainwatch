import { useState } from "react";
import { Check, Clock3, Send } from "lucide-react";
import { Button, EmptyState, ErrorState, PageHeader, ProcessingState, SeverityLabel, StatusIndicator } from "../components/ui";
import { EvidencePlaceholder } from "../components/EvidencePlaceholder";
import {
  type Crew,
  type Incident,
  type WorkOrder,
  completeWorkOrder,
  dispatchIncident,
  fetchCrews,
  fetchIncidents,
  fetchWorkOrders,
  setWorkOrderState,
} from "../api/operations";
import { ApiError } from "../api/client";
import { useAsync } from "../api/useAsync";
import { fmtDateTime, fmtSla, fmtTime } from "../lib/format";

function QueueList({ incidents, selectedId, onSelect }: { incidents: Incident[]; selectedId: string | null; onSelect: (i: Incident) => void }) {
  return (
    <aside className="dispatch-queue">
      <div className="dispatch-pane-head"><div><span>PRIORITIZED QUEUE</span><h2>Awaiting assignment</h2></div><strong>{incidents.length}</strong></div>
      <div className="queue-sort">ORDERED BY <b>SEVERITY · SLA</b></div>
      <div className="dispatch-queue-list">
        {incidents.map((item) => (
          <button className={selectedId === item.id ? "selected" : ""} onClick={() => onSelect(item)} key={item.id}>
            <div className="queue-line"><SeverityLabel severity={item.severity} value={`${item.blockage_percentage?.toFixed(1) ?? "—"}%`} /><span className="priority-reason">{item.severity === "critical" ? "Critical" : "Warning"}</span></div>
            <div className="queue-title"><code>{item.id}</code><strong>{item.asset_name}</strong></div>
            <div className="queue-meta"><span>{item.sector ?? "—"}</span><span>Opened <time>{fmtTime(item.opened_at)}</time></span><span className="queue-sla"><Clock3 size={12} /> <code>{fmtSla(item.sla)}</code></span></div>
            <div className="queue-source"><code>{item.source_id}</code><span>{item.inspection_id}</span></div>
            <span className="queue-state">READY FOR DISPATCH</span>
          </button>
        ))}
      </div>
    </aside>
  );
}

function DispatchDetail({ incident, crews, onDispatched }: { incident: Incident; crews: Crew[]; onDispatched: () => void }) {
  const available = crews.filter((c) => c.available);
  const [crewId, setCrewId] = useState(available[0]?.id ?? "");
  const [instructions, setInstructions] = useState("Clear accumulated solid waste from the functional channel. Verify unobstructed flow after removal and submit completion evidence.");
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const crew = crews.find((c) => c.id === crewId);

  async function assign() {
    setBusy(true); setError(null);
    try { await dispatchIncident(incident.id, crewId, instructions); onDispatched(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "Dispatch failed."); setConfirm(false); }
    finally { setBusy(false); }
  }

  return (
    <section className="dispatch-detail">
      <div className="dispatch-detail-header">
        <div><span>SELECTED INCIDENT</span><div><code>{incident.id}</code><SeverityLabel severity={incident.severity} value={`${incident.blockage_percentage?.toFixed(1) ?? "—"}%`} /><StatusIndicator tone="info">{incident.state}</StatusIndicator></div><h2>{incident.asset_name} · {incident.sector ?? "—"}</h2></div>
      </div>
      {error && <p className="warn">{error}</p>}
      <div className="dispatch-detail-grid">
        <div className="dispatch-main-form">
          <section className="dispatch-section incident-summary">
            <div className="section-heading"><div><span>INCIDENT SUMMARY</span><h2>Verified condition</h2></div></div>
            <div className="summary-content">
              <div className="summary-image"><EvidencePlaceholder sourceId={incident.source_id ?? "SOURCE"} label="VERIFIED EVIDENCE" compact /><span>VERIFIED EVIDENCE · {incident.inspection_id}</span></div>
              <dl><div><dt>Camera / source</dt><dd><code>{incident.source_id}</code></dd></div><div><dt>Detection</dt><dd><code>{incident.inspection_id}</code></dd></div><div><dt>Blockage</dt><dd><code>{incident.blockage_percentage?.toFixed(1) ?? "—"}%</code></dd></div><div><dt>Opened</dt><dd><time>{fmtDateTime(incident.opened_at)}</time></dd></div><div><dt>SLA</dt><dd><code>{fmtSla(incident.sla)}</code></dd></div></dl>
            </div>
          </section>
          <section className="dispatch-section">
            <div className="section-heading"><div><span>UNIT AVAILABILITY</span><h2>Select response unit</h2></div><small>{available.length} available</small></div>
            <div className="unit-list">{crews.map((c) => <button disabled={!c.available} className={crewId === c.id ? "selected" : ""} onClick={() => setCrewId(c.id)} key={c.id}><i>{crewId === c.id && <Check size={13} />}</i><span><strong>{c.name}</strong><small>{c.supervisor}</small></span><StatusIndicator tone={c.available ? "normal" : "neutral"}>{c.state}</StatusIndicator><span><small>Current area</small>{c.sector ?? "—"}</span><span><small>Est. travel</small><code>{c.eta_minutes ? `${c.eta_minutes} min` : "—"}</code></span></button>)}</div>
          </section>
          <section className="dispatch-section">
            <div className="section-heading"><div><span>FIELD INSTRUCTIONS</span><h2>Dispatch instruction</h2></div></div>
            <textarea className="instruction-field" value={instructions} onChange={(e) => setInstructions(e.target.value)} />
          </section>
        </div>
        <aside className="dispatch-summary">
          <section><span className="section-kicker">SELECTED UNIT</span><h2>{crew?.name ?? "No unit selected"}</h2>{crew && <StatusIndicator tone="normal">{crew.state}</StatusIndicator>}<dl><div><dt>Est. travel</dt><dd><code>{crew?.eta_minutes ? `${crew.eta_minutes} min` : "—"}</code></dd></div><div><dt>Crew supervisor</dt><dd>{crew?.supervisor ?? "—"}</dd></div></dl></section>
          <section><span className="section-kicker">DESTINATION</span><h2>{incident.asset_name}</h2><p>{incident.sector ?? "—"}</p><code>{incident.latitude != null ? `${incident.latitude.toFixed(4)}, ${incident.longitude?.toFixed(4)}` : "—"}</code></section>
          <Button className="dispatch-primary" variant="primary" icon={<Send size={15} />} disabled={!crewId || busy} onClick={() => setConfirm(true)}>Assign and dispatch unit</Button>
        </aside>
      </div>
      {confirm && <div className="dialog-backdrop"><div className="dialog confirm-dispatch" role="dialog" aria-modal="true"><h2>Confirm dispatch</h2><p>Confirm the field assignment before transmitting the work order.</p><dl><div><dt>Incident</dt><dd><code>{incident.id}</code></dd></div><div><dt>Unit</dt><dd>{crew?.name}</dd></div><div><dt>Destination</dt><dd>{incident.asset_name} · {incident.sector ?? "—"}</dd></div></dl><div className="dialog-actions"><Button onClick={() => setConfirm(false)}>Cancel</Button><Button variant="primary" disabled={busy} onClick={assign}>Assign and dispatch unit</Button></div></div></div>}
    </section>
  );
}

function ActiveOrderDetail({ order, onChanged }: { order: WorkOrder; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState("Solid waste removed from channel entrance.");
  const [finalBlock, setFinalBlock] = useState("18.4");

  async function run(action: () => Promise<unknown>) {
    setBusy(true); setError(null);
    try { await action(); onChanged(); }
    catch (e) { setError(e instanceof ApiError ? e.message : "The action could not be completed."); }
    finally { setBusy(false); }
  }

  return (
    <section className="active-order-detail">
      <div className="section-heading"><div><span>ACTIVE WORK ORDER</span><h2>{order.id}</h2></div><StatusIndicator tone="info">{order.state}</StatusIndicator></div>
      {error && <p className="warn">{error}</p>}
      <div className="active-order-grid"><dl>{[["Incident", order.incident_id], ["Unit", order.crew_name], ["Supervisor", order.crew_supervisor], ["Destination", order.destination], ["Dispatched", fmtTime(order.dispatched_at)], ["SLA", fmtSla(order.sla)]].map(([label, value]) => <div key={String(label)}><dt>{label}</dt><dd>{label === "Incident" || label === "Dispatched" || label === "SLA" ? <code>{value}</code> : value}</dd></div>)}</dl>
      <ol className="activity-list">{[["Dispatched", order.dispatched_at], ["Acknowledged", order.acknowledged_at], ["On site", order.arrived_at], ["Completed", order.completed_at]].filter(([, t]) => t).map(([label, t]) => <li key={String(label)}><time>{fmtTime(t as string)}</time><div><strong>{order.crew_name}</strong><span>{label}</span></div></li>)}</ol></div>
      {order.state !== "Completed" && <div className="field-actions">{order.state === "Dispatched" && <Button disabled={busy} onClick={() => run(() => setWorkOrderState(order.id, "Acknowledged"))}>Record acknowledgement</Button>}{(order.state === "Acknowledged" || order.state === "Dispatched") && <Button disabled={busy} onClick={() => run(() => setWorkOrderState(order.id, "On site"))}>Record arrival</Button>}</div>}
      {order.state === "On site" && <section className="completion-panel"><div><span>FIELD COMPLETION</span><h2>Submit completed work</h2><p>Completing this work order does not close the incident. Mitigation evidence must still be verified.</p></div><label>Completion note<textarea value={note} onChange={(e) => setNote(e.target.value)} /></label><label>Final blockage %<input value={finalBlock} onChange={(e) => setFinalBlock(e.target.value)} /></label><Button variant="primary" disabled={busy || !note.trim()} onClick={() => run(() => completeWorkOrder(order.id, note, Number.parseFloat(finalBlock) || undefined, ["completion-1.jpg", "completion-2.jpg"]))}>Submit completion</Button></section>}
      {order.state === "Completed" && <div className="completion-panel"><div><span>COMPLETED</span><h2>Field work submitted</h2><p>{order.completion_note}</p><small>Final blockage {order.final_blockage_percentage?.toFixed(1) ?? "—"}% · {order.evidence.length} evidence item{order.evidence.length === 1 ? "" : "s"}. The incident remains open until mitigation is verified.</small></div></div>}
    </section>
  );
}

function ActiveDispatches() {
  const orders = useAsync(() => fetchWorkOrders("?active_only=true"), []);
  const [selected, setSelected] = useState<string | null>(null);
  if (orders.loading) return <ProcessingState />;
  if (orders.error || !orders.data) return <ErrorState title="Work orders could not be loaded" description={orders.error ?? ""} reference="WORKORDERS"><Button variant="primary" onClick={orders.reload}>Retry</Button></ErrorState>;
  if (!orders.data.length) return <EmptyState title="No active work orders" description="Assign a verified incident to a crew to create one." />;
  const current = orders.data.find((o) => o.id === selected) ?? orders.data[0];
  return (
    <div className="active-dispatch-layout">
      <section className="table-section">
        <div className="filter-bar"><strong className="result-count">{orders.data.length} active work order{orders.data.length === 1 ? "" : "s"}</strong></div>
        <div className="data-table-wrap"><table className="data-table active-table"><thead><tr>{["Priority", "Work order", "Incident", "Unit", "Destination", "State", "Dispatched", "SLA"].map((h) => <th key={h}>{h}</th>)}</tr></thead><tbody>{orders.data.map((row) => <tr className={current.id === row.id ? "selected-table-row" : ""} onClick={() => setSelected(row.id)} key={row.id}><td><SeverityLabel severity={row.incident_severity ?? "normal"} value="" /></td><td><code>{row.id}</code></td><td><code>{row.incident_id}</code></td><td>{row.crew_name}</td><td>{row.destination}</td><td><StatusIndicator tone="info">{row.state}</StatusIndicator></td><td><code>{fmtTime(row.dispatched_at)}</code></td><td><code className={row.sla.state === "Overdue" ? "critical-text" : ""}>{fmtSla(row.sla)}</code></td></tr>)}</tbody></table></div>
      </section>
      <ActiveOrderDetail order={current} onChanged={orders.reload} />
    </div>
  );
}

function CompletedDispatches() {
  const orders = useAsync(() => fetchWorkOrders(), []);
  if (orders.loading) return <ProcessingState />;
  const completed = (orders.data ?? []).filter((o) => o.state === "Completed");
  if (!completed.length) return <EmptyState title="No completed work orders yet" description="Completed field work appears here." />;
  return <section className="table-section"><div className="filter-bar"><strong className="result-count">{completed.length} completed</strong></div><div className="data-table-wrap"><table className="data-table"><thead><tr>{["Work order", "Incident", "Unit", "Completed", "Final blockage", "Evidence"].map((h) => <th key={h}>{h}</th>)}</tr></thead><tbody>{completed.map((o) => <tr key={o.id}><td><code>{o.id}</code></td><td><code>{o.incident_id}</code></td><td>{o.crew_name}</td><td><time>{fmtDateTime(o.completed_at)}</time></td><td><code>{o.final_blockage_percentage?.toFixed(1) ?? "—"}%</code></td><td>{o.evidence.length} item{o.evidence.length === 1 ? "" : "s"}</td></tr>)}</tbody></table></div></section>;
}

export function DispatchPage({ onNavigate: _onNavigate }: { onNavigate: (page: string) => void }) {
  const [tab, setTab] = useState<"unassigned" | "active" | "completed">("unassigned");
  const incidents = useAsync(() => fetchIncidents("?active_only=true"), []);
  const crews = useAsync(fetchCrews, []);
  const [selected, setSelected] = useState<Incident | null>(null);

  const queue = (incidents.data ?? []).filter((i) => i.state === "Ready for dispatch" && !i.work_order_id);
  const active = selected && queue.find((i) => i.id === selected.id) ? selected : queue[0] ?? null;

  return (
    <div className="page">
      <PageHeader title="Dispatch" description="Assign verified drainage incidents to field units and monitor response progress." />
      <div className="subnav"><button className={tab === "unassigned" ? "active" : ""} onClick={() => setTab("unassigned")}>Unassigned <b>{queue.length.toString().padStart(2, "0")}</b></button><button className={tab === "active" ? "active" : ""} onClick={() => setTab("active")}>Active</button><button className={tab === "completed" ? "active" : ""} onClick={() => setTab("completed")}>Completed</button></div>
      {tab === "unassigned" && (incidents.loading || crews.loading ? <ProcessingState /> : incidents.error ? <ErrorState title="Queue could not be loaded" description={incidents.error} reference="DISPATCH"><Button variant="primary" onClick={incidents.reload}>Retry</Button></ErrorState> : !queue.length ? <EmptyState title="No incidents awaiting dispatch" description="Verify a detection and create an incident, then it appears here ready to assign." action={<Button onClick={() => setTab("active")}>View active dispatches</Button>} /> : active && <div className="dispatch-workspace"><QueueList incidents={queue} selectedId={active.id} onSelect={setSelected} /><DispatchDetail incident={active} crews={crews.data ?? []} onDispatched={() => { incidents.reload(); crews.reload(); setSelected(null); setTab("active"); }} /></div>)}
      {tab === "active" && <ActiveDispatches />}
      {tab === "completed" && <CompletedDispatches />}
    </div>
  );
}
