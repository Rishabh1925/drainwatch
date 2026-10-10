import { useEffect, useState, type ButtonHTMLAttributes, type ReactNode } from "react";
import { AlertTriangle, Check, LoaderCircle } from "lucide-react";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "danger" | "quiet";
  icon?: ReactNode;
};

export function Button({ variant = "secondary", icon, className = "", children, ...props }: ButtonProps) {
  return (
    <button className={`button button-${variant} ${className}`} {...props}>
      {icon}{children}
    </button>
  );
}

export type Severity = "critical" | "warning" | "normal";

export function SeverityLabel({ severity, value }: { severity: Severity; value: string }) {
  return <span className={`severity severity-${severity}`}><i />{severity.toUpperCase()} · <b>{value}</b></span>;
}

export function StatusIndicator({
  tone = "neutral",
  children,
}: {
  tone?: "normal" | "warning" | "critical" | "info" | "neutral";
  children: ReactNode;
}) {
  return <span className={`status status-${tone}`}><i />{children}</span>;
}

export function PageHeader({
  title,
  description,
  status,
  action,
  eyebrow,
}: {
  title: string;
  description: string;
  status?: ReactNode;
  action?: ReactNode;
  eyebrow?: string;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <span className="page-eyebrow">{eyebrow}</span>}
        <h1>{title}</h1>
        <p>{description}</p>
        {status && <div className="page-status">{status}</div>}
      </div>
      {action && <div className="page-actions">{action}</div>}
    </header>
  );
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <h2>{title}</h2>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorState({
  title,
  description,
  reference,
  children,
}: {
  title: string;
  description: string;
  reference: string;
  children: ReactNode;
}) {
  return (
    <div className="error-state">
      <AlertTriangle size={18} />
      <div><h2>{title}</h2><p>{description}</p><code>{reference}</code><div className="error-actions">{children}</div></div>
    </div>
  );
}

export function LoadingRows({ rows = 5 }: { rows?: number }) {
  return <>{Array.from({ length: rows }, (_, index) => <div className="skeleton-row" key={index}><i /><i /><i /><i /></div>)}</>;
}

export function ProcessingState() {
  const steps = ["Upload received", "Image validation", "Segmentation processing", "Calculating blockage", "Saving evidence"];
  const [completed, setCompleted] = useState(0);

  useEffect(() => {
    if (completed >= steps.length) return;
    const delay = completed === 0 ? 400 : 500 + Math.random() * 400;
    const timer = window.setTimeout(() => setCompleted((c) => c + 1), delay);
    return () => window.clearTimeout(timer);
  }, [completed, steps.length]);

  return (
    <div className="processing-state">
      <LoaderCircle className="spin" size={20} />
      <h2>{completed >= steps.length ? "Analysis complete" : "Analysis in progress"}</h2>
      <ol>{steps.map((step, index) => {
        const done = index < completed;
        const active = index === completed && completed < steps.length;
        return <li className={done ? "done" : active ? "active" : ""} key={step}>{done && <Check size={13} />}{step}</li>;
      })}</ol>
      <p>{completed >= steps.length ? "Loading results…" : "Analysis is continuing. You may leave this page."}</p>
    </div>
  );
}
