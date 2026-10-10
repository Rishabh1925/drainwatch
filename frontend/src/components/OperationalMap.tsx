import { useEffect, useState } from "react";
import L from "leaflet";
import { MapContainer, Marker, Polygon, Polyline, TileLayer, useMap, useMapEvents } from "react-leaflet";
import MarkerClusterGroup from "react-leaflet-cluster";
import type { Severity } from "./ui";
import "leaflet/dist/leaflet.css";
import "leaflet.markercluster/dist/MarkerCluster.css";
import "leaflet.markercluster/dist/MarkerCluster.Default.css";

export type MapRecord = {
  id: string;
  kind: "asset" | "detection" | "incident" | "crew" | "source" | "cluster";
  name: string;
  severity?: Severity;
  position: [number, number];
  blockage?: string;
  state?: string;
  sector?: string;
  source?: string;
  detection?: string;
  workOrder?: string;
  destination?: string;
  timestamp?: string;
};

export const mapRecords: MapRecord[] = [
  { id: "INC-24891", kind: "incident", name: "Drain D-1042", severity: "critical", position: [28.6139, 77.209], blockage: "78.4%", state: "Ready for dispatch", sector: "Sector 4", source: "CCTV-17", detection: "INSP-24891", timestamp: "07 Oct 2026 · 09:32 IST" },
  { id: "INC-24861", kind: "incident", name: "Inlet I-0031", severity: "critical", position: [28.629, 77.178], blockage: "72.1%", state: "On site", sector: "Sector 7", source: "CCTV-31", detection: "INSP-24861", workOrder: "WO-24861", timestamp: "07 Oct 2026 · 07:42 IST" },
  { id: "D-1098", kind: "asset", name: "Drain D-1098", severity: "critical", position: [28.6341, 77.2218], blockage: "74.6%", state: "Requires verification", sector: "Sector 6", source: "CCTV-24", detection: "INSP-24903", timestamp: "07 Oct 2026 · 09:40 IST" },
  { id: "D-0882", kind: "asset", name: "Drain D-0882", severity: "warning", position: [28.597, 77.189], blockage: "56.8%", state: "Awaiting review", sector: "Sector 2", source: "DRONE-03", detection: "INSP-24888", timestamp: "07 Oct 2026 · 09:24 IST" },
  { id: "C-0198", kind: "asset", name: "Culvert C-0198", severity: "warning", position: [28.642, 77.225], blockage: "64.3%", state: "Monitoring", sector: "Sector 3" },
  { id: "D-761", kind: "asset", name: "Drain D-761", severity: "normal", position: [28.606, 77.238], blockage: "21.2%", state: "Normal", sector: "Sector 5" },
  { id: "D-522", kind: "asset", name: "Drain D-522", severity: "normal", position: [28.65, 77.193], blockage: "18.8%", state: "Normal", sector: "Sector 6" },
  { id: "CCTV-17", kind: "source", name: "Drain corridor camera", position: [28.6148, 77.2078], state: "Active", sector: "Sector 4", detection: "INSP-24891", timestamp: "07 Oct 2026 · 09:32 IST" },
  { id: "CCTV-24", kind: "source", name: "Municipal road camera", position: [28.635, 77.2205], state: "Active", sector: "Sector 6", detection: "INSP-24903", timestamp: "07 Oct 2026 · 09:40 IST" },
  { id: "UNIT-03", kind: "crew", name: "Drainage Unit 03", position: [28.625, 77.183], state: "On site", sector: "WO-24861", workOrder: "WO-24861" },
];

function iconFor(record: MapRecord, selected: boolean) {
  const severity = record.severity ?? "normal";
  return L.divIcon({
    className: "map-div-icon",
    html: `<div class="geo-marker ${record.kind} ${severity} ${selected ? "selected" : ""}" aria-label="${record.kind} ${record.name}"><span>${record.kind === "cluster" ? "7" : record.kind === "crew" ? "U" : record.kind === "source" ? "C" : record.kind === "detection" ? "D" : record.kind === "incident" ? "!" : ""}</span></div>`,
    iconSize: record.kind === "cluster" ? [34, 34] : [26, 26],
    iconAnchor: record.kind === "cluster" ? [17, 17] : [13, 13],
  });
}

