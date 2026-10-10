import { useState, type ReactNode } from "react";
import { ArrowDown, Menu, X } from "lucide-react";
import { mapRecords, OperationalMap } from "../components/OperationalMap";
import { AnimatedHero } from "../components/ui/animated-hero";
import CardDeckCarousel, { type Slide } from "../components/ui/card-deck-carousel";
import { Button, SeverityLabel, StatusIndicator } from "../components/ui";
// Real drainage/flood frames the operator supplied (frontend/Pictures/).
import heroFrame from "../../Pictures/ITO.webp";
import evidenceFrame from "../../Pictures/thequint_2018-01_d507a634-c3b3-4921-93a2-2519fb2ba97f_c725ccaa-b5eb-4234-8c92-991862603976.webp";
import drainPhoto from "../../Pictures/photo.webp";
import drainAvif from "../../Pictures/117063860.avif";
import drownIncident from "../../Pictures/A-22-year-old-woman-and-her-three-year-old-son-dro_1722969209094_1722973164114.webp";
import cloggedDrains from "../../Pictures/Delhi_-Clogged-city-drains-leave-the-Capital-flooded.jpg";
import drainagePlan from "../../Pictures/New-Drainage-Master-plan-targets-30-year-solution-for-Delhis-urban-flooding.jpg";
import barapullaDrain from "../../Pictures/an-inspection-of-the-barapulla-kushak-and-sunehri-drain-.webp";
import newslaundryDrain from "../../Pictures/newslaundry_2025-03-04_b2zcqxcf_unnamed-3.avif";
// Generated product images for Landing page showcase sections.
import segmentationOutput from "../../Pictures/segmentation_output.jpg";
import dashboardScreenshot from "../../Pictures/dashboard_screenshot.jpg";
import inspectionScreen from "../../Pictures/inspection_screen.jpg";
import incidentScreen from "../../Pictures/incident_screen.jpg";
import dispatchScreen from "../../Pictures/dispatch_screen.jpg";
import auditScreen from "../../Pictures/audit_screen.jpg";
// Field response mobile screens.
import mobileWorkOrder from "../../Pictures/mobile_workorder.png";
import mobileEvidence from "../../Pictures/mobile_evidence.png";

// Carousel slides — drain/flood field imagery for the hero.
// Add more images to frontend/Pictures/ and add entries here.
const heroSlides: Slide[] = [
  { image: heroFrame, title: "CCTV-17 · Sector 4", caption: "ITO underpass drainage — live feed analysis frame." },
  { image: cloggedDrains, title: "Capital Flooded", caption: "Clogged city drains leave Delhi submerged after monsoon rain." },
  { image: barapullaDrain, title: "Barapulla Nallah", caption: "Inspection of Barapulla, Kushak and Sunehri drain systems." },
  { image: evidenceFrame, title: "Blocked Nallah", caption: "Open-channel blockage detected at 78.4% severity." },
  { image: drownIncident, title: "Human Cost", caption: "Lives lost to undetected drainage failures every monsoon." },
  { image: drainagePlan, title: "30-Year Challenge", caption: "Delhi's new drainage master plan targets decades of urban flooding." },
  { image: newslaundryDrain, title: "Ground Report", caption: "Investigative reporting on Delhi's failing drain infrastructure." },
];

const pipeline = [
  ["01", "Camera input", "Existing public or municipal CCTV provides drainage imagery."],
  ["02", "Frame analysis", "Relevant frames from the camera stream are processed by DRAINWATCH."],
  ["03", "Semantic segmentation", "The model distinguishes structural concrete, flowing water and solid waste."],
  ["04", "Blockage calculation", "The occupied proportion of the functional drainage area is calculated."],
  ["05", "Severity", "<40% Normal · 40–69.9% Warning · ≥70% Critical."],
  ["06", "Alert", "A critical detection is surfaced to the responsible municipal operator."],
  ["07", "Verification", "The operator reviews evidence before creating an incident."],
  ["08", "Response", "The verified incident moves into dispatch and field response."],
  ["09", "Resolution + audit", "Final evidence, state changes and operational history are recorded."],
];

function MediaSlot({
  label,
  guidance,
  ratio = "16 / 9",
  className = "",
  src,
  alt,
  children,
}: {
  label: string;
  guidance: string;
  ratio?: string;
  className?: string;
  src?: string;
  alt?: string;
  children?: ReactNode;
}) {
  return (
    <div className={`real-media-slot ${src ? "has-media" : ""} ${className}`} style={{ aspectRatio: ratio }} data-media-slot={label}>
      {src ? <img className="media-slot-image" src={src} alt={alt ?? label} loading="lazy" /> : <div className="media-slot-guidance"><span>PROJECT MEDIA</span><strong>{label}</strong><small>{guidance}</small></div>}
      {children}
    </div>
  );
}

