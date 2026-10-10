import { useMemo, useState } from "react";
import { ChevronRight, Download, ExternalLink, Search } from "lucide-react";
import { Button, EmptyState, ErrorState, PageHeader, ProcessingState, SeverityLabel, StatusIndicator } from "../components/ui";
import {
  type AuditEvent,
  fetchAsset,
  fetchAssets,
  fetchAudit,
  fetchIncidents,
  fetchInspections,
  fetchWorkOrders,
} from "../api/operations";
import { useAsync } from "../api/useAsync";
import { fmtDateTime, fmtPct } from "../lib/format";

// Every view here reads the append-only audit log or the live record collections. The
// mock's fixed "184 events" and hand-written rows are gone — an empty deployment reads
// zero, and corrections appear as new events because that is how the backend stores them.

function pageForRecord(recordId: string): string {
  if (recordId.startsWith("INSP")) return "inspections";
  if (recordId.startsWith("INC")) return "incidents";
  if (recordId.startsWith("WO")) return "dispatch";
  if (recordId.startsWith("CCTV") || recordId.startsWith("DRONE")) return "system";
  return "map";
}

function downloadCsv(events: AuditEvent[]) {
  const header = ["id", "timestamp", "actor", "type", "record", "action", "previous", "next", "reason", "outcome"];
  const escape = (value: string) => `"${String(value ?? "").replace(/"/g, '""')}"`;
  const body = events.map((e) => header.map((key) => escape(String((e as unknown as Record<string, unknown>)[key] ?? ""))).join(","));
  const csv = [header.join(","), ...body].join("\n");
  const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `drainwatch-audit-${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function HistoryFilters({ query, setQuery, count }: { query: string; setQuery: (value: string) => void; count: number }) {
  return <div className="filter-bar history-filters"><label className="table-search"><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search IDs, assets, actors..." /></label>{["Actor","Record type","Action","Severity"].map((filter) => <button key={filter}>{filter}<span>⌄</span></button>)}<button className="clear-filter" onClick={() => setQuery("")}>Clear all</button><strong className="result-count">{count} event{count === 1 ? "" : "s"}</strong></div>;
}

function EventPanel({ event, onClose, onNavigate }: { event: AuditEvent; onClose: () => void; onNavigate: (page: string) => void }) {
  return <aside className="audit-detail"><div className="audit-detail-head"><div><span>AUDIT EVENT</span><h2>{event.id}</h2></div><button onClick={onClose} aria-label="Close event detail">✕</button></div><div className="append-only-note"><strong>Append-only record</strong><p>Corrections are stored as new events. This record cannot be edited.</p></div><dl><div><dt>Timestamp</dt><dd><time>{fmtDateTime(event.timestamp)}</time></dd></div><div><dt>Actor / process</dt><dd>{event.actor}</dd></div><div><dt>Record</dt><dd><code>{event.record}</code></dd></div><div><dt>Action</dt><dd>{event.action}</dd></div><div><dt>Previous state</dt><dd>{event.previous}</dd></div><div><dt>New state</dt><dd>{event.next}</dd></div><div className="wide"><dt>Reason / note</dt><dd>{event.reason || "—"}</dd></div></dl><section><span>RELATED RECORD</span><button onClick={() => onNavigate(pageForRecord(event.record))}><code>{event.record}</code><small>{event.type}</small><ChevronRight size={14} /></button></section></aside>;
}

function OperationalActivity({ onNavigate }: { onNavigate: (page: string) => void }) {
  const audit = useAsync(() => fetchAudit(), []);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [closed, setClosed] = useState(false);
  const events = audit.data ?? [];
  const shown = useMemo(() => events.filter((event) => Object.values(event).join(" ").toLowerCase().includes(query.toLowerCase())), [events, query]);
  const selected = closed ? null : (shown.find((e) => e.id === selectedId) ?? shown[0] ?? null);

  if (audit.loading) return <ProcessingState />;
  if (audit.error) return <ErrorState title="Audit log could not be loaded" description={audit.error} reference="AUDIT"><Button variant="primary" onClick={audit.reload}>Retry</Button></ErrorState>;
  return <><HistoryFilters query={query} setQuery={setQuery} count={shown.length} />{shown.length ? <div className={`audit-workspace ${selected ? "detail-open" : ""}`}><div className="data-table-wrap"><table className="data-table audit-table"><thead><tr><th>Timestamp</th><th>Actor / process</th><th>Record type</th><th>Record ID</th><th>Action</th><th>Previous state</th><th>New state</th><th>Reason / note</th><th /></tr></thead><tbody>{shown.map((event) => <tr className={selected?.id === event.id ? "selected-table-row" : ""} onClick={() => { setSelectedId(event.id); setClosed(false); }} key={event.id}><td><time>{fmtDateTime(event.timestamp)}</time></td><td><strong>{event.actor}</strong></td><td>{event.type}</td><td><code>{event.record}</code></td><td>{event.action}</td><td>{event.previous}</td><td><span className="state-change">{event.next}</span></td><td>{event.reason}</td><td><Button variant="quiet">View details</Button></td></tr>)}</tbody></table></div>{selected && <EventPanel event={selected} onClose={() => setClosed(true)} onNavigate={onNavigate} />}</div> : <EmptyState title="No history matches the current filters" description="Operational activity is recorded as it happens. Analyze a frame or act on a record to populate the log." action={<Button onClick={() => setQuery("")}>Clear filters</Button>} />}</>;
}

function InspectionHistory({ onNavigate }: { onNavigate: (page: string) => void }) {
  const list = useAsync(() => fetchInspections(), []);
  const [query, setQuery] = useState("");
  if (list.loading) return <ProcessingState />;
  if (list.error) return <ErrorState title="Inspection history could not be loaded" description={list.error} reference="INSP-HISTORY"><Button variant="primary" onClick={list.reload}>Retry</Button></ErrorState>;
  const rows = (list.data ?? []).filter((r) => `${r.id} ${r.source_id} ${r.asset_name} ${r.reviewer}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="table-section history-record-table"><HistoryFilters query={query} setQuery={setQuery} count={rows.length} /><div className="data-table-wrap"><table className="data-table"><thead><tr>{["Inspection ID","Source","Asset","Capture time","Blockage","Severity","Confidence","Review state","Reviewer"].map((head) => <th key={head}>{head}</th>)}<th /></tr></thead><tbody>{rows.map((r) => <tr key={r.id}><td><code>{r.id}</code></td><td><code>{r.source_id ?? "—"}</code></td><td>{r.asset_name}</td><td><time>{fmtDateTime(r.captured_at)}</time></td><td><code>{fmtPct(r.blockage_percentage)}</code></td><td><SeverityLabel severity={r.severity} value="" /></td><td><code>{fmtPct(r.confidence_percentage)}</code></td><td>{r.verification}</td><td>{r.reviewer ?? "—"}</td><td><Button variant="quiet" icon={<ExternalLink size={13} />} onClick={() => onNavigate("inspections")}>Open</Button></td></tr>)}</tbody></table></div></section>;
}