function MapController({ selected }: { selected?: MapRecord }) {
  const map = useMap();
  useEffect(() => {
    if (selected) map.flyTo(selected.position, 14, { duration: .55 });
  }, [map, selected]);
  return null;
}

function LocationSelection({ position, onSelect }: { position?: [number, number]; onSelect: (position: [number, number]) => void }) {
  const map = useMap();
  useMapEvents({ click: (event) => onSelect([event.latlng.lat, event.latlng.lng]) });
  useEffect(() => {
    if (position) map.flyTo(position, Math.max(map.getZoom(), 15), { duration: .5 });
  }, [map, position]);
  return position ? <Marker position={position} icon={L.divIcon({ className: "map-div-icon", html: '<div class="geo-marker selected-location"><span>+</span></div>', iconSize: [30,30], iconAnchor: [15,15] })} /> : null;
}

export function OperationalMap({
  records = mapRecords,
  selected,
  onSelect,
  compact = false,
  selectedLocation,
  onLocationSelect,
}: {
  records?: MapRecord[];
  selected?: MapRecord;
  onSelect?: (record: MapRecord) => void;
  compact?: boolean;
  selectedLocation?: [number, number];
  onLocationSelect?: (position: [number, number]) => void;
}) {
  const [tileState, setTileState] = useState<"loading" | "ready" | "error">("loading");
  const [tileKey, setTileKey] = useState(0);
  const clusterIcon = (cluster: L.MarkerCluster) => {
    const markers = cluster.getAllChildMarkers();
    const highest = markers.some((marker) => String((marker.options.icon as L.DivIcon)?.options.html).includes("critical")) ? "critical" : markers.some((marker) => String((marker.options.icon as L.DivIcon)?.options.html).includes("warning")) ? "warning" : "normal";
    return L.divIcon({ className: "map-div-icon", html: `<div class="geo-cluster ${highest}"><strong>${cluster.getChildCount()}</strong><span>${highest.toUpperCase()}</span></div>`, iconSize: [42,42], iconAnchor: [21,21] });
  };
  return (
    <div className="operational-map-shell">
      <MapContainer center={selectedLocation ?? [28.617, 77.207]} zoom={13} zoomControl={!compact || Boolean(onLocationSelect)} attributionControl className={compact ? "leaflet-map compact" : "leaflet-map"}>
        <TileLayer key={tileKey} attribution="&copy; OpenStreetMap contributors" url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png" eventHandlers={{ loading: () => setTileState("loading"), load: () => setTileState("ready"), tileerror: () => setTileState("error") }} />
        {!onLocationSelect && <><Polygon positions={[[28.57,77.16],[28.665,77.16],[28.665,77.25],[28.57,77.25]]} pathOptions={{ color: "#667085", weight: 1, dashArray: "6 6", fillOpacity: .02 }} />
        <Polyline positions={[[28.575,77.18],[28.596,77.189],[28.614,77.209],[28.642,77.225],[28.658,77.235]]} pathOptions={{ color: "#175cd3", weight: 3, opacity: .65 }} /></>}
        <MarkerClusterGroup chunkedLoading iconCreateFunction={clusterIcon}>
          {records.map((record) => <Marker key={record.id} position={record.position} icon={iconFor(record, selected?.id === record.id)} eventHandlers={{ click: () => onSelect?.(record) }} />)}
        </MarkerClusterGroup>
        <MapController selected={selected} />
        {onLocationSelect && <LocationSelection position={selectedLocation} onSelect={onLocationSelect} />}
      </MapContainer>
      {tileState !== "ready" && <div className={`map-tile-state ${tileState}`}><strong>{tileState === "loading" ? "Loading map data..." : "Map tiles unavailable"}</strong><span>{tileState === "loading" ? "Loading OpenStreetMap and operational records." : "Operational records remain available. Check connectivity or retry the basemap."}</span>{tileState === "error" && <button onClick={() => { setTileState("loading"); setTileKey((key) => key + 1); }}>Retry map</button>}</div>}
    </div>
  );
}