function LandingNav({ onOpenDashboard }: { onOpenDashboard: () => void }) {
  const [open, setOpen] = useState(false);
  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
    setOpen(false);
  };
  return (
    <header className="landing-nav">
      <div className="landing-nav-inner">
        <button className="landing-wordmark" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}><img src="/logo.png" alt="" className="wordmark-logo" /><span className="wm-drain">DRAIN</span><span className="wm-watch">WATCH</span></button>
        <nav className={open ? "open" : ""} aria-label="Landing page navigation">
          <button onClick={() => scrollTo("platform")}>Platform</button>
          <button onClick={() => scrollTo("how-it-works")}>How It Works</button>
          <button onClick={() => scrollTo("operations")}>Operations</button>
          <button onClick={() => scrollTo("trust")}>Trust</button>
        </nav>
        <div className="landing-nav-actions"><Button variant="primary" onClick={onOpenDashboard}>Open Dashboard</Button><button className="landing-menu" onClick={() => setOpen(!open)} aria-label={open ? "Close menu" : "Open menu"}>{open ? <X size={19} /> : <Menu size={19} />}</button></div>
      </div>
    </header>
  );
}

function HeroCarousel() {
  return (
    <div className="hero-carousel-wrap">
      <CardDeckCarousel
        slides={heroSlides}
        height="auto"
        cardWidth="min(90%, 560px)"
        aspect="16 / 10"
        scatter={5}
        autoplay={3800}
        background="transparent"
        ink="#d0d5dd"
        className="hero-deck"
        ariaLabel="DrainWatch field imagery carousel"
      />
    </div>
  );
}

