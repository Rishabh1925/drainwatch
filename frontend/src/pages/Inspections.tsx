import { useState } from "react";
import { Check, ChevronLeft, Move, Maximize2, RefreshCw, RotateCcw, Search, SlidersHorizontal, ZoomIn } from "lucide-react";
import { Button, EmptyState, ErrorState, PageHeader, ProcessingState, SeverityLabel, StatusIndicator } from "../components/ui";
import { EvidencePlaceholder } from "../components/EvidencePlaceholder";
import {
  type Inspection,
  createIncident,
  fetchInspection,
  fetchInspections,
  overrideSeverity,
  rejectInspection,
  verifyInspection,
} from "../api/operations";
import { ApiError } from "../api/client";
import { useAsync } from "../api/useAsync";
import { fmtDateTime, fmtTime } from "../lib/format";

function FilterBar({ query, setQuery, count }: { query: string; setQuery: (v: string) => void; count: number }) {
  return (
    <div className="filter-bar">
      <label className="table-search"><Search size={15} /><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search inspections..." /></label>
      {["Severity", "Review state", "Source type", "Sector", "Reviewer"].map((f) => <button key={f}>{f}<span>⌄</span></button>)}
      <button className="clear-filter" onClick={() => setQuery("")}>Clear all</button>
      <strong className="result-count">{count} inspection{count === 1 ? "" : "s"}</strong>
    </div>
  );
}

function InspectionTable({ records, onReview }: { records: Inspection[]; onReview: (id: string) => void }) {
  const [query, setQuery] = useState("");
  const shown = records.filter((r) => `${r.id} ${r.source_id} ${r.asset_name} ${r.sector}`.toLowerCase().includes(query.toLowerCase()));
  if (!records.length) return <EmptyState title="No inspections here yet" description="Analyze a frame to create the first detection." />;
  return (
    <section className="table-section">
      <FilterBar query={query} setQuery={setQuery} count={shown.length} />
      <div className="data-table-wrap">
        <table className="data-table inspection-table">
          <thead><tr><th>Severity</th><th>Detection / inspection</th><th>Source</th><th>Asset / location</th><th>Captured</th><th>Blockage</th><th>Confidence</th><th>Verification</th><th><span className="sr-only">Action</span></th></tr></thead>
          <tbody>{shown.map((item) => (
            <tr key={item.id}>
              <td><SeverityLabel severity={item.effective_severity} value={`${item.blockage_percentage?.toFixed(1) ?? "—"}%`} /></td>
              <td><code>{item.id}</code><small>{item.verification}</small></td>
              <td><code>{item.source_id}</code><small>{item.source_name}</small></td>
              <td><strong>{item.asset_name}</strong><small>{item.sector ?? "—"}</small></td>
              <td><time>{fmtDateTime(item.captured_at)}</time></td>
              <td><code className="measurement">{item.blockage_percentage?.toFixed(1) ?? "—"}%</code></td>
              <td><code>{item.confidence_percentage?.toFixed(1) ?? "—"}%</code></td>
              <td><span className="review-state">{item.verification}</span><small>{item.reviewer ?? "Unassigned"}</small></td>
              <td><div className="row-actions"><Button variant="secondary" onClick={() => onReview(item.id)}>Review</Button></div></td>
            </tr>
          ))}</tbody>
        </table>
      </div>
    </section>
  );
}

