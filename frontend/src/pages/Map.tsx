import { useMemo, useState } from "react";
import { AlertTriangle, Check, ChevronDown, Crosshair, Layers, RotateCcw, Search, X } from "lucide-react";
import { OperationalMap, type MapRecord } from "../components/OperationalMap";
import { Button, EmptyState, ErrorState, PageHeader, ProcessingState, SeverityLabel, StatusIndicator } from "../components/ui";
import { EvidencePlaceholder } from "../components/EvidencePlaceholder";
import { fetchIncident, fetchMapRecords } from "../api/operations";
import { useAsync } from "../api/useAsync";
import { fmtDateTime, fmtSla } from "../lib/format";

// Driven by GET /api/map/records: every pin is a real stored record with real
// coordinates (records without coordinates are dropped by the backend, never placed at
// 0,0). The detail panel shows only fields the record actually carries — owner and SLA
// for an incident are fetched live rather than invented.

function IncidentExtras({ id }: { id: string }) {
  const { data, loading } = useAsync(() => fetchIncident(id), [id]);
  return (
    <>
      <div><dt>Owner</dt><dd>{loading ? "…" : data?.owner ?? "Unassigned"}</dd></div>
      <div><dt>SLA</dt><dd><code className={data?.sla.state === "Overdue" ? "critical-text" : ""}>{loading ? "…" : fmtSla(data?.sla)}</code></dd></div>
    </>
  );
}

function SelectedPanel({ record, onClose, onNavigate }: { record: MapRecord; onClose: () => void; onNavigate: (page: string) => void }) {
  const isCrew = record.kind === "crew";
  const isSource = record.kind === "source";
  const isIncident = record.kind === "incident";
  return (
    <aside className="map-side-panel" aria-label={`Selected ${record.kind}: ${record.name}`}>
      <div className="map-panel-header"><div><span>{record.kind.toUpperCase()}</span><h2>{isIncident ? record.id : record.name}</h2></div><button onClick={onClose} aria-label="Close details"><X size={17} /></button></div>
      {isCrew ? (
        <>
          <div className="selected-record-status"><StatusIndicator tone="info">{record.state}</StatusIndicator></div>
          <dl className="map-detail-list"><div><dt>Unit</dt><dd>{record.name}</dd></div><div><dt>Current work order</dt><dd><code>{record.workOrder ?? "—"}</code></dd></div><div><dt>Destination</dt><dd><code>{record.destination ?? "—"}</code></dd></div><div><dt>State</dt><dd>{record.state}</dd></div></dl>
          <Button variant="primary" onClick={() => onNavigate("dispatch")}>Open work order</Button>
        </>
      ) : isSource ? (
        <>
          <div className="selected-record-status"><StatusIndicator tone={record.state === "Active" ? "normal" : "warning"}>{record.state}</StatusIndicator></div>
          <dl className="map-detail-list"><div><dt>Source ID</dt><dd><code>{record.id}</code></dd></div><div><dt>Type</dt><dd>{record.name}</dd></div><div><dt>Coverage</dt><dd>{record.sector ?? "—"}</dd></div><div><dt>Last frame</dt><dd><time>{fmtDateTime(record.timestamp)}</time></dd></div><div><dt>Latest detection</dt><dd><code>{record.detection ?? "—"}</code></dd></div></dl>
          <Button variant="primary" onClick={() => onNavigate("system")}>Open source health</Button>
        </>
      ) : (
        <>
          <div className="selected-record-status"><SeverityLabel severity={record.severity ?? "normal"} value={record.blockage ?? ""} />{isIncident && <StatusIndicator tone="info">{record.state}</StatusIndicator>}</div>
          <div className="map-evidence"><EvidencePlaceholder sourceId={record.source ?? "SOURCE"} label="LATEST DETECTION FRAME" compact /><span>{record.detection ?? "LATEST EVIDENCE"}</span></div>
          <dl className="map-detail-list">
            <div><dt>Asset</dt><dd>{record.name}</dd></div>
            <div><dt>{isIncident ? "Incident ID" : "Asset ID"}</dt><dd><code>{record.id}</code></dd></div>
            <div><dt>Sector</dt><dd>{record.sector ?? "—"}</dd></div>
            <div><dt>Current blockage</dt><dd><code>{record.blockage ?? "—"}</code></dd></div>
            <div><dt>Camera / source</dt><dd><code>{record.source ?? "No fixed source"}</code></dd></div>
            <div><dt>Latest detection</dt><dd><code>{record.detection ?? "No active detection"}</code></dd></div>
            <div><dt>{isIncident ? "Opened" : "Detection time"}</dt><dd><time>{fmtDateTime(record.timestamp)}</time></dd></div>
            {isIncident && <IncidentExtras id={record.id} />}
            <div><dt>Work order</dt><dd><code>{record.workOrder ?? "Not assigned"}</code></dd></div>
          </dl>
          <div className="map-panel-actions"><Button variant="primary" onClick={() => onNavigate(isIncident ? "incidents" : "inspections")}>{isIncident ? "Open Incident" : "Review Detection"}</Button><Button onClick={() => onNavigate(isIncident ? "dispatch" : "history")}>{isIncident ? "Dispatch" : "View Asset History"}</Button></div>
        </>
      )}
    </aside>
  );
}

