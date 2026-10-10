import { useState, type FormEvent } from "react";
import { ArrowLeft, Eye, EyeOff, LockKeyhole, UserPlus } from "lucide-react";
import { signIn, signUp, requestPasswordReset, AuthApiError, type AuthUser } from "../api/auth";
import { Button } from "../components/ui";

type Mode = "signin" | "signup" | "forgot";

export function LoginPage({ admin = false, expired = false, onAuthenticated, onBack, onAdmin }: { admin?: boolean; expired?: boolean; onAuthenticated: (user: AuthUser) => void; onBack: () => void; onAdmin: () => void }) {
  const [mode, setMode] = useState<Mode>("signin");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [organization, setOrganization] = useState("");
  const [visible, setVisible] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  const switchMode = (next: Mode) => {
    setMode(next);
    setError("");
    setSent(false);
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setSubmitting(true);
    setError("");
    try {
      if (mode === "forgot") {
        await requestPasswordReset(email);
        setSent(true);
      } else if (mode === "signup") {
        if (password !== confirmPassword) {
          setError("Passwords do not match.");
          return;
        }
        if (password.length < 8) {
          setError("Password must be at least 8 characters.");
          return;
        }
        onAuthenticated(await signUp({ name, email, password, organization: organization || undefined }));
      } else {
        onAuthenticated(await signIn(email, password, admin));
      }
    } catch (nextError) {
      const authError = nextError instanceof AuthApiError ? nextError : null;
      if (authError?.code === "ACCOUNT_DISABLED") {
        setError("This account has been disabled. Contact your administrator.");
      } else {
        setError(authError?.message || "The request could not be completed.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const headingIcon = mode === "signup" ? <UserPlus size={20} /> : <LockKeyhole size={20} />;
  const headingLabel = mode === "signup" ? "CREATE ACCOUNT" : admin ? "ELEVATED ACCESS" : "SECURE ACCESS";
  const headingTitle = mode === "forgot"
    ? "Reset your password"
    : mode === "signup"
      ? "Create your account"
      : admin
        ? "Administrator Access"
        : "Sign in to DRAINWATCH";
  const headingDescription = mode === "forgot"
    ? "Enter your account email. If it is registered, reset instructions will be sent."
    : mode === "signup"
      ? "Create a new operator account to access the municipal operations platform."
      : admin
        ? "Administrative credentials are required to manage users, roles, sources, and system configuration."
        : "Use your authorized municipal operations account.";

  return <div className="auth-page">
    <section className="auth-intro"><button className="auth-wordmark" onClick={onBack}><img src="/logo.png" alt="" className="wordmark-logo" /><span className="wm-drain">DRAIN</span><span className="wm-watch">WATCH</span></button><div><span>MUNICIPAL OPERATIONS PLATFORM</span><h1>Municipal drainage monitoring and response.</h1><p>Monitor drainage conditions, review AI detections, coordinate field response, and maintain an auditable operational record.</p></div><small>Protected operational access · Delhi NCR</small></section>
    <main className="auth-form-panel"><div className="auth-card">
      <button className="auth-back" onClick={onBack}><ArrowLeft size={14} /> Public site</button>
      <div className="auth-card-heading">{headingIcon}<span>{headingLabel}</span><h2>{headingTitle}</h2><p>{headingDescription}</p></div>
      {expired && <div className="auth-notice warning">Your session has expired. Sign in again to continue.</div>}
      {sent ? <div className="auth-success" role="status"><strong>Check your email</strong><p>If an account matches that email, password reset instructions have been sent.</p><Button onClick={() => switchMode("signin")}>Return to Sign In</Button></div> : <form onSubmit={submit}>

        {/* Sign-up: name field */}
        {mode === "signup" && <label>Full Name<input type="text" autoComplete="name" value={name} onChange={(e) => setName(e.target.value)} required placeholder="Rishabh Singh" /></label>}

        {/* Email — always shown */}
        <label>Email<input type="email" autoComplete="email" value={email} onChange={(e) => setEmail(e.target.value)} required placeholder="name@organization.gov" /></label>

        {/* Password — hidden in forgot mode */}
        {mode !== "forgot" && <label>Password<span className="password-field"><input type={visible ? "text" : "password"} autoComplete={mode === "signup" ? "new-password" : "current-password"} value={password} onChange={(e) => setPassword(e.target.value)} required minLength={mode === "signup" ? 8 : undefined} placeholder={mode === "signup" ? "Min 8 characters" : undefined} /><button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "Hide password" : "Show password"}>{visible ? <EyeOff size={15} /> : <Eye size={15} />}</button></span></label>}

        {/* Sign-up: confirm password */}
        {mode === "signup" && <label>Confirm Password<span className="password-field"><input type={visible ? "text" : "password"} autoComplete="new-password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} required /><button type="button" onClick={() => setVisible(!visible)} aria-label={visible ? "Hide password" : "Show password"}>{visible ? <EyeOff size={15} /> : <Eye size={15} />}</button></span></label>}

        {/* Sign-up: optional org */}
        {mode === "signup" && <label>Organization <small>(optional)</small><input type="text" value={organization} onChange={(e) => setOrganization(e.target.value)} placeholder="Delhi NCR Drainage" /></label>}

        {error && <div className="auth-notice error" role="alert">{error}</div>}

        <Button variant="primary" type="submit" disabled={submitting}>{
          submitting
            ? (mode === "forgot" ? "Sending Instructions..." : mode === "signup" ? "Creating Account..." : "Signing In...")
            : (mode === "forgot" ? "Send Reset Instructions" : mode === "signup" ? "Create Account" : "Sign In")
        }</Button>

        {/* Forgot password link — only in signin mode */}
        {mode === "signin" && <button className="auth-text-action" type="button" onClick={() => switchMode("forgot")}>Forgot Password?</button>}
      </form>}

      {/* Toggle between sign-in and sign-up */}
      {mode !== "forgot" && <div className="auth-secondary">{
        mode === "signin"
          ? <><span>New to DRAINWATCH?</span><button type="button" onClick={() => switchMode("signup")}>Create an account</button></>
          : <><span>Already have an account?</span><button type="button" onClick={() => switchMode("signin")}>Sign in</button></>
      }</div>}

      {/* Admin login link */}
      {!admin && mode === "signin" && <button className="admin-login-link" onClick={onAdmin}>Sign in as Administrator</button>}
      {admin && mode === "signin" && <button className="admin-login-link" onClick={onBack}>Return to standard sign in</button>}
    </div></main>
  </div>;
}