function EvidenceViewer({ inspection }: { inspection: Inspection }) {
  const [mode, setMode] = useState<"original" | "segmentation" | "overlay">("overlay");
  const src = mode === "original" ? inspection.original_url : inspection.segmentation_url;
  return (
    <section className="evidence-section">
      <div className="section-heading"><div><span>EVIDENCE · {inspection.source_id}</span><h2>Camera frame and model segmentation</h2></div><small>Captured {fmtDateTime(inspection.captured_at)}</small></div>
      <div className="evidence-viewer">
        <div className="viewer-toolbar">
          <div className="view-modes">{(["original", "segmentation", "overlay"] as const).map((m) => <button className={mode === m ? "active" : ""} onClick={() => setMode(m)} key={m}>{m.charAt(0).toUpperCase() + m.slice(1)}</button>)}</div>
          <div className="viewer-tools"><button aria-label="Zoom"><ZoomIn size={15} /></button><button aria-label="Pan"><Move size={15} /></button><button aria-label="Reset"><RotateCcw size={15} /></button><button aria-label="Full screen"><Maximize2 size={15} /></button></div>
        </div>
        <div className={`evidence-image mode-${mode}`}>
          {src ? <img src={src} alt={`${mode} for ${inspection.source_id}`} /> : <EvidencePlaceholder sourceId={inspection.source_id ?? "SOURCE"} label={mode === "original" ? "ORIGINAL FRAME NOT STORED" : "SEGMENTATION NOT STORED"} />}
          <div className="segmentation-legend"><span><i className="legend-structure" />Structural / background</span><span><i className="legend-water" />Flowing water</span><span><i className="legend-waste" />Solid waste</span></div>
          <span className="image-scale">PROCESSING VIEW · 512 × 512</span>
        </div>
      </div>
    </section>
  );
}

function AnalysisPanel({ inspection }: { inspection: Inspection }) {
  const classes = inspection.classes ?? [];
  const waste = classes.find((c) => c.name === "waste")?.pixel_count;
  const water = classes.find((c) => c.name === "water")?.pixel_count;
  const structural = classes.find((c) => c.name === "structural")?.pixel_count;
  const channel = (waste ?? 0) + (water ?? 0);
  return (
    <aside className="analysis-column">
      <section className="blockage-summary">
        <span className="section-kicker">ANALYSIS</span>
        <strong className="large-reading">{inspection.blockage_percentage?.toFixed(1) ?? "—"}<small>%</small></strong>
        <h2>Blocked channel</h2>
        <SeverityLabel severity={inspection.effective_severity} value={`${inspection.blockage_percentage?.toFixed(1) ?? "—"}%`} />
      </section>
      {classes.length > 0 && channel > 0 ? (
        <>
          <section className="calculation">
            <h3>Blockage calculation</h3>
            <dl>
              <div><dt>Functional channel pixels</dt><dd>{channel.toLocaleString("en-IN")}</dd></div>
              <div><dt>Waste pixels</dt><dd>{(waste ?? 0).toLocaleString("en-IN")}</dd></div>
              <div><dt>Flowing water pixels</dt><dd>{(water ?? 0).toLocaleString("en-IN")}</dd></div>
              <div className="calculation-result"><dt>Calculated blockage</dt><dd>{(waste ?? 0).toLocaleString("en-IN")} / {channel.toLocaleString("en-IN")} = <b>{inspection.blockage_percentage?.toFixed(1)}%</b></dd></div>
            </dl>
            <p>Class pixels are model output. Blockage is computed by the service as waste ÷ (waste + water).</p>
          </section>
          <section className="class-breakdown">
            <h3>Class pixel breakdown</h3>
            {[["Structural / background", structural, "structure"], ["Flowing water", water, "water"], ["Solid waste", waste, "waste"]].map(([label, value, cls]) => {
              const total = (structural ?? 0) + (water ?? 0) + (waste ?? 0);
              const width = total ? ((value as number) ?? 0) / total * 100 : 0;
              return <div className="class-row" key={String(label)}><span>{label}</span><code>{width.toFixed(1)}%</code><i><b className={String(cls)} style={{ width: `${width}%` }} /></i></div>;
            })}
          </section>
        </>
      ) : (
        <section className="calculation"><h3>Blockage calculation</h3><p>Per-class pixel counts were not stored for this inspection. The blockage percentage above is the model-computed Choke Index.</p></section>
      )}
    </aside>
  );
}

