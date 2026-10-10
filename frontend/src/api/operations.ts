// Typed access to the operations API. The field names here mirror the backend
// serializers in drainwatch/ops/serialize.py exactly — a mismatch blanks a screen with
// no error, so the two are kept deliberately in lockstep.

import { get, post } from "./client";
import type { Severity } from "../components/ui";

export type Verification = "Awaiting review" | "Verified" | "False detection" | "Referred";

export type ClassCount = { name: string; pixel_count: number; percentage?: number };

export type Inspection = {
  id: string;
  source_id: string | null;
  source_name: string | null;
  asset_id: string | null;
  asset_name: string | null;
  asset_type: string | null;
  sector: string | null;
  latitude: number | null;
  longitude: number | null;
  captured_at: string | null;
  analyzed_at: string | null;
  blockage: number | null;
  blockage_percentage: number | null;
  severity: Severity;
  confidence: number | null;
  confidence_percentage: number | null;
  verification: Verification;
  reviewer: string | null;
  verified_at: string | null;
  previous_blockage: number | null;
  previous_blockage_percentage: number | null;
  change_pp: number | null;
  model_version: string | null;
  classes: ClassCount[];
  original_url: string | null;
  segmentation_url: string | null;
  incident_id: string | null;
  submitted_by: string | null;
  rejection_reason: string | null;
  override: { severity: string; reason: string; actor: string; at: string; model_severity: string } | null;
  effective_severity: Severity;
};

export type Sla = {
  target_minutes: number;
  elapsed_minutes: number | null;
  remaining_minutes: number | null;
  state: string;
};

export type Incident = {
  id: string;
  inspection_id: string | null;
  asset_id: string | null;
  asset_name: string | null;
  asset_type: string | null;
  source_id: string | null;
  sector: string | null;
  latitude: number | null;
  longitude: number | null;
  blockage: number | null;
  blockage_percentage: number | null;
  severity: Severity;
  state: string;
  owner: string | null;
  work_order_id: string | null;
  work_order_state: string | null;
  work_order_crew: string | null;
  opened_at: string | null;
  closed_at: string | null;
  closure_reason: string | null;
  sla: Sla;
  notes: string[];
  audit?: AuditEvent[];
};

export type WorkOrder = {
  id: string;
  incident_id: string | null;
  incident_severity: Severity | null;
  incident_state: string | null;
  crew_id: string | null;
  crew_name: string | null;
  crew_supervisor: string | null;
  destination: string | null;
  state: string;
  instructions: string | null;
  dispatched_at: string | null;
  acknowledged_at: string | null;
  arrived_at: string | null;
  completed_at: string | null;
  completion_note: string | null;
  final_blockage: number | null;
  final_blockage_percentage: number | null;
  evidence: string[];
  sla: Sla;
  audit?: AuditEvent[];
};

export type Crew = {
  id: string;
  name: string;
  supervisor: string | null;
  state: string;
  sector: string | null;
  eta_minutes: number | null;
  work_order_id: string | null;
  members: number;
  available: boolean;
};

export type Source = {
  id: string;
  type: string;
  status: string;
  coverage: string;
  sector: string | null;
  latitude: number | null;
  longitude: number | null;
  last_frame: string | null;
  last_analysis: string | null;
  health: string;
  enabled: boolean;
};

export type Asset = {
  id: string;
  name: string;
  type: string;
  sector: string | null;
  latitude: number | null;
  longitude: number | null;
  source_id: string | null;
  latest_inspection_id: string | null;
  blockage: number | null;
  blockage_percentage: number | null;
  severity: Severity;
  incident_id: string | null;
  condition: string;
  history?: Inspection[];
};

export type AuditEvent = {
  id: string;
  timestamp: string;
  actor: string;
  type: string;
  record: string;
  action: string;
  previous: string;
  next: string;
  reason: string;
  outcome: string;
};

