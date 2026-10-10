import { useState, type FormEvent } from "react";
import { Bell, KeyRound, UserRound } from "lucide-react";
import { updateProfile, type AuthUser } from "../api/auth";
import { Button, PageHeader, StatusIndicator } from "../components/ui";

export function ProfilePage({ user, onUpdated }: { user: AuthUser; onUpdated: (user: AuthUser) => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(user.name);
  const [organization, setOrganization] = useState(user.organization || "");
  const [timezone, setTimezone] = useState(user.timezone || "Asia/Kolkata");
  const [notifications, setNotifications] = useState(user.notifications ?? true);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");

  const save = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    setMessage("");
    try {
      onUpdated(await updateProfile({ name, organization, timezone, notifications }));
      setEditing(false);
      setMessage("Changes saved");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Profile changes could not be saved.");
    } finally {
      setSaving(false);
    }
  };

  const cancel = () => {
    setName(user.name); setOrganization(user.organization || ""); setTimezone(user.timezone || "Asia/Kolkata"); setNotifications(user.notifications ?? true); setEditing(false); setMessage("");
  };
  const initials = user.name.split(/\s+/).map((part) => part[0]).join("").slice(0, 2).toUpperCase();
  return <div className="page profile-page"><PageHeader title="Profile" description="Manage your account and operational preferences." action={!editing && <Button onClick={() => setEditing(true)}>Edit Profile</Button>} />
    <section className="profile-summary"><div className="profile-avatar">{user.avatar_url ? <img src={user.avatar_url} alt="" /> : initials}</div><div><h2>{user.name}</h2><p>{user.email}</p><span>{user.title || (user.role === "ADMIN" ? "System Administrator" : "Operations Operator")}</span></div><StatusIndicator tone="normal">{user.status || "Active"}</StatusIndicator></section>
    <form className="profile-grid" onSubmit={save}>
      <section className="profile-section"><header><UserRound size={17} /><div><span>ACCOUNT INFORMATION</span><h2>Identity and organization</h2></div></header><label>Full name<input value={name} disabled={!editing} onChange={(event) => setName(event.target.value)} /></label><label>Email<input value={user.email} disabled /><small>Email changes require account confirmation.</small></label><label>Organization / jurisdiction<input value={organization} disabled={!editing} onChange={(event) => setOrganization(event.target.value)} /></label><dl><div><dt>Role</dt><dd>{user.role === "ADMIN" ? "Administrator" : "Operations Operator"}</dd></div><div><dt>Account status</dt><dd>{user.status || "Active"}</dd></div></dl></section>
      <section className="profile-section"><header><KeyRound size={17} /><div><span>SECURITY</span><h2>Account security</h2></div></header><dl><div><dt>Password</dt><dd>Managed securely</dd></div><div><dt>Last sign-in</dt><dd><time>{user.last_sign_in || "Current session"}</time></dd></div><div><dt>Active sessions</dt><dd>Current browser</dd></div></dl><Button type="button" disabled>Change Password</Button><small>Password changes require a dedicated secure backend workflow.</small></section>
      <section className="profile-section preferences"><header><Bell size={17} /><div><span>PREFERENCES</span><h2>Operational preferences</h2></div></header><label>Timezone<select value={timezone} disabled={!editing} onChange={(event) => setTimezone(event.target.value)}><option value="Asia/Kolkata">Asia/Kolkata (IST)</option><option value="UTC">UTC</option></select></label><label className="check-field"><input type="checkbox" checked={notifications} disabled={!editing} onChange={(event) => setNotifications(event.target.checked)} /> Operational notifications</label><dl><div><dt>Map preference</dt><dd>{user.map_preference || "Operational records"}</dd></div></dl></section>
      {message && <p className={message === "Changes saved" ? "save-message" : "save-message error"} role="status">{message}</p>}
      {editing && <div className="profile-actions"><Button type="button" onClick={cancel}>Cancel</Button><Button variant="primary" type="submit" disabled={saving}>{saving ? "Saving..." : "Save Changes"}</Button></div>}
    </form>
  </div>;
}
