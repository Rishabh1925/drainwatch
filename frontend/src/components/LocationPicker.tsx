import { useState } from "react";
import { Crosshair, MapPin } from "lucide-react";
import { Button } from "./ui";
import { OperationalMap } from "./OperationalMap";

export type Coordinates = { latitude: number; longitude: number };

type LocationState = "idle" | "requesting" | "granted" | "denied" | "unavailable" | "timeout" | "unsupported";

export function LocationPicker({ value, onChange, onConfirm }: { value: Coordinates | null; onChange: (value: Coordinates) => void; onConfirm?: () => void }) {
  const [state, setState] = useState<LocationState>("idle");
  const [manual, setManual] = useState(false);

  const locate = () => {
    if (!navigator.geolocation) {
      setState("unsupported");
      setManual(true);
      return;
    }
    setState("requesting");
    navigator.geolocation.getCurrentPosition(
      (position) => {
        onChange({ latitude: position.coords.latitude, longitude: position.coords.longitude });
        setState("granted");
        setManual(true);
      },
      (error) => {
        setState(error.code === error.PERMISSION_DENIED ? "denied" : error.code === error.TIMEOUT ? "timeout" : "unavailable");
        setManual(true);
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 },
    );
  };

  const messages: Partial<Record<LocationState, string>> = {
    requesting: "Waiting for browser location permission...",
    granted: "Current user location selected. Confirm before using it as the monitored asset location.",
    denied: "Location access was denied.",
    unavailable: "Your current location is unavailable.",
    timeout: "The location request timed out.",
    unsupported: "This browser does not support location access.",
  };

  return (
    <section className="location-picker">
      <div className="location-privacy"><Crosshair size={17} /><div><strong>USER LOCATION</strong><p>Requested only for this workflow. It is not stored until you confirm it as the monitored asset location.</p></div></div>
      <div className="location-actions">
        <Button icon={<Crosshair size={14} />} onClick={locate} disabled={state === "requesting"}>{state === "requesting" ? "Requesting Location" : "Use My Current Location"}</Button>
        <Button variant="quiet" icon={<MapPin size={14} />} onClick={() => setManual(true)}>Select on Map</Button>
      </div>
      {messages[state] && <p className={`location-message ${["denied", "unavailable", "timeout", "unsupported"].includes(state) ? "error" : ""}`} role="status">{messages[state]} {state !== "requesting" && state !== "granted" && <button onClick={() => setManual(true)}>Enter location manually</button>}</p>}
      {(manual || value) && <div className="location-map">
        <OperationalMap records={[]} compact selectedLocation={value ? [value.latitude, value.longitude] : undefined} onLocationSelect={([latitude, longitude]) => onChange({ latitude, longitude })} />
        <p>Pan, zoom, and click the map to select the monitored asset location.</p>
      </div>}
      <div className="coordinate-readout">
        <span><small>LATITUDE</small><code>{value ? value.latitude.toFixed(6) : "Not selected"}</code></span>
        <span><small>LONGITUDE</small><code>{value ? value.longitude.toFixed(6) : "Not selected"}</code></span>
        {onConfirm && <Button variant="primary" disabled={!value} onClick={onConfirm}>Confirm Location</Button>}
      </div>
    </section>
  );
}