function InspectionDetail({ id, onBack, onNavigate }: { id: string; onBack: () => void; onNavigate: (page: string) => void }) {
  const { data: inspection, error, loading, reload } = useAsync(() => fetchInspection(id), [id]);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [overrideOpen, setOverrideOpen] = useState(false);
  const [falseOpen, setFalseOpen] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  const [overrideSev, setOverrideSev] = useState("warning");
  const [falseReason, setFalseReason] = useState("");

  if (loading) return <div className="page detail-page"><ProcessingState /></div>;
  if (error || !inspection) return <div className="page detail-page"><button className="back-link" onClick={onBack}><ChevronLeft size={15} /> Back</button><ErrorState title="Inspection could not be loaded" description={error ?? "Not found."} reference={id}><Button onClick={onBack}>Back to queue</Button></ErrorState></div>;

  const verified = inspection.verification === "Verified";
  const rejected = inspection.verification === "False detection";

  async function run(action: () => Promise<unknown>, after?: () => void) {
    setBusy(true); setActionError(null);
    try { await action(); reload(); after?.(); }
    catch (e) { setActionError(e instanceof ApiError ? e.message : "The action could not be completed."); }
    finally { setBusy(false); }
  }

  return (
    <div className="page detail-page">
      <button className="back-link" onClick={onBack}><ChevronLeft size={15} /> Back to review queue</button>
      <PageHeader
        eyebrow={`INSPECTION ${inspection.id}`}
        title={`${inspection.asset_name} · ${inspection.sector ?? "—"}`}
        description={`${inspection.source_id} · ${inspection.source_name ?? ""} · Captured ${fmtDateTime(inspection.captured_at)}`}
        status={<div className="header-status-row"><SeverityLabel severity={inspection.effective_severity} value={`${inspection.blockage_percentage?.toFixed(1) ?? "—"}%`} /><StatusIndicator tone={verified ? "normal" : rejected ? "critical" : "warning"}>{inspection.verification}</StatusIndicator></div>}
        action={<div className="button-row"><Button variant="secondary" disabled={!verified || busy || Boolean(inspection.incident_id)} onClick={() => inspection.incident_id ? onNavigate("incidents") : run(() => createIncident(inspection.id), () => onNavigate("incidents"))}>{inspection.incident_id ? "Open incident" : "Create incident"}</Button><Button variant="primary" disabled={verified || rejected || busy} onClick={() => run(() => verifyInspection(inspection.id))} icon={<Check size={15} />}>{verified ? "Operator-confirmed" : "Verify detection"}</Button></div>}
      />
      <div className="detail-meta-strip">
        <span><small>ASSET</small><strong>{inspection.asset_name}</strong></span><span><small>CAMERA / SOURCE</small><strong><code>{inspection.source_id}</code></strong></span><span><small>MODEL</small><code>{inspection.model_version ?? "—"}</code></span><span><small>CONFIDENCE</small><code>{inspection.confidence_percentage?.toFixed(1) ?? "—"}%</code></span>
      </div>
      <div className="detection-lineage"><span><small>CAMERA FRAME</small><code>{inspection.source_id}</code></span><i>→</i><span><small>AI DETECTION</small><code>{inspection.id}</code></span><i>→</i><span><small>MODEL SEVERITY</small><b>{inspection.severity.toUpperCase()}</b></span><i>→</i><span><small>OPERATOR STATUS</small><b>{verified ? "VERIFIED" : rejected ? "FALSE" : "REQUIRES REVIEW"}</b></span><i>→</i><span><small>INCIDENT</small><code>{inspection.incident_id ?? "NOT CREATED"}</code></span></div>

      {actionError && <p className="warn">{actionError}</p>}

      <div className="inspection-primary"><EvidenceViewer inspection={inspection} /><AnalysisPanel inspection={inspection} /></div>

      <div className="inspection-support-grid">
        <section className="plain-section asset-context">
          <div className="section-heading"><div><span>ASSET CONTEXT</span><h2>{inspection.asset_name}</h2></div><Button variant="quiet" onClick={() => onNavigate("map")}>Open in map</Button></div>
          <dl className="detail-list"><div><dt>Type</dt><dd>{inspection.asset_type ?? "Open drainage channel"}</dd></div><div><dt>Sector</dt><dd>{inspection.sector ?? "—"}</dd></div><div><dt>Coordinates</dt><dd><code>{inspection.latitude != null ? `${inspection.latitude.toFixed(4)}, ${inspection.longitude?.toFixed(4)}` : "—"}</code></dd></div><div><dt>Previous blockage</dt><dd><code>{inspection.previous_blockage_percentage?.toFixed(1) ?? "—"}%</code></dd></div><div><dt>Current blockage</dt><dd><code>{inspection.blockage_percentage?.toFixed(1) ?? "—"}%</code></dd></div><div><dt>Change</dt><dd className={(inspection.change_pp ?? 0) >= 0 ? "critical-text" : ""}><code>{inspection.change_pp == null ? "—" : `${inspection.change_pp >= 0 ? "+" : ""}${inspection.change_pp.toFixed(1)} pp`}</code></dd></div></dl>
        </section>
        <section className="plain-section model-output">
          <div className="section-heading"><div><span>MODEL OUTPUT</span><h2>Technical information</h2></div></div>
          <dl className="detail-list"><div><dt>Model version</dt><dd><code>{inspection.model_version ?? "—"}</code></dd></div><div><dt>Processing resolution</dt><dd><code>512 × 512</code></dd></div><div><dt>Analysed</dt><dd><time>{fmtTime(inspection.analyzed_at)}</time></dd></div><div><dt>Submitted by</dt><dd>{inspection.submitted_by ?? "Camera ingestion"}</dd></div><div><dt>Quality status</dt><dd><StatusIndicator tone={(inspection.confidence ?? 1) < 0.55 ? "warning" : "normal"}>{(inspection.confidence ?? 1) < 0.55 ? "Low confidence" : "Good"}</StatusIndicator></dd></div></dl>
          {inspection.override && <div className="quality-note"><SlidersHorizontal size={15} /><div><strong>Severity overridden to {inspection.override.severity.toUpperCase()}</strong><p>{inspection.override.reason}</p><small>Model computed {inspection.override.model_severity?.toUpperCase()}. By {inspection.override.actor}.</small></div></div>}
          {rejected && inspection.rejection_reason && <div className="quality-note"><strong>Marked as false detection</strong><p>{inspection.rejection_reason}</p></div>}
        </section>
      </div>

      <div className="inspection-support-grid">
        <section className="plain-section verification-panel">
          <span className="section-kicker">OPERATIONAL DECISION</span><h2>Verification</h2>
          <StatusIndicator tone={verified ? "normal" : rejected ? "critical" : "warning"}>{verified ? "Detection verified" : rejected ? "False detection" : "Awaiting human verification"}</StatusIndicator>
          <p>Review the source image and segmentation before confirming this detection as an operational incident.</p>
          <div className="button-row"><Button variant="primary" disabled={verified || rejected || busy} onClick={() => run(() => verifyInspection(inspection.id))}>{verified ? "Evidence verified" : "Verify detection"}</Button><Button variant="secondary" disabled={!verified || busy || Boolean(inspection.incident_id)} onClick={() => inspection.incident_id ? onNavigate("incidents") : run(() => createIncident(inspection.id), () => onNavigate("incidents"))}>{inspection.incident_id ? "Open incident" : "Create incident"}</Button></div>
          <div className="secondary-actions"><button disabled={rejected} onClick={() => setFalseOpen(true)}>Mark as false detection</button><button onClick={() => setOverrideOpen(true)}><SlidersHorizontal size={13} /> Override severity</button></div>
          <div className="model-severity"><span>Model-computed severity</span><SeverityLabel severity={inspection.severity} value={`${inspection.blockage_percentage?.toFixed(1) ?? "—"}%`} /></div>
        </section>
        <section className="plain-section history-panel">
          <div className="section-heading"><div><span>REVIEW HISTORY</span><h2>Inspection activity</h2></div></div>
          <ol className="activity-list">
            <li><time>{fmtTime(inspection.captured_at)}</time><div><strong>System</strong><span>Frame captured</span></div></li>
            <li><time>{fmtTime(inspection.analyzed_at)}</time><div><strong>{inspection.model_version}</strong><span>Segmentation completed · blockage <code>{inspection.blockage_percentage?.toFixed(1)}%</code></span></div></li>
            {inspection.verified_at && <li><time>{fmtTime(inspection.verified_at)}</time><div><strong>{inspection.reviewer}</strong><span>{rejected ? "Marked false detection" : "Detection verified"}</span></div></li>}
          </ol>
        </section>
      </div>

      {overrideOpen && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true"><h2>Override severity</h2><p>Changes to severity are recorded in the inspection audit history.</p><label>Model-computed severity<div><SeverityLabel severity={inspection.severity} value={`${inspection.blockage_percentage?.toFixed(1) ?? "—"}%`} /></div></label><label>Operator-confirmed severity<select value={overrideSev} onChange={(e) => setOverrideSev(e.target.value)}><option value="critical">CRITICAL</option><option value="warning">WARNING</option><option value="normal">NORMAL</option></select></label><label>Reason<textarea value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} placeholder="Explain the operational basis for this override." /></label><div className="dialog-actions"><Button onClick={() => setOverrideOpen(false)}>Cancel</Button><Button variant="primary" disabled={!overrideReason.trim() || busy} onClick={() => run(() => overrideSeverity(inspection.id, overrideSev, overrideReason), () => setOverrideOpen(false))}>Confirm severity override</Button></div></div></div>}
      {falseOpen && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true"><h2>Mark detection as false?</h2><p>Inspection <code>{inspection.id}</code> remains in audit history. No incident will be created from it.</p><label>Reason<textarea value={falseReason} onChange={(e) => setFalseReason(e.target.value)} placeholder="Explain why the model output does not represent a functional-channel blockage." /></label><div className="dialog-actions"><Button onClick={() => setFalseOpen(false)}>Cancel</Button><Button variant="danger" disabled={!falseReason.trim() || busy} onClick={() => run(() => rejectInspection(inspection.id, falseReason), () => setFalseOpen(false))}>Mark as false detection</Button></div></div></div>}
    </div>
  );
}

export function InspectionsPage({ onNavigate, initialInspectionId }: { onNavigate: (page: string) => void; initialInspectionId?: string | null }) {
  const [view, setView] = useState<"queue" | "all">("queue");
  const [selected, setSelected] = useState<string | null>(initialInspectionId ?? null);
  const list = useAsync(fetchInspections, []);

  if (selected) return <InspectionDetail id={selected} onBack={() => { setSelected(null); list.reload(); }} onNavigate={onNavigate} />;

  if (list.loading) return <div className="page"><PageHeader title="Inspections" description="Review AI-generated drainage detections before they become operational incidents." /><ProcessingState /></div>;
  if (list.error || !list.data) return <div className="page"><PageHeader title="Inspections" description="Review AI-generated drainage detections before they become operational incidents." /><ErrorState title="Inspections could not be loaded" description={list.error ?? ""} reference="INSPECTIONS"><Button variant="primary" onClick={list.reload}>Retry</Button></ErrorState></div>;

  const queue = list.data.filter((i) => i.verification === "Awaiting review");
  const records = view === "queue" ? queue : list.data;

  return (
    <div className="page">
      <PageHeader title="Inspections" description="Review AI-generated drainage detections before they become operational incidents." />
      <div className="subnav"><button className={view === "queue" ? "active" : ""} onClick={() => setView("queue")}>Review queue <b>{queue.length.toString().padStart(2, "0")}</b></button><button className={view === "all" ? "active" : ""} onClick={() => setView("all")}>All inspections <b>{list.data.length.toString().padStart(2, "0")}</b></button></div>
      <InspectionTable records={records} onReview={setSelected} />
    </div>
  );
}
