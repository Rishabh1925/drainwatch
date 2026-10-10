import type { PageKey } from "../components/AppShell";
import { Button, PageHeader, StatusIndicator } from "../components/ui";

export function OverviewPlaceholder({ onReview }: { onReview: () => void }) {
  return (
    <div className="page">
      <PageHeader
        title="Overview"
        description="Municipal drainage monitoring across the active jurisdiction."
        status={<StatusIndicator tone="normal">System operational</StatusIndicator>}
        action={<Button variant="primary" onClick={onReview}>Review highest-priority incident</Button>}
      />
      <section className="shell-placeholder" aria-label="Overview content placeholder">
        <div><span>PAGE CONTENT REGION</span><h2>Overview workspace</h2><p>The operational overview will be designed as a separate workflow. This frame establishes the shared page grid, spacing, actions and system context.</p></div>
        <div className="grid-guide">{Array.from({ length: 12 }, (_, i) => <i key={i} />)}</div>
      </section>
    </div>
  );
}

const descriptions: Record<PageKey, string> = {
  overview: "",
  inspections: "",
  incidents: "",
  map: "Locate monitored assets, active incidents and response units across the jurisdiction.",
  dispatch: "Assign verified drainage incidents to municipal field response units.",
  history: "Review immutable inspection, incident and dispatch activity.",
  system: "Monitor analysis services, image sources and infrastructure availability.",
};

export function SectionPlaceholder({ page }: { page: PageKey }) {
  const title = page.charAt(0).toUpperCase() + page.slice(1);
  return (
    <div className="page">
      <PageHeader title={title} description={descriptions[page]} status={<StatusIndicator tone="normal">System operational</StatusIndicator>} />
      <section className="shell-placeholder"><div><span>FOUNDATION READY</span><h2>{title} workspace</h2><p>This operational area will inherit the established shell and component system.</p></div></section>
    </div>
  );
}