function IncidentHistory({ onNavigate }: { onNavigate: (page: string) => void }) {
  const list = useAsync(() => fetchIncidents(), []);
  const [query, setQuery] = useState("");
  if (list.loading) return <ProcessingState />;
  if (list.error) return <ErrorState title="Incident history could not be loaded" description={list.error} reference="INC-HISTORY"><Button variant="primary" onClick={list.reload}>Retry</Button></ErrorState>;
  const rows = (list.data ?? []).filter((r) => `${r.id} ${r.asset_name} ${r.state}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="table-section history-record-table"><HistoryFilters query={query} setQuery={setQuery} count={rows.length} /><div className="data-table-wrap"><table className="data-table"><thead><tr>{["Incident ID","Asset","Severity","Opened","State","Closed","Resolution"].map((head) => <th key={head}>{head}</th>)}<th /></tr></thead><tbody>{rows.map((r) => <tr key={r.id}><td><code>{r.id}</code></td><td>{r.asset_name}</td><td><SeverityLabel severity={r.severity} value="" /></td><td><time>{fmtDateTime(r.opened_at)}</time></td><td><StatusIndicator tone={r.state === "Closed" ? "normal" : r.state === "Dismissed" ? "neutral" : "info"}>{r.state}</StatusIndicator></td><td>{r.closed_at ? <time>{fmtDateTime(r.closed_at)}</time> : "—"}</td><td>{r.closure_reason ?? "—"}</td><td><Button variant="quiet" icon={<ExternalLink size={13} />} onClick={() => onNavigate("incidents")}>Open</Button></td></tr>)}</tbody></table></div></section>;
}

function DispatchHistory({ onNavigate }: { onNavigate: (page: string) => void }) {
  const list = useAsync(() => fetchWorkOrders(), []);
  const [query, setQuery] = useState("");
  if (list.loading) return <ProcessingState />;
  if (list.error) return <ErrorState title="Dispatch history could not be loaded" description={list.error} reference="WO-HISTORY"><Button variant="primary" onClick={list.reload}>Retry</Button></ErrorState>;
  const rows = (list.data ?? []).filter((r) => `${r.id} ${r.incident_id} ${r.crew_name}`.toLowerCase().includes(query.toLowerCase()));
  return <section className="table-section history-record-table"><HistoryFilters query={query} setQuery={setQuery} count={rows.length} /><div className="data-table-wrap"><table className="data-table"><thead><tr>{["Work order","Incident","Unit","Dispatched","Acknowledged","On site","Completed","Outcome"].map((head) => <th key={head}>{head}</th>)}<th /></tr></thead><tbody>{rows.map((r) => <tr key={r.id}><td><code>{r.id}</code></td><td><code>{r.incident_id ?? "—"}</code></td><td>{r.crew_name ?? "—"}</td><td><time>{fmtDateTime(r.dispatched_at)}</time></td><td>{r.acknowledged_at ? <time>{fmtDateTime(r.acknowledged_at)}</time> : "—"}</td><td>{r.arrived_at ? <time>{fmtDateTime(r.arrived_at)}</time> : "—"}</td><td>{r.completed_at ? <time>{fmtDateTime(r.completed_at)}</time> : "—"}</td><td>{r.completion_note ?? "—"}</td><td><Button variant="quiet" icon={<ExternalLink size={13} />} onClick={() => onNavigate("dispatch")}>Open</Button></td></tr>)}</tbody></table></div></section>;
}

function AssetHistory({ onNavigate }: { onNavigate: (page: string) => void }) {
  const assets = useAsync(() => fetchAssets(), []);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const activeId = selectedId ?? assets.data?.[0]?.id ?? null;
  const detail = useAsync(() => (activeId ? fetchAsset(activeId) : Promise.resolve(null)), [activeId]);

  if (assets.loading) return <ProcessingState />;
  if (assets.error) return <ErrorState title="Assets could not be loaded" description={assets.error} reference="ASSETS"><Button variant="primary" onClick={assets.reload}>Retry</Button></ErrorState>;
  if (!assets.data?.length) return <EmptyState title="No monitored assets yet" description="Assets appear here once sources and drains are configured." />;

  const asset = detail.data;
  const history = asset?.history ?? [];
  const points = history.filter((h) => h.blockage_percentage !== null).slice(-6);
  const xs = (index: number) => points.length <= 1 ? 300 : 55 + (index * (465 / (points.length - 1)));
  const ys = (pct: number) => 150 - (Math.min(pct, 100) / 100) * 112;

  return <div className="asset-history">
    <header>
      <div><span>ASSET HISTORY</span><h2>{asset?.name ?? activeId}</h2><p>{asset?.type ?? "Open drainage channel"} · {asset?.sector ?? "—"} · <code>{asset?.latitude != null ? `${asset.latitude.toFixed(4)}, ${asset.longitude?.toFixed(4)}` : "—"}</code> · Source <code>{asset?.source_id ?? "—"}</code></p>
        <label className="asset-picker">Asset <select value={activeId ?? ""} onChange={(e) => setSelectedId(e.target.value)}>{assets.data.map((a) => <option value={a.id} key={a.id}>{a.id} · {a.name}</option>)}</select></label>
      </div>
      <div><span>CURRENT CONDITION</span><strong>{fmtPct(asset?.blockage_percentage)}</strong><SeverityLabel severity={asset?.severity ?? "normal"} value={fmtPct(asset?.blockage_percentage)} /></div>
    </header>
    {detail.loading ? <ProcessingState /> : <>
    <div className="asset-history-grid">
      <section><div className="section-heading"><div><span>BLOCKAGE OVER TIME</span><h2>{points.length >= 2 ? ((points[points.length - 1].blockage_percentage ?? 0) >= (points[0].blockage_percentage ?? 0) ? "Condition is worsening" : "Condition is improving") : "Awaiting repeat detections"}</h2></div><small>Last {points.length} detection{points.length === 1 ? "" : "s"}</small></div>
        {points.length >= 2 ? <svg className="asset-line-chart" viewBox="0 0 560 180" aria-label="Blockage over time"><line x1="40" y1="150" x2="540" y2="150" /><path d={points.map((p, i) => `${i === 0 ? "M" : "L"}${xs(i)} ${ys(p.blockage_percentage ?? 0)}`).join("")} />{points.map((p, i) => <g key={p.id}><circle cx={xs(i)} cy={ys(p.blockage_percentage ?? 0)} r="4" /><text x={xs(i)} y={ys(p.blockage_percentage ?? 0) - 12}>{fmtPct(p.blockage_percentage)}</text><text className="date" x={xs(i)} y="168">{(p.captured_at ?? "").slice(5, 10)}</text></g>)}</svg> : <EmptyState title="Not enough history to chart" description="A trend appears after the asset has at least two detections." />}
      </section>
      <section><div className="section-heading"><div><span>MONITORING SUMMARY</span><h2>Source and response history</h2></div></div><dl className="asset-summary"><div><dt>Monitoring source</dt><dd><code>{asset?.source_id ?? "—"}</code></dd></div><div><dt>Detections</dt><dd><code>{history.length}</code> recorded</dd></div><div><dt>Current incident</dt><dd><code>{asset?.incident_id ?? "None"}</code></dd></div><div><dt>Condition</dt><dd><StatusIndicator tone={asset?.severity === "critical" ? "critical" : asset?.severity === "warning" ? "warning" : "normal"}>{asset?.condition ?? "Normal"}</StatusIndicator></dd></div><div><dt>Latest detection</dt><dd><time>{fmtDateTime(history[history.length - 1]?.captured_at)}</time></dd></div></dl></section>
    </div>
    <div className="asset-history-grid lower">
      <section><div className="section-heading"><div><span>DETECTION EVIDENCE</span><h2>Chronological readings</h2></div></div><div className="evidence-history">{history.length ? [...history].reverse().map((h) => <button onClick={() => onNavigate("inspections")} key={h.id}><time>{fmtDateTime(h.captured_at)}</time><code>{fmtPct(h.blockage_percentage)}</code><span>{h.source_id ?? "—"}</span><code>{h.id}</code><ChevronRight size={14} /></button>) : <EmptyState title="No detections recorded" description="This asset has no inspections yet." />}</div></section>
      <section><div className="section-heading"><div><span>CURRENT INCIDENT</span><h2>{asset?.incident_id ?? "No open incident"}</h2></div></div>{asset?.incident_id ? <Button variant="primary" onClick={() => onNavigate("incidents")}>Open incident {asset.incident_id}</Button> : <p>This asset has no open incident. Verified detections become incidents on the Inspections screen.</p>}</section>
    </div>
    </>}
  </div>;
}

function ExportDialog({ onClose, view, events }: { onClose: () => void; view: string; events: AuditEvent[] }) {
  return <div className="dialog-backdrop"><div className="dialog export-dialog" role="dialog" aria-modal="true"><h2>Export audit record</h2><p>The export preserves timestamps, record IDs, state transitions, and reasons for every event currently loaded.</p><dl><div><dt>Records</dt><dd><code>{events.length}</code></dd></div><div><dt>View</dt><dd>{view}</dd></div><div><dt>Timezone</dt><dd><code>UTC (ISO-8601)</code></dd></div><div><dt>Format</dt><dd><code>CSV</code></dd></div></dl><div className="dialog-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" icon={<Download size={14} />} disabled={!events.length} onClick={() => { downloadCsv(events); onClose(); }}>Download CSV</Button></div></div></div>;
}

export function HistoryPage({ onNavigate }: { onNavigate: (page: string) => void }) {
  const [view, setView] = useState<"activity" | "inspection" | "incident" | "dispatch" | "asset">("activity");
  const [exportOpen, setExportOpen] = useState(false);
  const audit = useAsync(() => fetchAudit(), []);
  const labels = { activity: "Operational activity", inspection: "Inspection history", incident: "Incident history", dispatch: "Dispatch history", asset: "Asset history" };
  return <div className="page"><PageHeader title="History" description="Review operational activity, inspection history, incident changes, dispatch actions, and asset history." action={<Button variant="primary" icon={<Download size={14} />} onClick={() => setExportOpen(true)}>Export audit record</Button>} /><div className="subnav history-nav">{Object.entries(labels).map(([key,label]) => <button className={view === key ? "active" : ""} onClick={() => setView(key as typeof view)} key={key}>{label}</button>)}<span className="subnav-context">Append-only audit record</span></div>{view === "activity" && <OperationalActivity onNavigate={onNavigate} />}{view === "inspection" && <InspectionHistory onNavigate={onNavigate} />}{view === "incident" && <IncidentHistory onNavigate={onNavigate} />}{view === "dispatch" && <DispatchHistory onNavigate={onNavigate} />}{view === "asset" && <AssetHistory onNavigate={onNavigate} />}{exportOpen && <ExportDialog onClose={() => setExportOpen(false)} view={labels[view]} events={audit.data ?? []} />}</div>;
}
