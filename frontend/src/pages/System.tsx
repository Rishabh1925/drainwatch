import { useState } from "react";
import { Download, Search } from "lucide-react";
import { Button, ErrorState, PageHeader, ProcessingState, StatusIndicator } from "../components/ui";
import { EvidencePlaceholder } from "../components/EvidencePlaceholder";
import { type Source, type SystemHealth, fetchInspections, fetchSources, fetchSystemHealth } from "../api/operations";
import { useAsync } from "../api/useAsync";
import { fmtDateTime, fmtPct } from "../lib/format";

// Every figure comes from GET /api/system/health, which the backend derives from stored
// records — the model's loaded state is read from the same STATE the analysis route uses,
// so this screen cannot claim the model is up while an upload would 503. Services with no
// data report their real state rather than a comfortable green.

function tone(state: string): "normal" | "warning" | "critical" {
  if (state === "Operational") return "normal";
  if (state === "Degraded") return "warning";
  return "critical";
}

function downloadReport(health: SystemHealth) {
  const lines = [
    "DRAINWATCH operational diagnostic report",
    `Generated: ${new Date().toISOString()}`,
    `System status: ${health.status}`,
    "",
    "Services:",
    ...health.services.map((s) => `  - ${s.name}: ${s.state} (${s.detail}) — ${s.consequence}`),
    "",
    "Counts:",
    ...Object.entries(health.counts).map(([k, v]) => `  - ${k}: ${v}`),
    "",
    `Model: ${health.model.version} · loaded=${health.model.loaded} · device=${health.model.device} · low_confidence_rate=${health.model.low_confidence_rate ?? "n/a"}`,
    "",
    "Infrastructure credentials and internal logs are excluded.",
  ];
  const blob = new Blob([lines.join("\n")], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `drainwatch-diagnostic-${new Date().toISOString().slice(0, 10)}.txt`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function SystemOverview({ health, onReport }: { health: SystemHealth; onReport: () => void }) {
  const degraded = health.services.filter((s) => s.state !== "Operational");
  return <>
    {degraded.length ? <section className="system-state degraded"><div><span>SYSTEM DEGRADED</span><h2>{degraded.map((s) => s.name).join(", ")} {degraded.length === 1 ? "is" : "are"} not operating normally.</h2><p>{degraded.map((s) => s.consequence).join(" ")} Other services remain available.</p></div><dl><div><dt>Affected services</dt><dd>{degraded.length}</dd></div><div><dt>System status</dt><dd>{health.status}</dd></div></dl></section>
      : <section className="system-state"><div><span>SYSTEM OPERATIONAL</span><h2>All monitored services are operating normally.</h2><p>Image analysis, storage, source ingestion, and the analysis queue are available.</p></div></section>}
    <section className="system-table-section"><div className="system-section-head"><div><span>SERVICE HEALTH</span><h2>Operational services</h2></div><Button variant="quiet" icon={<Download size={13} />} onClick={onReport}>Diagnostic report</Button></div><div className="data-table-wrap"><table className="data-table system-table"><thead><tr><th>Service</th><th>Status</th><th>Detail</th><th>Operational consequence</th></tr></thead><tbody>{health.services.map((service) => <tr key={service.name}><td><strong>{service.name}</strong></td><td><StatusIndicator tone={tone(service.state)}>{service.state}</StatusIndicator></td><td><code>{service.detail}</code></td><td className={service.state !== "Operational" ? "warning-text" : ""}>{service.consequence}</td></tr>)}</tbody></table></div></section>
  </>;
}

function SourcesView({ health, onNavigate, canManageSources }: { health: SystemHealth; onNavigate: (page: string) => void; canManageSources: boolean }) {
  const sources = useAsync(() => fetchSources(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  if (sources.loading) return <ProcessingState />;
  if (sources.error) return <ErrorState title="Sources could not be loaded" description={sources.error} reference="SOURCES"><Button variant="primary" onClick={sources.reload}>Retry</Button></ErrorState>;
  const rows = (sources.data ?? []).filter((s) => `${s.id} ${s.type} ${s.coverage}`.toLowerCase().includes(query.toLowerCase()));
  const current: Source | undefined = (sources.data ?? []).find((s) => s.id === selected) ?? sources.data?.[0];
  const latestFrame = (sources.data ?? []).map((s) => s.last_frame).filter(Boolean).sort().reverse()[0] ?? null;
  const active = health.counts.sources - health.counts.delayed_sources;
  return <><section className="source-metrics"><div><span>Reporting sources</span><strong>{active} <small>active</small></strong></div><div><span>Delayed sources</span><strong className={health.counts.delayed_sources ? "warning-text" : ""}>{health.counts.delayed_sources} <small>require attention</small></strong></div><div><span>Frame ingestion</span><StatusIndicator tone={health.counts.delayed_sources ? "warning" : "normal"}>{health.counts.delayed_sources ? "Degraded" : "Operational"}</StatusIndicator></div><div><span>Latest successful frame</span><strong><code>{latestFrame ? fmtDateTime(latestFrame) : "—"}</code></strong></div></section><div className="source-workspace"><section className="system-table-section"><div className="filter-bar"><label className="table-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search camera or source..." /></label><strong className="result-count">{health.counts.sources} monitored source{health.counts.sources === 1 ? "" : "s"}</strong></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Source ID</th><th>Type</th><th>Status</th><th>Last frame</th><th>Last analysis</th><th>Asset / coverage</th><th>Health</th><th /></tr></thead><tbody>{rows.map((source) => <tr className={current?.id === source.id ? "selected-table-row" : ""} onClick={() => setSelected(source.id)} key={source.id}><td><code>{source.id}</code></td><td>{source.type}</td><td><StatusIndicator tone={source.status === "Delayed" ? "warning" : source.status === "Disabled" ? "neutral" : "normal"}>{source.status}</StatusIndicator></td><td><time>{fmtDateTime(source.last_frame)}</time></td><td><time>{fmtDateTime(source.last_analysis)}</time></td><td>{source.coverage || source.sector || "—"}</td><td>{source.health}</td><td><Button variant="quiet" onClick={() => setSelected(source.id)}>View Source</Button></td></tr>)}</tbody></table></div></section>{current && <aside className="source-detail"><div className="system-section-head"><div><span>SOURCE DETAIL</span><h2>{current.id}</h2></div></div><StatusIndicator tone={current.status === "Delayed" ? "warning" : "normal"}>{current.status}</StatusIndicator><div className="source-frame-preview"><EvidencePlaceholder sourceId={current.id} label={current.status === "Delayed" ? "LATEST CAPTURED FRAME UNAVAILABLE" : "LATEST CAPTURED FRAME"} compact /><time>{fmtDateTime(current.last_frame)}</time></div><dl><div><dt>Type</dt><dd>{current.type}</dd></div><div><dt>Asset / coverage</dt><dd>{current.coverage || "—"}</dd></div><div><dt>Sector</dt><dd>{current.sector ?? "—"}</dd></div><div><dt>Last successful frame</dt><dd><time>{fmtDateTime(current.last_frame)}</time></dd></div><div><dt>Last successful analysis</dt><dd><time>{fmtDateTime(current.last_analysis)}</time></dd></div><div><dt>Health</dt><dd>{current.health}</dd></div></dl>{current.status === "Delayed" && <div className="operational-consequence"><strong>Operational consequence</strong><p>New detections from this camera may be delayed until frame delivery recovers.</p></div>}<Button onClick={() => onNavigate("inspections")}>Open related detections</Button>{canManageSources && <Button variant="quiet" onClick={() => onNavigate("administration")}>Manage source in Administration</Button>}</aside>}</div></>;
}

function ModelView({ health, onNavigate }: { health: SystemHealth; onNavigate: (page: string) => void }) {
  const recent = useAsync(() => fetchInspections("?limit=8"), []);
  const stateTone = (verification: string) => verification === "Verified" ? "normal" : verification === "False detection" ? "critical" : verification === "Failed processing" ? "critical" : "info";
  return <><section className="processing-metrics"><div><span>Active model</span><code>{health.model.version}</code></div><div><span>Compute device</span><code>{health.model.device}</code></div><div><span>Model loaded</span><code className={health.model.loaded ? "" : "critical-text"}>{health.model.loaded ? "Yes" : "No"}</code></div><div><span>Inspections</span><code>{health.counts.inspections}</code></div><div><span>Failed analyses</span><code className={health.counts.failed_inspections ? "critical-text" : ""}>{health.counts.failed_inspections}</code></div><div><span>Low-confidence rate</span><code>{health.model.low_confidence_rate === null ? "—" : `${(health.model.low_confidence_rate * 100).toFixed(1)}%`}</code></div></section><section className="system-table-section"><div className="system-section-head"><div><span>RECENT ANALYSIS JOBS</span><h2>Latest inspections</h2></div></div>{recent.loading ? <ProcessingState /> : recent.error ? <ErrorState title="Recent jobs could not be loaded" description={recent.error} reference="JOBS"><Button variant="primary" onClick={recent.reload}>Retry</Button></ErrorState> : <div className="data-table-wrap"><table className="data-table"><thead><tr><th>Inspection</th><th>State</th><th>Confidence</th><th>Blockage</th><th>Captured</th><th /></tr></thead><tbody>{(recent.data ?? []).map((job) => <tr key={job.id} onClick={() => onNavigate("inspections")}><td><code>{job.id}</code></td><td><StatusIndicator tone={stateTone(job.verification)}>{job.verification}</StatusIndicator></td><td><code>{fmtPct(job.confidence_percentage)}</code></td><td><code>{fmtPct(job.blockage_percentage)}</code></td><td><time>{fmtDateTime(job.captured_at)}</time></td><td><Button variant="quiet">Review</Button></td></tr>)}</tbody></table></div>}</section></>;
}

function IntegrationsView({ health }: { health: SystemHealth }) {
  return <section className="system-table-section"><div className="system-section-head"><div><span>INTEGRATIONS</span><h2>Operational connections</h2></div></div><div className="data-table-wrap"><table className="data-table"><thead><tr><th>Integration</th><th>Status</th><th>Detail</th><th>Operational consequence</th></tr></thead><tbody>{health.services.map((item) => <tr key={item.name}><td><strong>{item.name}</strong></td><td><StatusIndicator tone={tone(item.state)}>{item.state}</StatusIndicator></td><td><code>{item.detail}</code></td><td className={item.state !== "Operational" ? "warning-text" : ""}>{item.consequence}</td></tr>)}</tbody></table></div></section>;
}

function ReportDialog({ onClose, health }: { onClose: () => void; health: SystemHealth }) {
  return <div className="dialog-backdrop"><div className="dialog export-dialog"><h2>Download diagnostic report</h2><p>Operational diagnostic information only. Infrastructure credentials and internal logs are excluded.</p><dl><div><dt>System status</dt><dd>{health.status}</dd></div><div><dt>Affected services</dt><dd>{health.services.filter((s) => s.state !== "Operational").length}</dd></div><div><dt>Delayed sources</dt><dd>{health.counts.delayed_sources}</dd></div><div><dt>Failed analyses</dt><dd>{health.counts.failed_inspections}</dd></div><div><dt>Open incidents</dt><dd>{health.counts.open_incidents}</dd></div><div><dt>Format</dt><dd><code>TXT</code></dd></div></dl><div className="dialog-actions"><Button onClick={onClose}>Cancel</Button><Button variant="primary" icon={<Download size={14} />} onClick={() => { downloadReport(health); onClose(); }}>Download diagnostic report</Button></div></div></div>;
}

export function SystemPage({ onNavigate, canManageSources = false }: { onNavigate: (page: string) => void; canManageSources?: boolean }) {
  const [view, setView] = useState<"services" | "sources" | "model" | "integrations">("services");
  const [reportOpen, setReportOpen] = useState(false);
  const health = useAsync(() => fetchSystemHealth(), []);
  const labels = { services: "Service health", sources: "Sources", model: "Model processing", integrations: "Integrations" };

  if (health.loading) return <div className="page"><PageHeader title="System" description="Monitor the health of image ingestion, AI analysis, storage, and operational integrations." /><ProcessingState /></div>;
  if (health.error || !health.data) return <div className="page"><PageHeader title="System" description="Monitor the health of image ingestion, AI analysis, storage, and operational integrations." /><ErrorState title="System health could not be loaded" description={health.error ?? ""} reference="SYSTEM"><Button variant="primary" onClick={health.reload}>Retry</Button></ErrorState></div>;

  const degraded = health.data.services.filter((s) => s.state !== "Operational").length;
  return <div className="page"><PageHeader title="System" description="Monitor the health of image ingestion, AI analysis, storage, and operational integrations." status={<div className="system-title-status"><StatusIndicator tone={degraded ? "warning" : "normal"}>{degraded ? "System degraded" : "System operational"}</StatusIndicator></div>} /><div className="subnav">{Object.entries(labels).map(([key,label]) => <button className={view === key ? "active" : ""} onClick={() => setView(key as typeof view)} key={key}>{label}{key === "services" && degraded > 0 && <b>{degraded}</b>}</button>)}</div>{view === "services" && <SystemOverview health={health.data} onReport={() => setReportOpen(true)} />}{view === "sources" && <SourcesView health={health.data} onNavigate={onNavigate} canManageSources={canManageSources} />}{view === "model" && <ModelView health={health.data} onNavigate={onNavigate} />}{view === "integrations" && <IntegrationsView health={health.data} />}{reportOpen && <ReportDialog onClose={() => setReportOpen(false)} health={health.data} />}</div>;
}