export type Overview = {
  counts: {
    critical_incidents: number;
    open_incidents: number;
    awaiting_review: number;
    unassigned_dispatch: number;
    active_work_orders: number;
    sources: number;
    sources_reporting: number;
  };
  priority_queue: Incident[];
  active_response: WorkOrder[];
  recent_inspections: Inspection[];
};

export type SystemHealth = {
  status: string;
  services: { name: string; state: string; detail: string; consequence: string }[];
  counts: Record<string, number>;
  model: { loaded: boolean; device: string; version: string; low_confidence_rate: number | null };
};

export type MapRecordData = {
  id: string;
  kind: "asset" | "detection" | "incident" | "crew" | "source";
  name: string;
  severity?: Severity;
  position: [number, number];
  blockage?: string | null;
  state?: string;
  sector?: string | null;
  source?: string | null;
  detection?: string | null;
  workOrder?: string | null;
  destination?: string | null;
  timestamp?: string | null;
};

// --- reads ---
export const fetchOverview = () => get<Overview>("/api/overview");
export const fetchInspections = (params = "") => get<Inspection[]>(`/api/inspections${params}`);
export const fetchInspection = (id: string) => get<Inspection>(`/api/inspections/${encodeURIComponent(id)}`);
export const fetchIncidents = (params = "") => get<Incident[]>(`/api/incidents${params}`);
export const fetchIncident = (id: string) => get<Incident>(`/api/incidents/${encodeURIComponent(id)}`);
export const fetchWorkOrders = (params = "") => get<WorkOrder[]>(`/api/work-orders${params}`);
export const fetchWorkOrder = (id: string) => get<WorkOrder>(`/api/work-orders/${encodeURIComponent(id)}`);
export const fetchCrews = () => get<Crew[]>("/api/crews");
export const fetchSources = () => get<Source[]>("/api/sources");
export const fetchAssets = () => get<Asset[]>("/api/assets");
export const fetchAsset = (id: string) => get<Asset>(`/api/assets/${encodeURIComponent(id)}`);
export const fetchAudit = (params = "") => get<AuditEvent[]>(`/api/audit${params}`);
export const fetchMapRecords = () => get<MapRecordData[]>("/api/map/records");
export const fetchSystemHealth = () => get<SystemHealth>("/api/system/health");

// --- mutations ---
export const verifyInspection = (id: string) => post<Inspection>(`/api/inspections/${encodeURIComponent(id)}/verify`);
export const rejectInspection = (id: string, reason: string) =>
  post<Inspection>(`/api/inspections/${encodeURIComponent(id)}/reject`, { reason });
export const overrideSeverity = (id: string, severity: string, reason: string) =>
  post<Inspection>(`/api/inspections/${encodeURIComponent(id)}/override`, { severity, reason });
export const createIncident = (inspectionId: string) =>
  post<Incident>(`/api/inspections/${encodeURIComponent(inspectionId)}/incident`);
export const setIncidentState = (id: string, state: string, reason = "") =>
  post<Incident>(`/api/incidents/${encodeURIComponent(id)}/state`, { state, reason });
export const closeIncident = (id: string, reason: string) =>
  post<Incident>(`/api/incidents/${encodeURIComponent(id)}/close`, { reason });
export const dispatchIncident = (id: string, crewId: string, instructions = "") =>
  post<WorkOrder>(`/api/incidents/${encodeURIComponent(id)}/dispatch`, { crew_id: crewId, instructions });
export const setWorkOrderState = (id: string, state: string, reason = "") =>
  post<WorkOrder>(`/api/work-orders/${encodeURIComponent(id)}/state`, { state, reason });
export const completeWorkOrder = (
  id: string,
  completionNote: string,
  finalBlockage?: number,
  evidence?: string[],
) =>
  post<WorkOrder>(`/api/work-orders/${encodeURIComponent(id)}/complete`, {
    completion_note: completionNote,
    final_blockage: finalBlockage,
    evidence,
  });
