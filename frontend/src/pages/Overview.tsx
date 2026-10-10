import { AlertTriangle, ArrowDown, ArrowRight, ArrowUp, ExternalLink } from "lucide-react";
import { type PageKey } from "../components/AppShell";
import { OperationalMap, type MapRecord } from "../components/OperationalMap";
import { Button, EmptyState, ErrorState, PageHeader, ProcessingState, SeverityLabel, StatusIndicator } from "../components/ui";
import { fetchMapRecords, fetchOverview } from "../api/operations";
import { useAsync } from "../api/useAsync";
import { fmtSla, fmtTime } from "../lib/format";

// Overview is driven entirely by GET /api/overview and GET /api/map/records. Every figure
// is a real count — an empty deployment honestly reads zero rather than the mock's fixed
// "3 critical / 184 of 191", which is exactly the fabricated-metric trap to avoid.

export function OverviewPage({ onNavigate, onAnalyzeFrame }: { onNavigate: (page: PageKey) => void; onAnalyzeFrame: () => void }) {
  const overview = useAsync(fetchOverview, []);
  const map = useAsync(fetchMapRecords, []);

  if (overview.loading) return <div className="page overview-page"><PageHeader title="Overview" description="Current drainage risk and operational response across the active jurisdiction." /><ProcessingState /></div>;
  if (overview.error || !overview.data) return <div className="page overview-page"><PageHeader title="Overview" description="Current drainage risk and operational response across the active jurisdiction." /><ErrorState title="Overview could not be loaded" description={overview.error ?? "No data returned."} reference="OVERVIEW"><Button variant="primary" onClick={overview.reload}>Retry</Button></ErrorState></div>;

  const { counts, priority_queue, active_response, recent_inspections } = overview.data;
  const topCritical = priority_queue.find((incident) => incident.severity === "critical");
  const mapRecords: MapRecord[] = (map.data ?? []).map((r) => ({ ...r, severity: r.severity, position: r.position, blockage: r.blockage ?? undefined, state: r.state, source: r.source ?? undefined, detection: r.detection ?? undefined, workOrder: r.workOrder ?? undefined, sector: r.sector ?? undefined, timestamp: r.timestamp ?? undefined }));
  const risers = recent_inspections.filter((i) => i.change_pp !== null).slice(0, 3);
  const offline = counts.sources - counts.sources_reporting;

  return (
    <div className="page overview-page">
      <PageHeader
        title="Overview"
        description="Current drainage risk and operational response across the active jurisdiction."
        status={<StatusIndicator tone={offline > 0 ? "warning" : "normal"}>{offline > 0 ? `${offline} source${offline === 1 ? "" : "s"} not reporting` : "Monitoring operational"}</StatusIndicator>}
        action={<div className="button-row"><Button onClick={onAnalyzeFrame}>Analyze Frame</Button><Button variant="primary" onClick={() => onNavigate(topCritical ? "incidents" : "inspections")}>{topCritical ? "Review highest-priority incident" : "Review inspections"}</Button></div>}
      />

      {topCritical && (
        <section className="critical-banner">
          <div className="critical-evidence"><SeverityLabel severity="critical" value={`${topCritical.blockage_percentage?.toFixed(1) ?? "—"}%`} /></div>
          <div>
            <span>HIGHEST-PRIORITY INCIDENT</span>
            <h2><code>{topCritical.blockage_percentage?.toFixed(1) ?? "—"}%</code> blockage at {topCritical.asset_name}</h2>
            <p><code>{topCritical.source_id}</code> · <code>{topCritical.id}</code> · {topCritical.sector ?? "—"} · {topCritical.state}</p>
          </div>
          <StatusIndicator tone="critical">{fmtSla(topCritical.sla)}</StatusIndicator>
          <div className="critical-actions"><Button variant="danger" onClick={() => onNavigate("incidents")}>Open incident</Button></div>
        </section>
      )}

      <section className="overview-metrics">
        <div><span>Active critical incidents</span><strong>{counts.critical_incidents}</strong><small className="critical-text">{counts.open_incidents} open total</small></div>
        <div><span>Inspections awaiting review</span><strong>{counts.awaiting_review}</strong><small>Needs operator verification</small></div>
        <div><span>Unassigned dispatches</span><strong>{counts.unassigned_dispatch}</strong><small className={counts.unassigned_dispatch ? "warning-text" : ""}>Ready for a field unit</small></div>
        <div><span>Monitored sources reporting</span><strong>{counts.sources_reporting} <i>/ {counts.sources}</i></strong><small>{counts.sources ? ((counts.sources_reporting / counts.sources) * 100).toFixed(1) : "0"}% reporting</small></div>
      </section>

      <div className="overview-primary-grid">
        <section className="overview-section requires-action">
          <div className="section-heading"><div><span>REQUIRES ACTION</span><h2>Prioritized operational queue</h2></div><small>{priority_queue.length} open incident{priority_queue.length === 1 ? "" : "s"}</small></div>
          {priority_queue.length ? (
            <div className="action-list">{priority_queue.map((item) => (
              <button onClick={() => onNavigate("incidents")} key={item.id}>
                <SeverityLabel severity={item.severity} value={`${item.blockage_percentage?.toFixed(1) ?? "—"}%`} />
                <span><code>{item.id}</code><strong>{item.asset_name}</strong><small>{item.source_id}</small></span>
                <span><small>Current state</small>{item.state}</span>
                <span><small>Owner</small>{item.owner ?? "Unassigned"}</span>
                <span className={item.sla.state === "Overdue" ? "critical-text" : ""}><small>SLA</small><code>{fmtSla(item.sla)}</code></span>
                <ArrowRight size={15} />
              </button>
            ))}</div>
          ) : <EmptyState title="No open incidents" description="Verified detections that become incidents will appear here." />}
        </section>
        <section className="overview-section active-response">
          <div className="section-heading"><div><span>ACTIVE RESPONSE</span><h2>Field work orders</h2></div><Button variant="quiet" onClick={() => onNavigate("dispatch")}>Open dispatch</Button></div>
          {active_response.length ? (
            <div className="response-list">{active_response.map((order) => (
              <button onClick={() => onNavigate("dispatch")} key={order.id}>
                <code>{order.id}</code>
                <span><strong>{order.crew_name}</strong><small>{order.destination}</small></span>
                <StatusIndicator tone={order.state === "On site" ? "warning" : "info"}>{order.state}</StatusIndicator>
                <span><small>ETA / arrival</small><code>{fmtTime(order.arrived_at ?? order.acknowledged_at ?? order.dispatched_at)}</code></span>
                <span className={order.sla.state === "Overdue" ? "critical-text" : ""}><small>SLA</small>{order.sla.state}</span>
              </button>
            ))}</div>
          ) : <EmptyState title="No active work orders" description="Dispatched incidents appear here while crews respond." />}
        </section>
      </div>

      <div className="overview-secondary-grid">
        <section className="overview-section overview-map">
          <div className="section-heading"><div><span>GEOGRAPHIC RISK</span><h2>Active jurisdiction</h2></div><Button variant="quiet" icon={<ExternalLink size={13} />} onClick={() => onNavigate("map")}>Open full map</Button></div>
          <div className="overview-map-canvas"><OperationalMap records={mapRecords} compact /></div>
        </section>
        <section className="overview-section risk-changes">
          <div className="section-heading"><div><span>RECENT RISK CHANGES</span><h2>Assets with material change</h2></div></div>
          {risers.length ? (
            <div className="risk-list">{risers.map((item) => (
              <div key={item.id}>
                <span><strong>{item.asset_name}</strong><small>{item.sector ?? "—"}</small></span>
                <code>{item.previous_blockage_percentage?.toFixed(1) ?? "—"}% → {item.blockage_percentage?.toFixed(1) ?? "—"}%</code>
                <b className={(item.change_pp ?? 0) >= 0 ? "up" : "down"}>{(item.change_pp ?? 0) >= 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />} {(item.change_pp ?? 0) >= 0 ? "+" : ""}{item.change_pp?.toFixed(1)} pp</b>
                <SeverityLabel severity={item.severity} value="" />
              </div>
            ))}</div>
          ) : <EmptyState title="No change data yet" description="Repeat inspections of the same asset produce a trend." />}
        </section>
      </div>

      {offline > 0 && <section className="system-exception"><AlertTriangle size={16} /><div><span>MONITORING SYSTEM</span><strong>{offline} source exception{offline === 1 ? "" : "s"}</strong><p>{counts.sources_reporting} of {counts.sources} configured sources are reporting normally.</p></div><StatusIndicator tone="warning">{counts.sources_reporting} / {counts.sources} reporting</StatusIndicator><Button variant="quiet" onClick={() => onNavigate("system")}>Open system status</Button></section>}
    </div>
  );
}
