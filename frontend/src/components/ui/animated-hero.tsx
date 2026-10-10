import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { MoveRight } from "lucide-react";
import { Button } from "../ui";

export function AnimatedHero({
  onOpenDashboard,
  onSeeHowItWorks,
}: {
  onOpenDashboard: () => void;
  onSeeHowItWorks: () => void;
}) {
  const [active, setActive] = useState(0);
  const messages = useMemo(() => [
    "camera frames into blockage detections",
    "critical detections into operator alerts",
    "verified incidents into field response",
  ], []);

  useEffect(() => {
    const timer = window.setTimeout(() => setActive((current) => (current + 1) % messages.length), 2600);
    return () => window.clearTimeout(timer);
  }, [active, messages.length]);

  return (
    <div className="cctv-animated-hero">
      <span className="landing-eyebrow">CCTV-BASED MUNICIPAL DRAINAGE MONITORING</span>
      <h1>Turn city cameras into an early-warning system for blocked drains.</h1>
      <p>DRAINWATCH continuously analyzes drainage imagery from existing public CCTV infrastructure, detects blockage, identifies severity, and alerts municipal teams before problems escalate.</p>
      <div className="cctv-motion-line" aria-live="polite">
        <span>DRAINWATCH turns</span>
        <span className="cctv-motion-window">
          {messages.map((message, index) => (
            <motion.strong
              key={message}
              initial={false}
              animate={active === index ? { opacity: 1, y: 0 } : { opacity: 0, y: active > index ? -18 : 18 }}
              transition={{ duration: .34, ease: "easeOut" }}
            >
              {message}
            </motion.strong>
          ))}
        </span>
      </div>
      <div className="landing-hero-actions">
        <Button variant="primary" onClick={onOpenDashboard}>Open Operations Dashboard <MoveRight size={15} /></Button>
        <Button onClick={onSeeHowItWorks}>See How It Works</Button>
      </div>
      <div className="cctv-flow" aria-label="CCTV to municipal response workflow">
        {["CCTV", "AI detection", "Alert", "Municipal response"].map((step, index) => (
          <div key={step}><code>0{index + 1}</code><span>{step}</span>{index < 3 && <MoveRight size={13} />}</div>
        ))}
      </div>
    </div>
  );
}