export function MapPage({ onNavigate }: { onNavigate: (page: string) => void }) {
  const map = useAsync(fetchMapRecords, []);
  const [selected, setSelected] = useState<MapRecord | undefined>(undefined);
  const [severity, setSeverity] = useState<"all" | "critical" | "warning">("all");
  const [query, setQuery] = useState("");
  const [layers, setLayers] = useState({ assets: true, sources: true, detections: true, incidents: true, crews: true, channels: true, jurisdiction: true });
  const [layerOpen, setLayerOpen] = useState(false);

  const allRecords: MapRecord[] = useMemo(() => (map.data ?? []).map((r) => ({
    id: r.id,
    kind: r.kind,
    name: r.name,
    severity: r.severity,
    position: r.position,
    blockage: r.blockage ?? undefined,
    state: r.state,
    sector: r.sector ?? undefined,
    source: r.source ?? undefined,
    detection: r.detection ?? undefined,
    workOrder: r.workOrder ?? undefined,
    destination: r.destination ?? undefined,
    timestamp: r.timestamp ?? undefined,
  })), [map.data]);

  const records = useMemo(() => allRecords.filter((record) => {
    if (!layers.assets && record.kind === "asset") return false;
    if (!layers.sources && record.kind === "source") return false;
    if (!layers.detections && record.kind === "detection") return false;
    if (!layers.incidents && record.kind === "incident") return false;
    if (!layers.crews && record.kind === "crew") return false;
    if (severity !== "all" && record.kind !== "crew" && record.kind !== "source" && record.severity !== severity) return false;
    const haystack = `${record.id} ${record.name} ${record.sector ?? ""}`.toLowerCase();
    return haystack.includes(query.toLowerCase());
  }), [allRecords, layers, query, severity]);

  const clearFilters = () => { setSeverity("all"); setQuery(""); setLayers({ assets: true, sources: true, detections: true, incidents: true, crews: true, channels: true, jurisdiction: true }); };

  if (map.loading) return <div className="page map-page"><PageHeader title="Map" description="Investigate drainage assets, active incidents, and field response across the jurisdiction." /><ProcessingState /></div>;
  if (map.error || !map.data) return <div className="page map-page"><PageHeader title="Map" description="Investigate drainage assets, active incidents, and field response across the jurisdiction." /><ErrorState title="Map records could not be loaded" description={map.error ?? ""} reference="MAP"><Button variant="primary" onClick={map.reload}>Retry</Button></ErrorState></div>;

  return (
    <div className="page map-page">
      <PageHeader title="Map" description="Investigate drainage assets, active incidents, and field response across the jurisdiction." />
      <div className="map-command-bar">
        <label><Search size={15} /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search asset, incident, sector..." /></label>
        <div className="map-filter"><span>Severity</span>{(["all","critical","warning"] as const).map((item) => <button className={severity === item ? "active" : ""} onClick={() => setSeverity(item)} key={item}>{item === "all" ? "All assets" : `${item.charAt(0).toUpperCase() + item.slice(1)} only`}</button>)}</div>
        <button className="map-control-button"><span>Asset type</span>All types <ChevronDown size={13} /></button>
        <div className="layer-control"><button className="map-control-button" onClick={() => setLayerOpen(!layerOpen)}><Layers size={14} /> Layers <ChevronDown size={13} /></button>{layerOpen && <div className="layer-menu">{Object.entries(layers).map(([key, value]) => <label key={key}><input type="checkbox" checked={value} onChange={() => setLayers((current) => ({ ...current, [key]: !value }))} /><span><Check size={11} /></span>{key.charAt(0).toUpperCase() + key.slice(1)}</label>)}</div>}</div>
        <button className="reset-map" onClick={clearFilters}><RotateCcw size={14} /> Reset to jurisdiction</button>
        <strong>{records.length} map records</strong>
      </div>
      <div className={`map-workspace ${selected ? "panel-open" : ""}`}>
        {records.length ? <OperationalMap records={records} selected={selected} onSelect={setSelected} /> : <div className="map-empty"><EmptyState title="No assets match the current filters" description="Try removing a severity, asset-type, or sector filter, or analyze a frame to create the first record." action={<Button onClick={clearFilters}>Clear filters</Button>} /></div>}
        <div className="map-key"><span><i className="asset-symbol" />Asset</span><span><i className="source-symbol">C</i>Camera source</span><span><i className="detection-symbol">D</i>Detection</span><span><i className="incident-symbol">!</i>Active incident</span><span><i className="crew-symbol">U</i>Assigned crew</span></div>
        <button className="jurisdiction-control" onClick={clearFilters}><Crosshair size={14} /> Delhi NCR jurisdiction</button>
        {selected && <SelectedPanel record={selected} onClose={() => setSelected(undefined)} onNavigate={onNavigate} />}
      </div>
      <div className="map-accessible-summary"><AlertTriangle size={14} /><span>Current view contains {records.filter((r) => r.kind === "source").length} camera sources, {records.filter((r) => r.severity === "critical").length} critical records, {records.filter((r) => r.severity === "warning").length} warnings and {records.filter((r) => r.kind === "crew").length} active response unit{records.filter((r) => r.kind === "crew").length === 1 ? "" : "s"}.</span></div>
    </div>
  );
}