export function LandingPage({ onOpenDashboard }: { onOpenDashboard: () => void }) {
  const scrollTo = (id: string) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });
  return (
    <div className="landing-page">
      <LandingNav onOpenDashboard={onOpenDashboard} />
      <main>
        <section className="landing-hero cctv-landing-hero" id="platform">
          <AnimatedHero onOpenDashboard={onOpenDashboard} onSeeHowItWorks={() => scrollTo("how-it-works")} />
          <HeroCarousel />
        </section>

        <section className="landing-proof cctv-proof" aria-label="DRAINWATCH system capabilities">
          <div><span>EXISTING CCTV INFRASTRUCTURE</span><strong>Use available camera feeds and frames</strong></div>
          <div><span>AUTOMATED ANALYSIS</span><strong>Detect drainage blockage from imagery</strong></div>
          <div><span>SEVERITY CLASSIFICATION</span><strong>Normal · Warning · Critical</strong></div>
          <div><span>OPERATOR VERIFICATION</span><strong>Human confirmation before incident creation</strong></div>
          <div><span>AUDITABLE RESPONSE</span><strong>Detection → Dispatch → Resolution</strong></div>
        </section>

        <section className="landing-section problem-section">
          <div className="landing-section-intro"><span>THE OPERATING PROBLEM</span><h2>Blocked drains are often discovered after they become a problem.</h2><p>Municipal teams may already have cameras watching roads and public infrastructure, but useful visual evidence can remain unmonitored or depend on continuous manual observation. DRAINWATCH adds an automated monitoring layer for earlier, actionable detection.</p></div>
          <div className="before-after cctv-before-after">
            <div><span>TODAY</span>{["Public CCTV","Visual data","Manual observation","Problem noticed later","Response"].map((item, index) => <p key={item}><b>0{index + 1}</b>{item}</p>)}</div>
            <div><span>WITH DRAINWATCH</span>{["Public CCTV","Continuous analysis","Blockage detected","Severity determined","Authority alerted","Response"].map((item, index) => <p key={item}><b>0{index + 1}</b>{item}</p>)}</div>
          </div>
        </section>

        <section className="landing-section pipeline-section" id="how-it-works">
          <div className="landing-section-intro"><span>SYSTEM FLOW</span><h2>From camera feed to municipal action.</h2><p>Existing cameras provide the imagery. DRAINWATCH turns relevant frames into evidence, alerts and accountable field response.</p></div>
          <div className="landing-pipeline">{pipeline.map(([number,title,description]) => <article key={number}><code>{number}</code><h3>{title}</h3><p>{description}</p>{title === "Severity" && <div className="pipeline-thresholds"><span><code>&lt;40%</code> Normal</span><span><code>40–69.9%</code> Warning</span><span><code>≥70%</code> Critical</span></div>}</article>)}</div>
        </section>

        <section className="evidence-story">
          <div className="landing-section-intro"><span>MODEL EVIDENCE</span><h2>Don’t just flag a drain. Show why.</h2><p>Operators inspect the source frame, segmentation result and calculated blockage before a detection becomes an incident.</p></div>
          <div className="real-evidence-grid">
            <figure><figcaption>ORIGINAL CCTV / INSPECTION FRAME</figcaption><MediaSlot label="SOURCE DRAINAGE FRAME" guidance="Recommended crop: 4:3 · preserve camera timestamp where available" ratio="4 / 3" src={evidenceFrame} alt="Original drainage inspection frame showing a blocked channel" /></figure>
            <figure><figcaption>SEGMENTATION OUTPUT</figcaption><MediaSlot label="SEGMENTATION OUTPUT" guidance="Semantic segmentation: structure / water / waste" ratio="4 / 3" src={segmentationOutput} alt="AI segmentation output showing water (blue) and waste (magenta) regions in a blocked drain" /></figure>
            <aside><span>OPERATIONAL RESULT</span><strong>78.4<small>%</small></strong><SeverityLabel severity="critical" value="78.4%" /><dl><div><dt>Confidence</dt><dd><code>94.2%</code></dd></div><div><dt>Asset</dt><dd><code>D-1042</code></dd></div><div><dt>Inspection</dt><dd><code>INSP-24891</code></dd></div><div><dt>Source</dt><dd><code>CCTV-17</code></dd></div></dl><div className="confirmation-separation"><span>Model-computed severity<strong>CRITICAL</strong></span><span>Operator status<strong>VERIFIED</strong></span></div></aside>
          </div>
        </section>

        <section className="landing-section automatic-alert-section">
          <div className="landing-section-intro"><span>AUTOMATIC ALERT</span><h2>When the system finds a critical blockage, the workflow starts.</h2><p>The alert brings the camera source, evidence, severity and affected asset into the operator’s review queue.</p></div>
          <div className="alert-progression">{[["CAMERA","CCTV-17","Existing municipal source"],["DETECTION","D-1042","78.4% blockage"],["CRITICAL","Threshold exceeded","≥70%"],["ALERT","Requires attention","Municipal operator"],["VERIFICATION","Evidence reviewed","Operator-confirmed"],["INCIDENT","INC-24891","Ready for dispatch"]].map(([label,title,detail], index) => <div key={label}><code>0{index + 1}</code><span>{label}</span><strong>{title}</strong><p>{detail}</p></div>)}</div>
        </section>

        <section className="command-preview-section" id="operations">
          <div className="landing-section-intro"><span>OPERATIONS</span><h2>From detection to coordinated response.</h2><p>DRAINWATCH gives municipal operators one operational view for reviewing detections, managing incidents, dispatching crews and tracking resolution.</p></div>
          <MediaSlot label="DRAINWATCH OPERATIONS DASHBOARD" guidance="Critical alerts, priority queue, field response and geographic risk" className="product-screen-slot" src={dashboardScreenshot} alt="DrainWatch operations dashboard showing critical incidents, priority queue and Delhi map" />
        </section>

        <section className="landing-section application-sequence">
          <div className="landing-section-intro"><span>DETECTION TO AUDIT</span><h2>One record, carried through the operation.</h2><p>Detection, incident, work order and resolution remain separate records with an explicit relationship.</p></div>
          <div className="screen-sequence">
            {([["DETECTION","INSP-24891",inspectionScreen,"78.4% · 09:36 IST","Inspection detail with segmentation evidence and class breakdown"],["INCIDENT","INC-24891",incidentScreen,"Ready for dispatch","Incident management with state timeline and SLA"],["DISPATCH","WORK ORDER",dispatchScreen,"Crew · Destination · SLA","Dispatch panel with map, crew and equipment checklist"],["AUDIT","TRACEABLE RECORD",auditScreen,"Actor · Timestamp · Reason","Append-only audit trail of operational events"]] as [string, string, string, string, string][]).map(([label,title,screen,detail,alt]) => <article key={label}><span>{label}</span><h3>{title}</h3><MediaSlot label={title} guidance={detail} ratio="16 / 10" src={screen} alt={alt} /><p>{detail}</p></article>)}
          </div>
        </section>

        <section className="landing-section map-field-section">
          <div className="landing-section-intro"><span>OPERATIONAL MAP</span><h2>Know where the problem is.</h2><p>Connect camera sources and drainage assets to active incidents, jurisdiction context and field response.</p></div>
          <div className="map-field-grid">
            <div className="landing-map"><OperationalMap records={mapRecords} compact /></div>
            <div className="landing-work-order"><span>SELECTED ASSET</span><div className="work-order-title"><code>D-1042</code><SeverityLabel severity="critical" value="78.4%" /></div><h3>Drain D-1042 · Sector 4</h3><dl><div><dt>Camera source</dt><dd><code>CCTV-17</code></dd></div><div><dt>Incident</dt><dd><code>INC-24891</code></dd></div><div><dt>Response state</dt><dd><StatusIndicator tone="info">Ready for dispatch</StatusIndicator></dd></div><div><dt>Coordinates</dt><dd><code>28.6139, 77.2090</code></dd></div><div><dt>SLA</dt><dd><code>00:42 remaining</code></dd></div></dl><Button onClick={onOpenDashboard}>Open operational map</Button></div>
          </div>
        </section>

        <section className="trust-section" id="trust">
          <div className="landing-section-intro"><span>OPERATIONAL TRUST</span><h2>Built for decisions that need to be explained.</h2><p>Evidence, actor, timestamp, state and reason remain linked to the affected record. Operational history is append-only.</p></div>
          <div className="trust-grid"><ol>{[["09:32 IST","Detection received"],["09:34 IST","Model analysis completed"],["09:36 IST","Operator verified"],["09:38 IST","Ready for dispatch"]].map(([time,event]) => <li key={event}><time>{time}</time><span>{event}</span></li>)}</ol><div className="audit-fragment"><div><span>ACTOR</span><span>RECORD</span><span>STATE</span><span>REASON</span></div><div><strong>Rishabh Singh</strong><code>INC-24891</code><span>Verified → Ready for dispatch</span><span>Evidence confirmed</span></div><div><strong>System</strong><code>INSP-24891</code><span>Processing → Model computed</span><span>Segmentation complete</span></div></div></div>
          <div className="state-distinction"><span>Model-computed severity</span><b>≠</b><span>Operator-confirmed status</span><b>≠</b><span>Incident state</span><b>≠</b><span>Work-order state</span></div>
        </section>

        <section className="landing-section field-real-media">
          <div className="landing-section-intro"><span>FIELD RESPONSE</span><h2>From the control room to the field.</h2><p>Work-order context, location, safety notes, evidence capture and final blockage remain available without carrying the entire control room.</p></div>
          <div className="mobile-media-stage"><MediaSlot label="FIELD WORK ORDER" guidance="Asset, severity, location and field instruction" ratio="9 / 19.5" src={mobileWorkOrder} alt="Mobile work-order view for field crew" /><MediaSlot label="EVIDENCE CAPTURE" guidance="Evidence capture, final blockage and completion" ratio="9 / 19.5" src={mobileEvidence} alt="Mobile evidence capture showing drain condition" /></div>
        </section>

        <section className="landing-final-cta">
          <div><span>EXISTING CAMERAS. ACTIONABLE DETECTIONS.</span><h2>See what your cameras could be telling you.</h2><p>DRAINWATCH turns drainage imagery into actionable municipal intelligence.</p></div>
          <div><Button variant="primary" onClick={onOpenDashboard}>Open Operations Dashboard</Button><Button onClick={() => scrollTo("how-it-works")}>Explore the System</Button></div>
        </section>
      </main>
      <footer className="landing-footer">
        <div><button className="landing-wordmark" onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}><img src="/logo.png" alt="" className="wordmark-logo" /><span className="wm-drain">DRAIN</span><span className="wm-watch">WATCH</span></button><p>AI-powered municipal drainage monitoring and choke-point detection.</p></div>
        <div><strong>PLATFORM</strong>{["Platform","How It Works","Operations","Trust"].map((item) => <button onClick={() => scrollTo(item === "How It Works" ? "how-it-works" : item.toLowerCase())} key={item}>{item}</button>)}</div>
        <div><strong>ACCESS</strong><button onClick={onOpenDashboard}>Dashboard</button><span>Inspection</span><span>Incident</span><span>Dispatch</span><span>Audit</span></div>
        <small>Existing public cameras watch the city. DRAINWATCH watches for blocked drains.</small>
      </footer>
    </div>
  );
}
