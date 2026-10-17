import { FormEvent, createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import {
  Activity,
  Archive,
  Contact as AddressBook,
  ArrowRight,
  Bell,
  Boxes,
  Check,
  ChevronDown,
  CircleHelp,
  ClipboardCheck,
  ClipboardCopy,
  Clock3,
  Copy,
  Database,
  Download,
  Upload,
  Monitor,
  Eye,
  EyeOff,
  Filter,
  Globe2,
  Image as ImageIcon,
  KeyRound,
  Layers3,
  LockKeyhole,
  LogOut,
  Menu,
  Mail,
  Maximize2,
  Moon,
  Network,
  Palette,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  MoveRight,
  Pencil,
  Play,
  Plus,
  Power,
  RefreshCw,
  Search,
  Server,
  ShieldCheck,
  Sun,
  SlidersHorizontal,
  Tags,
  Trash2,
  UserRound,
  UsersRound,
  X,
  XCircle,
} from "lucide-react";
import { isOpenSessionState } from "./session-contract";
import { NewsletterPage } from "./newsletters/NewsletterPage";
import { runProxyTestQueue } from "./proxy-test";
import { api } from "./api";
import { AccountProfileImportModal, BulkProfileImportSection } from "./profile-import/ProfileImport";
import { ProfileCompactionModal } from "./profile-compaction/ProfileCompactionModal";
import { OvhPage } from "./ovh/OvhPage";
import { GoDaddyPage } from "./godaddy/GoDaddyPage";
import { canRunDesktopOperation, countDesktopAccounts, filterDesktopAccounts, searchDesktopAccounts, selectedLiveSessions, type DesktopAccountFilter } from "./desktop-contract";

type Group = { id: string; name: string; description: string | null; capacity: number; account_count: number; ready_count: number; issue_count: number; status: string };
type EmployeeGroupAccess = { group_id: string; group_name: string; access_level: "VIEWER" | "OPERATOR" | "MANAGER" };
type Employee = { id: string; full_name: string; email: string; role: string; status: string; notes: string | null; created_at: string; updated_at: string; group_access: EmployeeGroupAccess[] };
type AuthSession = { employee: { id: string; full_name: string; email: string; role: string }; permissions: { is_admin: boolean; group_ids: string[]; can_manage_workspace: boolean; can_operate_accounts: boolean } };
type AuthStatus = { authenticated: boolean; setup_required: boolean; session: AuthSession | null };
type NewsletterAgentChainEntry = { priority: number; name: string; enabled: boolean; provider: "openrouter" | "openai"; model: string; min_confidence: number; max_calls: number; timeout_seconds: number; vision_enabled: boolean; health?: string; consecutive_failures?: number; last_latency_ms?: number; last_error?: string };
type WorkspaceSettings = { id: string; organization_id: string; organization_name: string; default_group_capacity: number; default_login_concurrency: number; max_concurrent_operations: number; max_active_profiles: number; observer_interval_ms: number; session_idle_timeout_minutes: number; auto_close_completed_sessions: boolean; retain_audit_days: number; runtime_environment: string; runtime_browser_launch_enabled: boolean; runtime_observer_enabled: boolean; runtime_worker_enabled: boolean; runtime_redis_configured: boolean; updated_at: string; telegram_enabled: boolean; telegram_configured: boolean; telegram_chat_id: string | null; email_enabled: boolean; email_provider: "NONE" | "RESEND" | "SMTP"; email_configured: boolean; email_recipient_email: string | null; email_from_address: string | null; smtp_host: string | null; smtp_port: number; smtp_username: string | null; smtp_use_tls: boolean; notify_employee_logins: boolean; notify_group_reports: boolean; notify_account_status: boolean; notify_operation_failures: boolean; newsletter_agent_enabled: boolean; newsletter_agent_provider: "openrouter"; newsletter_agent_model: string; newsletter_agent_temperature: number; newsletter_agent_max_steps: number; newsletter_agent_timeout_seconds: number; newsletter_agent_shadow_mode: boolean; newsletter_agent_live_execution_enabled: boolean; newsletter_agent_learning_mode: boolean; newsletter_agent_chain: NewsletterAgentChainEntry[]; openrouter_api_key_configured: boolean; openai_api_key_configured: boolean; two_captcha_api_key_configured: boolean; two_captcha_enabled: boolean; ovh_endpoint: "ovh-eu" | "ovh-ca" | "ovh-us"; ovh_credentials_configured: boolean; ovh_application_key?: string; ovh_application_secret?: string; ovh_consumer_key?: string; openrouter_api_key?: string; openai_api_key?: string; two_captcha_api_key?: string; telegram_bot_token?: string; resend_api_key?: string; smtp_password?: string };
type Account = { id: string; email: string; group_id: string; group_name: string; lifecycle_status: string; readiness_status: string; login_status: string; gmail_status: string; secret_status: string; recovery_masked: string | null; two_fa_configured: boolean; tags?: string[]; proxy: { id: string | null; label: string | null; endpoint?: string | null; region: string | null; status: string }; profile: { id: string | null; storage_reference: string | null; status: string }; created_at: string; updated_at: string };
type Session = { id: string; account_id: string; account_email: string; profile_id: string; profile_storage_reference: string; profile_status: string; profile_saved?: boolean; profile_authenticated_at?: string | null; state: string; login_state: string; purpose?: string; action?: string; operation_status?: string; status_detail?: string | null; duration_seconds?: number | null; runtime_type: string; browser_opened: boolean; browser_alive?: boolean; observer_attached: boolean; automation_hint: string | null; session_url: string; opened_at: string; last_event_at: string; closed_at: string | null; failure_reason: string | null };
type AuditEvent = { id: string; action: string; target_type: string; target_id: string; result: string; metadata: Record<string, unknown>; created_at: string };
type ContactFile = { id: string; original_filename: string; media_type: string; size_bytes: number; contact_count: number; status: string; last_used_at: string | null; created_at: string; updated_at: string };
type Preview = { total_rows: number; valid_rows: number; duplicate_rows: number; invalid_rows: number; rows: { row_number: number; email?: string | null; recovery_masked?: string | null; two_fa_configured: boolean; valid: boolean; duplicate: boolean; error?: string | null }[] };
type Toast = { id: number; tone: "success" | "error" | "info"; message: string };
type LoginJob = { id: string; group_id: string | null; concurrency_limit: number; status: string; total_count: number; queued_count: number; running_count: number; succeeded_count: number; attention_count: number; failed_count: number; created_at: string; started_at: string | null; completed_at: string | null; updated_at: string; items: { id: string; account_id: string; account_email: string; session_id: string | null; state: string; attempts: number; last_error: string | null; paused_reason?: string | null; updated_at: string }[] };
type JobProgress = { id: string; status: string; concurrency_limit: number; total_count: number; queued_count: number; running_count: number; succeeded_count: number; attention_count: number; failed_count: number; cancelled_count: number; progress_percent: number; states: Record<string, number>; items: (LoginJob["items"][number] & { paused_reason?: string | null; completed_at?: string | null; confirmed_messages?: number | null; reviewed_message_count?: number | null; messages_needing_review?: number | null })[] };
type BrowserProfile = { id: string; account_id: string; account_email: string; storage_reference: string; status: string; lock_state: string; active_session_id: string | null; last_opened_at: string | null; last_closed_at: string | null; authenticated_at: string | null; created_at: string; updated_at: string };
type ProxyRecord = { id: string; label: string | null; proxy_group?: string | null; region: string | null; status: string; account_count: number; authenticated_count: number; account_emails: string[]; account_refs: { id: string; email: string }[]; provider: string | null; country: string | null; city: string | null; public_ip: string | null; test_status: string; test_error: string | null; latency_ms: number | null; last_tested_at: string | null };
type ProxyGroupList = { id: string; name: string; proxy_count: number };
type OvhProxyGroupLink = { id: string; proxy_group: string; resource_type?: "vps" | "public_cloud"; service_name?: string; instance_id?: string | null; server_name: string; status: string; ip: string | null };
type ProxyTestResponse = { proxy_id: string; status: string; public_ip: string | null; provider: string | null; country: string | null; city: string | null; latency_ms: number | null; tested_at: string; error: string | null };
type ProxyBulkImportRow = { line_number: number; status: "IMPORTED" | "DUPLICATE" | "INVALID"; message: string; proxy_id: string | null };
type ProxyBulkImportResult = { imported: number; duplicates: number; invalid: number; rows: ProxyBulkImportRow[] };
type CredentialRecord = { id: string; email: string; group_id: string; group_name: string; secret_status: string; password_present: boolean; recovery_masked: string | null; two_fa_configured: boolean; credential_status: string; active_version: number | null; latest_version: number; login_status: string; profile_status: string; updated_at: string };
type CredentialRevisionRow = { account_id: string; version: number; status: string; secret_status: string; recovery_masked: string | null; two_fa_configured: boolean; created_at: string };
type CredentialRotateResult = { account_id: string; email: string; password: string; recovery_masked: string | null; two_fa_configured: boolean; version: number };
type CredentialPlaintext = { account_id: string; email: string; password: string; recovery: string; recovery_masked: string | null; two_fa_configured: boolean; active_version: number | null; latest_version: number };
type AlertTopic = { id: string; topic: string; category: string; active: boolean; created_at: string; updated_at: string };
type AccountPreferences = { account_id: string; email: string; gmail_theme: "default" | "dark" | "light" | "high_contrast"; avatar_filename: string | null; avatar_available: boolean; updated_at: string | null };
type WorkspaceView = "overview" | "accounts" | "groups" | "employees" | "issues" | "sessions" | "desktop" | "proxies" | "profiles" | "credentials" | "newsletters" | "vps" | "godaddy" | "settings";
type InfrastructureView = "sessions" | "proxies" | "profiles" | "credentials";
type OperationKind = "contacts" | "security" | "labels" | "apps" | "mail" | "addons" | "alerts" | "preferences";
type RegistryAction = OperationKind | "login" | "credentials" | "edit" | "move" | "delete" | "tags" | "upload_profile";
const DesktopInspectorContext = createContext(false);
function useDesktopInspectorPresentation() { return useContext(DesktopInspectorContext); }
type ActionScope = "selected" | "group";
type CredentialField = "password" | "recovery" | "two_fa";
type CredentialPlan = { id: string; account_id: string; account_email: string; version: number; status: string; password_requested: boolean; recovery_requested: boolean; two_fa_requested: boolean; recovery_hint: string | null; recovery_count: number; session_id: string | null; provisioned_two_fa_secret?: string | null; created_at: string; updated_at: string; started_at: string | null; confirmed_at: string | null; completed_at: string | null; failure_reason: string | null };

const formatDate = (value: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
const formatDateTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)) : "—";
const formatBytes = (value: number) => value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : value < 1024 ** 3 ? `${(value / (1024 * 1024)).toFixed(1)} MB` : `${(value / (1024 ** 3)).toFixed(2)} GB`;
const labelStatus = (value: string) => value === "PROFILE_IMPORTED" ? "Imported · checking sign-in" : value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
function useDebouncedValue<T>(value: T, delay = 300) { const [debounced, setDebounced] = useState(value); useEffect(() => { const timer = window.setTimeout(() => setDebounced(value), delay); return () => window.clearTimeout(timer); }, [value, delay]); return debounced; }

function LegacyLoginPage({ setupRequired, onAuthenticated }: { setupRequired: boolean; onAuthenticated: (session: AuthSession) => void }) {
  const [email, setEmail] = useState(setupRequired ? "admin@northstar.local" : "");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault(); setPending(true); setError("");
    try {
      const session = await api<AuthSession>(setupRequired ? "/api/v1/auth/setup" : "/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
      onAuthenticated(session);
    } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); }
  };
  return <main className="auth-shell"><section className="auth-card" aria-labelledby="auth-title"><div className="brand-lockup"><div className="brand-mark"><Layers3 size={18} /></div><div><strong>Northstar</strong><span>Gmail operations</span></div></div><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE ACCESS</div><h1 id="auth-title">{setupRequired ? "Set up the administrator" : "Sign in to Northstar"}</h1><p>{setupRequired ? "Create the first administrator identity for this workspace. You can invite employees after signing in." : "Use your employee workspace credentials to access only the groups assigned to you."}</p><form onSubmit={submit} noValidate><label className="auth-field"><span>Work email</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" required disabled={pending} /></label><label className="auth-field"><span>{setupRequired ? "Administrator password" : "Password"}</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={setupRequired ? "new-password" : "current-password"} required disabled={pending} /></label>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<button type="submit" className="button primary auth-submit" disabled={pending}>{pending ? "Connecting…" : setupRequired ? "Create administrator" : "Sign in"}</button></form><div className="security-callout"><ShieldCheck size={17} /><div><strong>Scoped workspace access</strong><span>Employee sessions are separate from Gmail account credentials. Group assignments are enforced by the API.</span></div></div></section></main>;
}

function LoginPage({ setupRequired, onAuthenticated }: { setupRequired: boolean; onAuthenticated: (session: AuthSession) => void }) {
  const [email, setEmail] = useState(setupRequired ? "admin@northstar.local" : "");
  const [password, setPassword] = useState("");
  const [resetPassword, setResetPassword] = useState("");
  const [resetToken, setResetToken] = useState("");
  const [mode, setMode] = useState<"login" | "request" | "confirm">("login");
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  useEffect(() => { const token = new URLSearchParams(window.location.search).get("reset_token"); if (token) { setResetToken(token); setMode("confirm"); } }, []);
  const submitLogin = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(""); try { onAuthenticated(await api<AuthSession>(setupRequired ? "/api/v1/auth/setup" : "/api/v1/auth/login", { method: "POST", body: JSON.stringify({ email, password }) })); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); } };
  const requestReset = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(""); setMessage(""); try { await api<void>("/api/v1/auth/password-reset/request", { method: "POST", body: JSON.stringify({ email }) }); setMessage("If that employee account exists, a reset link has been sent through the configured email provider."); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); } };
  const confirmReset = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(""); try { await api<void>("/api/v1/auth/password-reset/confirm", { method: "POST", body: JSON.stringify({ token: resetToken, password: resetPassword }) }); setMode("login"); setPassword(""); setMessage("Password reset complete. Sign in with your new password."); window.history.replaceState({}, "", window.location.pathname); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); } };
  const title = mode === "request" ? "Reset workspace password" : mode === "confirm" ? "Choose a new password" : setupRequired ? "Set up the administrator" : "Sign in to Northstar";
  return <main className="auth-shell"><section className="auth-card" aria-labelledby="auth-title"><div className="brand-lockup"><div className="brand-mark"><Layers3 size={18} /></div><div><strong>Northstar</strong><span>Gmail operations</span></div></div><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE ACCESS</div><h1 id="auth-title">{title}</h1><p>{mode === "request" ? "Enter your employee email and we will send a one-hour reset link." : mode === "confirm" ? "Set a new password with at least 12 characters." : setupRequired ? "Create the first administrator identity for this workspace. You can invite employees after signing in." : "Use your employee workspace credentials to access only the groups assigned to you."}</p>{mode === "login" && <form onSubmit={submitLogin} noValidate><label className="auth-field"><span>Work email</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="username" required disabled={pending} /></label><label className="auth-field"><span>{setupRequired ? "Administrator password" : "Password"}</span><input type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete={setupRequired ? "new-password" : "current-password"} required disabled={pending} /></label>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{message && <div className="security-callout" role="status"><Check size={17} /><div><strong>Reset request</strong><span>{message}</span></div></div>}<button type="submit" className="button primary auth-submit" disabled={pending}>{pending ? "Connecting…" : setupRequired ? "Create administrator" : "Sign in"}</button>{!setupRequired && <button type="button" className="text-button auth-reset-link" onClick={() => { setMode("request"); setError(""); setMessage(""); }}>Forgot password?</button>}</form>}{mode === "request" && <form onSubmit={requestReset} noValidate><label className="auth-field"><span>Work email</span><input type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required disabled={pending} /></label>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{message && <div className="security-callout" role="status"><Check size={17} /><div><strong>Check your inbox</strong><span>{message}</span></div></div>}<button type="submit" className="button primary auth-submit" disabled={pending}>{pending ? "Sending…" : "Send reset link"}</button><button type="button" className="text-button auth-reset-link" onClick={() => { setMode("login"); setError(""); setMessage(""); }}>Back to sign in</button></form>}{mode === "confirm" && <form onSubmit={confirmReset} noValidate><label className="auth-field"><span>New password</span><input type="password" value={resetPassword} onChange={(event) => setResetPassword(event.target.value)} minLength={12} autoComplete="new-password" required disabled={pending} /></label>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<button type="submit" className="button primary auth-submit" disabled={pending || resetPassword.length < 12}>{pending ? "Updating…" : "Update password"}</button><button type="button" className="text-button auth-reset-link" onClick={() => { setMode("login"); setError(""); }}>Back to sign in</button></form>}<div className="security-callout"><ShieldCheck size={17} /><div><strong>Scoped workspace access</strong><span>Employee sessions are separate from Gmail account credentials. Group assignments are enforced by the API.</span></div></div></section></main>;
}

function AccountPreferencesPage({ accounts, groups, onRefresh, onNotify, onOpenSession }: { accounts: Account[]; groups: Group[]; onRefresh: () => void; onNotify: (tone: Toast["tone"], message: string) => void; onOpenSession: (session: Session) => void }) {
  const [groupFilter, setGroupFilter] = useState("all");
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const [preferences, setPreferences] = useState<AccountPreferences | null>(null);
  const [theme, setTheme] = useState<AccountPreferences["gmail_theme"]>("default");
  const [pending, setPending] = useState<"load" | "save" | "apply" | null>(null);
  const [error, setError] = useState("");

  const filteredAccounts = groupFilter === "all" ? accounts : accounts.filter((account) => account.group_id === groupFilter);
  const selected = accounts.find((account) => account.id === accountId) ?? filteredAccounts[0] ?? null;

  useEffect(() => {
    if (selected && selected.id !== accountId) setAccountId(selected.id);
    if (!selected && accounts[0]) setAccountId(accounts[0].id);
  }, [accounts, selected, accountId]);

  useEffect(() => {
    if (!selected) { setPreferences(null); return; }
    let mounted = true;
    setPending("load"); setError("");
    api<AccountPreferences>(`/api/v1/accounts/${selected.id}/preferences`).then((result) => {
      if (!mounted) return;
      setPreferences(result); setTheme(result.gmail_theme);
    }).catch((requestError) => { if (mounted) setError((requestError as Error).message); }).finally(() => { if (mounted) setPending(null); });
    return () => { mounted = false; };
  }, [selected?.id]);

  const save = async () => {
    if (!selected) return;
    setPending("save"); setError("");
    try {
      const result = await api<AccountPreferences>(`/api/v1/accounts/${selected.id}/preferences`, { method: "PUT", body: JSON.stringify({ gmail_theme: theme, avatar_filename: preferences?.avatar_filename ?? null }) });
      setPreferences(result); onRefresh(); onNotify("success", `Preferences saved for ${selected.email}.`);
    } catch (requestError) { setError((requestError as Error).message); onNotify("error", (requestError as Error).message); } finally { setPending(null); }
  };

  const apply = async () => {
    if (!selected) return;
    setPending("apply"); setError("");
    try {
      const session = await api<Session>(`/api/v1/accounts/${selected.id}/preferences/apply`, { method: "POST", body: JSON.stringify({ change_avatar: true, change_theme: true, random_theme: true }) });
      onOpenSession(session); onNotify("success", `Preference session opened for ${selected.email}.`); onRefresh();
    } catch (requestError) { setError((requestError as Error).message); onNotify("error", (requestError as Error).message); } finally { setPending(null); }
  };

  return <>
    <section className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT PREFERENCES</div><h1>Account preferences</h1><p>Run the appearance change from each account's saved browser profile using Google's own illustration and Gmail theme pickers.</p></div><div className="heading-actions"><label className="select-wrap"><select aria-label="Filter account preferences by group" value={groupFilter} onChange={(event) => setGroupFilter(event.target.value)}><option value="all">All groups</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select><ChevronDown size={15} /></label></div></section>
    <section className="preferences-layout">
      <div className="workspace-panel preferences-account-panel"><div className="panel-header"><div><h2>Saved accounts</h2><p>Choose one account to edit its presentation settings.</p></div><span className="panel-count">{filteredAccounts.length}</span></div><div className="preferences-account-list">{filteredAccounts.map((account) => <button type="button" key={account.id} className={`preferences-account-row ${selected?.id === account.id ? "active" : ""}`} onClick={() => setAccountId(account.id)}><span className="account-avatar-mini">{account.email.slice(0, 1).toUpperCase()}</span><span><strong>{account.email}</strong><small>{account.group_name} · {labelStatus(account.profile.status)}</small></span><ArrowRight size={15} /></button>)}{!filteredAccounts.length && <div className="empty-state"><ImageIcon size={22} /><strong>No accounts in this group</strong><span>Choose another group or add an account first.</span></div>}</div></div>
      <div className="workspace-panel preferences-form-panel">{selected && <><div className="panel-header"><div><span className="detail-label">SELECTED ACCOUNT</span><h2>{selected.email}</h2><p>Use the account's saved Google session to change its appearance.</p></div><span className="status-pill">Google pickers</span></div>{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{pending === "load" ? <div className="preferences-loading"><span className="spinner" /> Loading preferences…</div> : <><div className="preferences-section"><div><span className="detail-label">PROFILE IMAGE</span><h3>Google illustration</h3><p>The browser opens Profile picture → Browse Illustrations and selects one of Google's existing illustrations at random. Nothing is uploaded from this workspace.</p></div><div className="avatar-editor"><div className="avatar-preview"><ImageIcon size={22} /></div><span className="status-pill">Random on run</span></div></div><div className="preferences-section"><div><span className="detail-label">GMAIL APPEARANCE</span><h3>Theme</h3><p>The browser opens Settings → Theme → View all, selects one visible theme at random, and presses Save. The process recognizes localized labels.</p></div><div className="theme-choice-grid"><div className="theme-choice theme-default selected"><span className="theme-choice-dot" /><strong>Random theme</strong><small>Selected from Gmail's View all gallery</small></div></div></div><div className="preferences-footer"><span className="detail-label">No local image or fixed theme required</span><div><button type="button" className="button secondary" onClick={() => void save()} disabled={pending !== null || !preferences}>{pending === "save" && <span className="button-spinner" />}Save preference record</button><button type="button" className="button primary" onClick={() => void apply()} disabled={pending !== null}>{pending === "apply" && <span className="button-spinner" />}<Play size={16} /> Run in Gmail</button></div></div></>}</>}{!selected && <div className="empty-state"><ImageIcon size={22} /><strong>Select an account</strong><span>Account preferences are edited one account at a time.</span></div>}</div>
    </section>
  </>;
}

function App() {
  const [auth, setAuth] = useState<AuthSession | null>(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [setupRequired, setSetupRequired] = useState(false);
  useEffect(() => { api<AuthStatus>("/api/v1/auth/status").then((status) => { setAuth(status.session); setSetupRequired(status.setup_required); }).catch(() => setAuth(null)).finally(() => setAuthLoading(false)); }, []);
  if (authLoading) return <main className="auth-shell"><section className="auth-card auth-loading"><span className="spinner" /><strong>Checking workspace session…</strong></section></main>;
  if (!auth) return <LoginPage setupRequired={setupRequired} onAuthenticated={(session) => { setAuth(session); setSetupRequired(false); }} />;
  return <WorkspaceApp auth={auth} onSignOut={() => setAuth(null)} />;
}

function WorkspaceApp({ auth, onSignOut }: { auth: AuthSession; onSignOut: () => void }) {
  const [appTheme, setAppTheme] = useState<"light" | "dark">(() => window.localStorage.getItem("northstar-app-theme") === "dark" ? "dark" : "light");
  const [groups, setGroups] = useState<Group[]>([]);
  const [headerOvhLinks, setHeaderOvhLinks] = useState<OvhProxyGroupLink[]>([]);
  const [headerOvhError, setHeaderOvhError] = useState("");
  const [selectedGroup, setSelectedGroup] = useState("all");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [profileSizes, setProfileSizes] = useState<Record<string, number | null>>({});
  const [profileExporting, setProfileExporting] = useState(false);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [pageSize, setPageSize] = useState("25");
  const [page, setPage] = useState(1);
  const [statusCounts, setStatusCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isOnboardingOpen, setOnboardingOpen] = useState(false);
  const [isContactLibraryOpen, setContactLibraryOpen] = useState(false);
  const [isBatchOpen, setBatchOpen] = useState(false);
  const [activeAccount, setActiveAccount] = useState<Account | null>(null);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [activeJob, setActiveJob] = useState<LoginJob | null>(null);
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [newsletterLaunchAccountIds, setNewsletterLaunchAccountIds] = useState<string[]>([]);
  const [actionScope, setActionScope] = useState<ActionScope>("selected");
  const [activeOperation, setActiveOperation] = useState<{ kind: OperationKind; accounts: Account[] } | null>(null);
  const [labelsMarkersTargets, setLabelsMarkersTargets] = useState<Account[] | null>(null);
  const [securityTargets, setSecurityTargets] = useState<Account[] | null>(null);
  const [addonsTargets, setAddonsTargets] = useState<Account[] | null>(null);
  const [alertsTargets, setAlertsTargets] = useState<Account[] | null>(null);
  const [mailTargets, setMailTargets] = useState<Account[] | null>(null);
  const [preferencesTargets, setPreferencesTargets] = useState<Account[] | null>(null);
  const [contactTargets, setContactTargets] = useState<Account[] | null>(null);
  const [activeAdminAction, setActiveAdminAction] = useState<{ kind: "edit" | "move" | "delete"; accounts: Account[] } | null>(null);
  const [tagTargets, setTagTargets] = useState<Account[] | null>(null);
  const [profileImportAccount, setProfileImportAccount] = useState<Account | null>(null);
  const [profileCompactionOpen, setProfileCompactionOpen] = useState(false);
  const [profileCompactionSelection, setProfileCompactionSelection] = useState<{ id: string; email: string }[]>([]);
  const [stopGroupConfirmOpen, setStopGroupConfirmOpen] = useState(false);
  const [stopAllConfirmOpen, setStopAllConfirmOpen] = useState(false);
  const [credentialAccount, setCredentialAccount] = useState<Account | null>(null);
  const [credentialChoiceAccount, setCredentialChoiceAccount] = useState<Account | null>(null);
  const [credentialField, setCredentialField] = useState<CredentialField | null>(null);
  const [credentialPlan, setCredentialPlan] = useState<CredentialPlan | null>(null);
  const [credentialPlanConfirmOpen, setCredentialPlanConfirmOpen] = useState(false);
  const [batchAccountIds, setBatchAccountIds] = useState<string[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [mobileNav, setMobileNav] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(() => window.localStorage.getItem("northstar-sidebar-collapsed") === "true");
  const [desktopOperationHost, setDesktopOperationHost] = useState<HTMLElement | null>(null);
  const [activeView, setActiveView] = useState<WorkspaceView>(() => {
    const stored = window.localStorage.getItem("northstar-active-view");
    const views: WorkspaceView[] = ["overview", "accounts", "groups", "employees", "issues", "sessions", "desktop", "proxies", "profiles", "credentials", "newsletters", "vps", "godaddy", "settings"];
    if (!stored || !views.includes(stored as WorkspaceView)) return "accounts";
    if (!auth.permissions.is_admin && (stored === "employees" || stored === "settings")) return "accounts";
    if (!auth.permissions.can_operate_accounts && (stored === "desktop" || stored === "proxies" || stored === "vps" || stored === "godaddy")) return "accounts";
    return stored as WorkspaceView;
  });
  const [refreshKey, setRefreshKey] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => { document.title = activeJob ? "Login batch — Gmail Operations" : activeSession ? "Login session — Gmail Operations" : activeAccount ? "Account detail — Gmail Operations" : `${activeView === "accounts" ? "Account registry" : activeView === "profiles" ? "Browser profiles" : activeView === "newsletters" ? "Newsletters" : activeView === "vps" ? "VPS providers" : labelStatus(activeView)} — Gmail Operations`; }, [activeAccount, activeSession, activeJob, activeView]);

  useEffect(() => {
    api<Group[]>("/api/v1/groups").then((data) => { setGroups(data); if (data.length && selectedGroup === "all") setSelectedGroup("all"); }).catch(() => setError("Groups are unavailable. Start the API and retry."));
  }, [refreshKey]);

  useEffect(() => {
    requestRef.current?.abort();
    const controller = new AbortController();
    requestRef.current = controller;
    const timer = window.setTimeout(async () => {
      setLoading(true);
      setError("");
      const params = new URLSearchParams({ page: String(page), page_size: pageSize === "all" ? "10000" : pageSize });
      if (query.trim()) params.set("q", query.trim());
      if (selectedGroup !== "all") params.set("group_id", selectedGroup);
      if (statusFilter !== "ALL") params.set("status", statusFilter);
      try {
        const data = await api<{ items: Account[]; total: number; status_counts?: Record<string, number> }>(`/api/v1/accounts?${params.toString()}`, { signal: controller.signal });
        if (!controller.signal.aborted) {
          setAccounts(data.items); setTotal(data.total); setStatusCounts(data.status_counts ?? {});
          if (pageSize !== "all") {
            const lastPage = Math.max(1, Math.ceil(data.total / Number(pageSize)));
            if (page > lastPage) { setPage(lastPage); return; }
          }
          setLoading(false);
        }
      } catch (requestError) {
        if ((requestError as Error).name !== "AbortError" && !controller.signal.aborted) { setLoading(false); setError("Accounts are unavailable. Retry the connection."); }
      }
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, selectedGroup, statusFilter, refreshKey, page, pageSize]);

  useEffect(() => {
    let cancelled = false;
    const targets = accounts.map((account) => account.id);
    if (!targets.length) { setProfileSizes({}); return () => { cancelled = true; }; }
    setProfileSizes({});
    const chunks = Array.from({ length: Math.ceil(targets.length / 100) }, (_, index) => targets.slice(index * 100, (index + 1) * 100));
    void (async () => {
      const collected: Record<string, number | null> = {};
      for (let index = 0; index < chunks.length; index += 3) {
        const batch = await Promise.all(chunks.slice(index, index + 3).map((account_ids) =>
          api<{ items: { account_id: string; size_bytes: number | null }[] }>("/api/v1/profiles/sizes", { method: "POST", body: JSON.stringify({ account_ids }) })
        ));
        batch.flatMap((result) => result.items).forEach((item) => { collected[item.account_id] = item.size_bytes; });
        if (cancelled) return;
      }
      if (!cancelled) setProfileSizes(collected);
    })().catch(() => { if (!cancelled) setProfileSizes(Object.fromEntries(targets.map((id) => [id, null]))); });
    return () => { cancelled = true; };
  }, [accounts]);

  const notify = (tone: Toast["tone"], message: string) => {
    const id = Date.now();
    setToasts((current) => [...current.filter((toast) => toast.message !== message), { id, tone, message }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4500);
  };

  const handleAccountContextAction = (account: Account, kind: "copy" | "login" | "edit" | "delete" | "subscribe", contextSelection?: string[]) => {
    const targets = contextSelection?.length ? contextSelection : [account.id];
    if (kind === "copy") {
      void (async () => {
        try {
          const emailsById = new Map(accounts.map((item) => [item.id, item.email]));
          const missingIds = targets.filter((id) => !emailsById.has(id));
          const fetched = await Promise.all(missingIds.map((id) => api<Account>(`/api/v1/accounts/${encodeURIComponent(id)}`)));
          fetched.forEach((item) => emailsById.set(item.id, item.email));
          const emails = targets.map((id) => emailsById.get(id));
          if (emails.some((email) => !email)) throw new Error("One or more selected account emails could not be loaded.");
          if (!navigator.clipboard?.writeText) throw new Error("Clipboard access is unavailable in this browser.");
          await navigator.clipboard.writeText(emails.join("\n"));
          notify("success", `Copied ${emails.length} account email${emails.length === 1 ? "" : "s"} to the clipboard.`);
        } catch (copyError) { notify("error", (copyError as Error).message || "Could not copy the selected account emails."); }
      })();
      return;
    }
    if (kind === "subscribe") { setNewsletterLaunchAccountIds(targets); setActiveView("newsletters"); return; }
    closeOperationConfigurations();
    if (kind === "login") { setBatchAccountIds(targets); setBatchOpen(true); return; }
    setSelectedAccountIds([account.id]); setActionScope("selected"); setActiveAdminAction({ kind, accounts: [account] });
  };

  const reload = () => setRefreshKey((current) => current + 1);
  const exportSelectedProfiles = async () => {
    if (!selectedAccountIds.length || profileExporting) return;
    if (selectedAccountIds.length > 100) { notify("error", "Export up to 100 selected account profiles at a time."); return; }
    setProfileExporting(true);
    try {
      const query = new URLSearchParams();
      selectedAccountIds.forEach((id) => query.append("account_ids", id));
      const link = document.createElement("a"); link.href = `/api/v1/profiles/export?${query.toString()}`; link.style.display = "none"; document.body.appendChild(link); link.click(); link.remove();
      notify("success", `Profile ZIP download started for ${selectedAccountIds.length} account${selectedAccountIds.length === 1 ? "" : "s"}.`);
    } catch (requestError) { notify("error", (requestError as Error).message || "Selected profiles could not be exported."); }
    finally { setProfileExporting(false); }
  };
  useEffect(() => { setSelectedAccountIds([]); }, [selectedGroup, query, statusFilter, refreshKey, page, pageSize]);
  const changeRegistryFilter = (value: string) => { setStatusFilter(value); setPage(1); };
  const changeRegistryQuery = (value: string) => { setQuery(value); setPage(1); };
  const changeRegistryGroup = (value: string) => { setSelectedGroup(value); setPage(1); };
  const registryFilters = [{ value: "ALL", label: "All" }, { value: "READY_FOR_LOGIN", label: "Ready for login" }, { value: "SETUP_REQUIRED", label: "Setup required" }, { value: "AUTHENTICATED", label: "Authenticated" }, { value: "NEEDS_ATTENTION", label: "Needs attention" }, { value: "VERIFICATION_REQUIRED", label: "Verification required" }, { value: "NEVER_LOGGED_IN", label: "Never logged in" }, { value: "NONEXISTENT", label: "Nonexistent" }];
  const registryPageSize = pageSize === "all" ? Math.max(total, 1) : Number(pageSize);
  const pageCount = pageSize === "all" ? 1 : Math.max(1, Math.ceil(total / registryPageSize));
  const firstVisible = total ? (page - 1) * registryPageSize + 1 : 0;
  const lastVisible = Math.min(page * registryPageSize, total);
  const visibleCount = selectedGroup === "all" ? groups.reduce((sum, group) => sum + group.account_count, 0) : groups.find((group) => group.id === selectedGroup)?.account_count ?? 0;
  const readyCount = selectedGroup === "all" ? groups.reduce((sum, group) => sum + group.ready_count, 0) : groups.find((group) => group.id === selectedGroup)?.ready_count ?? 0;
  const attentionAccounts = accounts.filter((account) => account.readiness_status === "SETUP_ERROR" || ["NONEXISTENT", "VERIFICATION_REQUIRED", "NEEDS_ATTENTION", "UNKNOWN_PAGE", "ACCOUNT_NOT_FOUND"].includes(account.login_status) || ["ERROR", "UNREACHABLE", "OFFLINE"].includes(account.proxy.status) || ["ERROR", "CORRUPT"].includes(account.profile.status));
  const selectedAccounts = accounts.filter((account) => selectedAccountIds.includes(account.id));
  const currentGroup = groups.find((group) => group.id === selectedGroup);
  const getGroupAccounts = async (): Promise<Account[]> => {
    if (selectedGroup === "all") return accounts;
    const first = await api<{ items: Account[]; total: number }>(`/api/v1/accounts?group_id=${encodeURIComponent(selectedGroup)}&page=1&page_size=100`);
    if (first.items.length >= first.total) return first.items;
    const pages = await Promise.all(Array.from({ length: Math.ceil(first.total / 100) - 1 }, (_, index) => api<{ items: Account[] }>(`/api/v1/accounts?group_id=${encodeURIComponent(selectedGroup)}&page=${index + 2}&page_size=100`)));
    return [ ...first.items, ...pages.flatMap((page) => page.items) ];
  };
  const resolveActionAccounts = async (): Promise<Account[]> => actionScope === "selected" ? selectedAccounts : getGroupAccounts();
  // Configuration surfaces are mutually exclusive. Switching an operation
  // dismisses the previous form without touching any already-running session.
  const closeOperationConfigurations = () => {
    setActiveOperation(null);
    setLabelsMarkersTargets(null);
    setSecurityTargets(null);
    setAddonsTargets(null);
    setAlertsTargets(null);
    setMailTargets(null);
    setPreferencesTargets(null);
    setContactTargets(null);
    setActiveAdminAction(null);
    setTagTargets(null);
    setProfileImportAccount(null);
    setProfileCompactionOpen(false);
    setCredentialAccount(null);
    setCredentialChoiceAccount(null);
    setCredentialField(null);
    setBatchOpen(false);
    setBatchAccountIds([]);
  };
  const stopGroupProcesses = () => {
    if (!currentGroup?.account_count) { notify("info", "Choose a group with accounts first."); return; }
    setStopGroupConfirmOpen(true);
  };
  const stopAllProcesses = () => setStopAllConfirmOpen(true);
  const confirmStopGroupProcesses = async () => {
    if (!currentGroup?.account_count) { setStopGroupConfirmOpen(false); return; }
    setStopGroupConfirmOpen(false);
    try {
      const result = await api<{ closed_count: number }>(`/api/v1/groups/${currentGroup.id}/sessions/close`, { method: "POST" });
      notify("success", result.closed_count ? `Stopped ${result.closed_count} active group process${result.closed_count === 1 ? "" : "es"}.` : "No active group processes were running.");
      reload();
    } catch (requestError) { notify("error", (requestError as Error).message); }
  };
  const confirmStopAllProcesses = async () => {
    setStopAllConfirmOpen(false);
    try {
      const result = await api<{ closed_count: number }>("/api/v1/sessions/close-active", { method: "POST" });
      notify("success", result.closed_count ? `Stopped ${result.closed_count} active session${result.closed_count === 1 ? "" : "s"}.` : "No active sessions were running.");
      reload();
    } catch (requestError) { notify("error", (requestError as Error).message); }
  };
  const runRegistryAction = async (kind: RegistryAction, explicitTargets?: Account[]) => {
    closeOperationConfigurations();
    try {
      const targets = explicitTargets ?? await resolveActionAccounts();
      if (!targets.length) { notify("info", actionScope === "group" ? "Choose a group with accounts first." : "Select at least one account first."); return; }
      if (kind === "login") {
        setBatchAccountIds(targets.map((account) => account.id));
        setBatchOpen(true);
        return;
      }
      if (kind === "credentials") {
        if (targets.length !== 1) { notify("info", "Credential revisions are account-specific. Select exactly one account."); return; }
        setCredentialChoiceAccount(targets[0]);
        return;
      }
      if (kind === "upload_profile") {
        if (targets.length !== 1) { notify("info", "Choose exactly one account to upload its profile. Use Settings → Backups for multiple profiles."); return; }
        setProfileImportAccount(targets[0]);
        return;
      }
      if (kind === "tags") { setTagTargets(targets); return; }
      if (kind === "contacts") {
        setContactTargets(targets);
        return;
      }
      if (kind === "labels") {
        setLabelsMarkersTargets(targets);
        return;
      }
      if (kind === "security") {
        setSecurityTargets(targets);
        return;
      }
      if (kind === "addons") {
        setAddonsTargets(targets);
        return;
      }
      if (kind === "mail") {
        setMailTargets(targets);
        return;
      }
      if (kind === "alerts") {
        setAlertsTargets(targets);
        return;
      }
      if (kind === "preferences") {
        setPreferencesTargets(targets);
        return;
      }
      if (["edit", "move", "delete"].includes(kind)) { setActiveAdminAction({ kind: kind as "edit" | "move" | "delete", accounts: targets }); return; }
      setActiveOperation({ kind: kind as OperationKind, accounts: targets });
    } catch (requestError) { notify("error", (requestError as Error).message); }
  };
  const resumeCredentialPlan = async (plan: CredentialPlan): Promise<boolean> => {
    setActiveAccount(null);
    try {
      let current = plan;
      if (plan.status === "DRAFT") {
        current = await api<CredentialPlan>(`/api/v1/credential-change-plans/${plan.id}/start`, { method: "POST" });
      } else if (plan.status === "READY_FOR_OPERATOR") {
        const linked = plan.session_id ? await api<Session>(`/api/v1/sessions/${plan.session_id}`).catch(() => null) : null;
        if (!(linked && isOpenSessionState(linked.state))) {
          current = await api<CredentialPlan>(`/api/v1/credential-change-plans/${plan.id}/start`, { method: "POST" });
        }
      }
      setCredentialPlan(current);
      const session = current.session_id ? await api<Session>(`/api/v1/sessions/${current.session_id}`).catch(() => null) : null;
      if (session && isOpenSessionState(session.state)) {
        setActiveSession(session);
      } else setCredentialPlanConfirmOpen(true);
      return true;
    } catch (requestError) {
      const latest = await api<CredentialPlan>(`/api/v1/credential-change-plans/${plan.id}`).catch(() => plan);
      const live = latest.session_id ? await api<Session>(`/api/v1/sessions/${latest.session_id}`).catch(() => null) : null;
      if (live && isOpenSessionState(live.state)) { setCredentialPlan(latest); setActiveSession(live); return true; }
      if (["DRAFT", "READY_FOR_OPERATOR"].includes(latest.status)) { setCredentialPlan(latest); setCredentialPlanConfirmOpen(true); }
      notify("error", (requestError as Error).message);
      return false;
    }
  };
  const cancelCredentialPlan = async (plan: CredentialPlan): Promise<void> => {
    setActiveAccount(null);
    try {
      await api<CredentialPlan>(`/api/v1/credential-change-plans/${plan.id}/cancel`, { method: "POST" });
      setCredentialPlanConfirmOpen(false);
      setCredentialPlan(null);
      setActiveSession(null);
      notify("success", `Credential plan v${plan.version} for ${plan.account_email} was cancelled. No credential material was changed or stored.`);
      reload();
    } catch (requestError) { notify("error", (requestError as Error).message); }
  };
  const signOut = async () => { try { await api<void>("/api/v1/auth/logout", { method: "POST" }); } finally { onSignOut(); } };

  useEffect(() => { document.documentElement.dataset.theme = appTheme; window.localStorage.setItem("northstar-app-theme", appTheme); }, [appTheme]);
  useEffect(() => { window.localStorage.setItem("northstar-active-view", activeView); }, [activeView]);
  useEffect(() => { if (!auth.permissions.is_admin && (activeView === "employees" || activeView === "settings")) setActiveView("overview"); else if (!auth.permissions.can_operate_accounts && (activeView === "desktop" || activeView === "proxies" || activeView === "vps" || activeView === "godaddy")) setActiveView("overview"); }, [activeView, auth.permissions.is_admin, auth.permissions.can_operate_accounts]);
  useEffect(() => { if (activeView === "desktop") setSidebarCollapsed(true); }, [activeView]);
  useEffect(() => { window.localStorage.setItem("northstar-sidebar-collapsed", String(sidebarCollapsed)); }, [sidebarCollapsed]);
  useEffect(() => {
    if (!auth.permissions.can_operate_accounts) { setHeaderOvhLinks([]); return; }
    let cancelled = false;
    const refresh = () => {
      if (document.visibilityState !== "visible") return;
      api<{ items: OvhProxyGroupLink[]; provider_error?: string | null }>("/api/v1/infrastructure/ovh/proxy-groups")
        .then((result) => { if (!cancelled) { setHeaderOvhLinks(result.items); setHeaderOvhError(result.provider_error || ""); } })
        .catch((requestError) => { if (!cancelled) { setHeaderOvhLinks([]); setHeaderOvhError((requestError as Error).message || "OVH status unavailable"); } });
    };
    const onVisibilityChange = () => { if (document.visibilityState === "visible") refresh(); };
    refresh();
    const timer = window.setInterval(refresh, 60_000);
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => { cancelled = true; window.clearInterval(timer); document.removeEventListener("visibilitychange", onVisibilityChange); };
  }, [auth.permissions.can_operate_accounts, refreshKey]);
  const uniqueHeaderOvhLinks = [...new Map(headerOvhLinks.map((link) => [`${link.resource_type || "vps"}:${link.service_name || link.server_name}:${link.instance_id || ""}`, link])).values()];
  const ovhPowerKind = (status: string) => { const value = status.toUpperCase(); return ["RUNNING", "ACTIVE", "ENABLED", "STARTED"].includes(value) ? "on" : ["STOPPED", "OFF", "SHUTOFF", "HALTED", "SHELVED", "SUSPENDED"].includes(value) ? "off" : "unknown"; };

  const operationDialogs = <>
    {stopGroupConfirmOpen && currentGroup && <GroupStopConfirm group={currentGroup} onCancel={() => setStopGroupConfirmOpen(false)} onConfirm={confirmStopGroupProcesses} />}
    {stopAllConfirmOpen && <AllStopConfirm onCancel={() => setStopAllConfirmOpen(false)} onConfirm={confirmStopAllProcesses} />}
    {credentialChoiceAccount && <CredentialChoiceModal account={credentialChoiceAccount} onClose={() => setCredentialChoiceAccount(null)} onPlan={async (fields, recoveryEmails) => { const email = credentialChoiceAccount.email; setCredentialChoiceAccount(null); try { const created = await api<CredentialPlan>(`/api/v1/accounts/${credentialChoiceAccount.id}/credential-change-plans`, { method: "POST", body: JSON.stringify({ password: fields.includes("password"), recovery: fields.includes("recovery"), "2fa": fields.includes("two_fa"), recovery_emails: recoveryEmails }) }); const ok = await resumeCredentialPlan(created); reload(); if (ok) { const summary = [fields.includes("password") ? "password" : null, fields.includes("recovery") ? "recovery email" : null, fields.includes("two_fa") ? "2FA" : null].filter(Boolean).join(" + "); notify("success", `Credential plan v${created.version} prepared for ${email}. The ${summary} change runs automatically in the saved Google session — confirm here once it finishes.`); } } catch (requestError) { notify("error", (requestError as Error).message); } }} />}
    {credentialAccount && credentialField && <CredentialEditor account={credentialAccount} field={credentialField} onClose={() => { setCredentialAccount(null); setCredentialField(null); }} onSaved={() => { setCredentialAccount(null); setCredentialField(null); notify("success", "Credential updated and the account now requires a fresh login."); reload(); }} onError={(message) => notify("error", message)} />}
    {activeSession && activeView === "desktop" && <SessionWorkspace session={activeSession} credentialPlan={credentialPlan && credentialPlan.session_id === activeSession.id ? credentialPlan : null} onUpdate={(session) => { setActiveSession(session); reload(); }} onConfirmPlan={() => setCredentialPlanConfirmOpen(true)} onClose={() => { setActiveSession(null); reload(); }} onError={(message) => notify("error", message)} />}
    {credentialPlan && credentialPlanConfirmOpen && <CredentialPlanConfirmModal plan={credentialPlan} accountEmail={credentialPlan.account_email} onClose={() => setCredentialPlanConfirmOpen(false)} onConfirmed={(provisioned) => { setCredentialPlanConfirmOpen(false); setCredentialPlan(null); setActiveSession(null); notify("success", provisioned ? `New credentials stored encrypted for ${credentialPlan.account_email}. A one-time authenticator secret was generated — add it to your authenticator app now.` : `New credentials stored encrypted for ${credentialPlan.account_email}. The account now requires a fresh login.`); reload(); }} onError={(message) => notify("error", message)} />}
    {isBatchOpen && <LoginBatchModal groups={groups} initialGroup={selectedGroup !== "all" ? selectedGroup : groups[0]?.id ?? ""} accountIds={batchAccountIds} onClose={() => { setBatchOpen(false); setBatchAccountIds([]); }} onStarted={(job) => { setBatchOpen(false); setBatchAccountIds([]); setActiveJob(job); notify("success", `${job.running_count} profile sessions opened`); reload(); }} onError={(message) => notify("error", message)} />}
    {activeOperation && <BulkOperationDesigner accounts={activeOperation.accounts} kind={activeOperation.kind} onClose={() => setActiveOperation(null)} onSaved={(message) => { setActiveOperation(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {contactTargets && <ContactsImportModal accounts={contactTargets} onClose={() => setContactTargets(null)} onStarted={(session) => setActiveSession(session)} onPrepared={(message) => { setContactTargets(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {labelsMarkersTargets && <LabelsMarkersBatchModal accounts={labelsMarkersTargets} onClose={() => setLabelsMarkersTargets(null)} onFinished={(message) => { notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {securityTargets && <SecurityBatchModal accounts={securityTargets} onClose={() => setSecurityTargets(null)} onFinished={(message) => { notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {addonsTargets && <AddonsBatchModal accounts={addonsTargets} onClose={() => setAddonsTargets(null)} onFinished={(message) => { notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {alertsTargets && <AlertsBatchModal accounts={alertsTargets} onClose={() => setAlertsTargets(null)} onFinished={(message) => { setAlertsTargets(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {mailTargets && <MailWorkflowModal accounts={mailTargets} onClose={() => setMailTargets(null)} onFinished={(message) => { setMailTargets(null); notify("success", message); reload(); }} onInterrupted={(message) => { setMailTargets(null); notify("info", message); reload(); }} onError={(message) => notify("error", message)} />}
    {preferencesTargets && <AccountPreferencesBatchModal accounts={preferencesTargets} onClose={() => setPreferencesTargets(null)} onStarted={(session, count) => { setPreferencesTargets(null); setActiveSession(session); notify("success", `Account preferences started for ${count} ${count === 1 ? "account" : "accounts"}.`); reload(); }} onError={(message) => notify("error", message)} />}
    {activeAdminAction && <BulkAdminDesigner accounts={activeAdminAction.accounts} groups={groups} kind={activeAdminAction.kind} onClose={() => setActiveAdminAction(null)} onSaved={(message) => { setActiveAdminAction(null); notify("success", message); reload(); }} onPartial={(message) => { setActiveAdminAction(null); notify("error", message); reload(); }} />}
    {tagTargets && <AccountTagsModal accounts={tagTargets} onClose={() => setTagTargets(null)} onSaved={(message) => { setTagTargets(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
    {profileImportAccount && <AccountProfileImportModal account={{ id: profileImportAccount.id, email: profileImportAccount.email }} onClose={() => setProfileImportAccount(null)} onImported={() => { notify("success", `Saved browser profile for ${profileImportAccount.email}.`); reload(); }} />}
    {profileCompactionOpen && <ProfileCompactionModal selected={profileCompactionSelection} onClose={() => setProfileCompactionOpen(false)} />}
  </>;
  const presentedOperationDialogs = desktopOperationHost && activeView === "desktop"
    ? createPortal(<DesktopInspectorContext.Provider value><div className="desktop-inspector-operation-layer">{operationDialogs}</div></DesktopInspectorContext.Provider>, desktopOperationHost)
    : operationDialogs;

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""} ${sidebarCollapsed ? "sidebar-collapsed" : ""}`} aria-label="Primary navigation">
        <div className="brand-lockup"><div className="brand-mark"><Layers3 size={18} /></div><div className="brand-copy"><strong>Northstar</strong><span>Gmail operations</span></div><button type="button" className="sidebar-collapse-toggle" aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"} title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"} onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}>{sidebarCollapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}</button></div>
        <nav className="nav-groups">
          <NavLabel>Workspace</NavLabel>
          <NavItem active={activeView === "overview"} icon={<Activity size={17} />} label="Overview" onClick={() => { setActiveView("overview"); setMobileNav(false); }} />
          <NavItem active={activeView === "accounts"} icon={<Database size={17} />} label="Accounts" badge={total || undefined} onClick={() => { setActiveView("accounts"); setMobileNav(false); }} />
          <NavItem active={activeView === "groups"} icon={<UsersRound size={17} />} label="Account groups" onClick={() => { setActiveView("groups"); setMobileNav(false); }} />
          {auth.permissions.is_admin && <NavItem active={activeView === "employees"} icon={<UserRound size={17} />} label="Employees" onClick={() => { setActiveView("employees"); setMobileNav(false); }} />}
          <NavLabel>Operations</NavLabel>
          <NavItem active={activeView === "issues"} icon={<ShieldCheck size={17} />} label="Issues" badge={attentionAccounts.length} onClick={() => { setActiveView("issues"); setMobileNav(false); }} />
          <NavItem active={activeView === "sessions"} icon={<Clock3 size={17} />} label="Sessions" onClick={() => { setActiveView("sessions"); setMobileNav(false); }} />
          {auth.permissions.can_operate_accounts && <NavItem active={activeView === "desktop"} icon={<Monitor size={17} />} label="Browser desktop" onClick={() => { setActiveView("desktop"); setMobileNav(false); }} />}
          <NavItem active={activeView === "newsletters"} icon={<Mail size={17} />} label="Newsletters" onClick={() => { setActiveView("newsletters"); setMobileNav(false); }} />
          <NavLabel>Infrastructure</NavLabel>
          {auth.permissions.can_operate_accounts && <NavItem active={activeView === "proxies"} icon={<Network size={17} />} label="Proxies" onClick={() => { setActiveView("proxies"); setMobileNav(false); }} />}
          {auth.permissions.can_operate_accounts && <NavItem active={activeView === "vps"} icon={<Server size={17} />} label="VPS providers" onClick={() => { setActiveView("vps"); setMobileNav(false); }} />}
          {auth.permissions.can_operate_accounts && <NavItem active={activeView === "godaddy"} icon={<Globe2 size={17} />} label="GoDaddy DNS" onClick={() => { setActiveView("godaddy"); setMobileNav(false); }} />}
          <NavItem active={activeView === "credentials"} icon={<KeyRound size={17} />} label="Credentials" onClick={() => { setActiveView("credentials"); setMobileNav(false); }} />
          <NavItem active={activeView === "profiles"} icon={<Layers3 size={17} />} label="Browser profiles" onClick={() => { setActiveView("profiles"); setMobileNav(false); }} />
          {auth.permissions.is_admin && <NavItem active={activeView === "settings"} icon={<SlidersHorizontal size={17} />} label="Settings" onClick={() => { setActiveView("settings"); setMobileNav(false); }} />}
        </nav>
        <div className="sidebar-footer"><div className="security-note"><LockKeyhole size={15} /><span>Credentials encrypted at rest</span></div><div className="user-card"><div className="user-avatar">{auth.employee.full_name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase()}</div><div><strong>{auth.employee.full_name}</strong><span>{labelStatus(auth.employee.role)}</span></div><button type="button" className="icon-button" aria-label="Sign out" onClick={() => void signOut()}><LogOut size={15} /></button></div></div>
      </aside>
      {mobileNav && <button type="button" className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}

      <main className="main-content">
        <header className="topbar"><button type="button" className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div className="breadcrumbs"><span>Workspace</span><ArrowRight size={14} /><strong>{activeView === "accounts" ? "Accounts" : activeView === "profiles" ? "Browser profiles" : activeView === "groups" ? "Account groups" : activeView === "newsletters" ? "Newsletters" : labelStatus(activeView)}</strong></div><div className="topbar-actions">{auth.permissions.can_operate_accounts && <div className="ovh-header-statuses" aria-label="OVH instance power status">{uniqueHeaderOvhLinks.slice(0, 2).map((link) => { const power = ovhPowerKind(link.status); return <div className={`ovh-header-server ${power}`} key={link.id} title={`${link.server_name} · ${link.proxy_group} · ${labelStatus(link.status)}`}><span className="ovh-header-light" aria-hidden="true" /><Server size={13} /><span className="ovh-header-server-name">{link.server_name}</span><strong>{power === "on" ? "On" : power === "off" ? "Off" : "?"}</strong></div>; })}{uniqueHeaderOvhLinks.length > 2 && <span className="ovh-header-more" title={uniqueHeaderOvhLinks.slice(2).map((link) => `${link.server_name}: ${labelStatus(link.status)}`).join(" · ")}>+{uniqueHeaderOvhLinks.length - 2}</span>}{!uniqueHeaderOvhLinks.length && <div className={`ovh-header-server ${headerOvhError ? "unknown" : "empty"}`} title={headerOvhError || "No OVH instance is linked to a proxy group"}><span className="ovh-header-light" aria-hidden="true" /><Server size={13} /><span className="ovh-header-server-name">OVH</span><strong>{headerOvhError ? "Unavailable" : "Not linked"}</strong></div>}</div>}<button type="button" className="icon-button theme-toggle" aria-label={`Switch to ${appTheme === "light" ? "dark" : "light"} theme`} title={`Switch to ${appTheme === "light" ? "dark" : "light"} theme`} onClick={() => setAppTheme(appTheme === "light" ? "dark" : "light")}>{appTheme === "light" ? <Moon size={17} /> : <Sun size={17} />}</button><button type="button" className="button secondary small" onClick={reload} title="Reload saved accounts, groups, and registry data from the database"><RefreshCw size={14} /> Sync data</button><div className="connection-state"><span className="pulse-dot" /> {auth.permissions.is_admin ? "Administrator" : "Scoped access"}</div><div className="avatar-button" role="img" aria-label={`Signed in as ${auth.employee.full_name}`}>{auth.employee.full_name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase()}</div></div></header>

        <div className="page-content">
          {activeView === "overview" && <OverviewPage groups={groups} refreshKey={refreshKey} onRefresh={reload} onNavigate={setActiveView} />}
          {activeView === "groups" && <GroupsPage groups={groups} refreshKey={refreshKey} onRefresh={reload} onOpenGroup={(groupId) => { setSelectedGroup(groupId); setActiveView("accounts"); }} canManage={auth.permissions.is_admin} onNotify={notify} />}
          {activeView === "employees" && auth.permissions.is_admin && <EmployeesPage groups={groups} refreshKey={refreshKey} onRefresh={reload} onNotify={notify} />}
          {activeView === "issues" && <IssuesPage refreshKey={refreshKey} onRefresh={reload} onOpenAccount={async (accountId) => { try { setActiveAccount(await api<Account>(`/api/v1/accounts/${accountId}`)); } catch (requestError) { notify("error", (requestError as Error).message); } }} />}
        {auth.permissions.can_operate_accounts && <div className="persistent-browser-desktop" hidden={activeView !== "desktop"} aria-hidden={activeView !== "desktop"}><BrowserDesktopPage isAdmin={auth.permissions.is_admin} isActive={activeView === "desktop"} groups={groups} refreshKey={refreshKey} onRefresh={reload} onOpenSession={(session) => setActiveSession(session)} onRunOperation={(kind, targets) => runRegistryAction(kind, targets)} onCompactProfiles={(targets) => { setProfileCompactionSelection(targets.map(({ id, email }) => ({ id, email }))); setProfileCompactionOpen(true); }} onOperationHost={setDesktopOperationHost} onConfirmStopAll={confirmStopAllProcesses} navHidden={sidebarCollapsed} onToggleNavigation={() => setSidebarCollapsed((collapsed) => !collapsed)} /></div>}
          {activeView === "settings" && auth.permissions.is_admin && <SettingsPage onNotify={notify} />}
          {activeView === "vps" && auth.permissions.can_operate_accounts && <OvhPage canManageSettings={auth.permissions.is_admin} onOpenSettings={() => setActiveView("settings")} onNotify={notify} />}
          {activeView === "godaddy" && auth.permissions.can_operate_accounts && <GoDaddyPage onNotify={notify} />}
          {activeView === "newsletters" && <NewsletterPage accounts={accounts.map((account) => ({ id: account.id, email: account.email, group_id: account.group_id, group_name: account.group_name, profile_status: account.profile.status }))} accountGroups={groups.map((group) => ({ id: group.id, name: group.name, account_count: group.account_count }))} canManage={auth.permissions.is_admin} canOperate={auth.permissions.can_operate_accounts} refreshKey={refreshKey} onNotify={notify} onOpenSettings={() => setActiveView("settings")} initialAccountIds={newsletterLaunchAccountIds} onClearInitialAccountIds={() => setNewsletterLaunchAccountIds([])} />}
          {isInfrastructureView(activeView) && <InfrastructurePage view={activeView} groups={groups} refreshKey={refreshKey} onRefresh={reload} canDeleteHistory={auth.permissions.is_admin} onDeleteHistory={async () => { const result = await api<{ deleted_count: number; active_preserved_count: number }>("/api/v1/sessions/history", { method: "DELETE" }); notify("success", `Deleted ${result.deleted_count} session record${result.deleted_count === 1 ? "" : "s"}; preserved ${result.active_preserved_count} active session${result.active_preserved_count === 1 ? "" : "s"}.`); reload(); }} onOpenAccount={async (accountId) => { try { setActiveAccount(await api<Account>(`/api/v1/accounts/${accountId}`)); } catch (requestError) { notify("error", (requestError as Error).message); } }} onOpenSession={(session) => setActiveSession(session)} onEditCredentials={(account, field) => { setCredentialAccount(account); setCredentialField(field); }} onPreparePlan={(account) => setCredentialChoiceAccount(account)} onNotify={notify} />}
          {activeView === "accounts" && <div className="accounts-workspace">
          <section className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE / ACCOUNTS</div><h1>Accounts <span className="heading-count">{total.toLocaleString()}</span></h1><p>Review account access, status, groups, and assigned infrastructure.</p></div><div className="heading-actions">{auth.permissions.can_operate_accounts && <button type="button" className="button secondary" onClick={() => { setProfileCompactionSelection(selectedAccountIds.map((id) => ({ id, email: accounts.find((account) => account.id === id)?.email ?? id }))); setProfileCompactionOpen(true); }}><Archive size={17} /> Compact profiles</button>}<button type="button" className="button secondary" onClick={() => setContactLibraryOpen(true)}><AddressBook size={17} /> Contact files</button><button type="button" className="button secondary" onClick={() => setOnboardingOpen(true)}><Plus size={17} /> Add account</button><button type="button" className="button primary" onClick={() => setOnboardingOpen(true)}><ClipboardCheck size={17} /> Import accounts</button></div></section>
          <section className="handoff-banner" aria-label="Phase 1 boundary"><div className="handoff-rail" /><div className="handoff-icon"><ShieldCheck size={19} /></div><div className="handoff-copy"><strong>Safe hand-off to Phase 2</strong><span>New records are encrypted, deduplicated, and ready for a profile-backed automatic login session.</span></div><span className="handoff-state">BOUNDARY LOCKED</span></section>

          <AttentionStrip accounts={attentionAccounts} onOpen={setActiveAccount} />

          <section className="summary-grid" aria-label="Account registry summary"><SummaryCard label="Accounts in view" value={visibleCount.toString()} note="Current group scope" icon={<Database size={17} />} /><SummaryCard label="Ready for login" value={readyCount.toString()} note="Stored and validated" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Authenticated" value={(statusCounts.AUTHENTICATED ?? 0).toString()} note="Verified account access" icon={<ShieldCheck size={17} />} /><SummaryCard label="Verification required" value={(statusCounts.VERIFICATION_REQUIRED ?? 0).toString()} note="Needs operator action" icon={<LockKeyhole size={17} />} tone="amber" /></section>

          <section className="registry-panel"><div className="panel-header"><div><h2>Account registry</h2><p>Choose a group to inspect its account boundary.</p></div><div className="group-context"><span>GROUP CONTEXT</span><label className="select-wrap"><select aria-label="Select account group" value={selectedGroup} onChange={(event) => changeRegistryGroup(event.target.value)}><option value="all">All groups · {groups.reduce((sum, group) => sum + group.account_count, 0)} accounts</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><ChevronDown size={15} /></label></div></div>
            <div className="toolbar registry-toolbar"><label className="search-field"><Search size={17} /><span className="sr-only">Search accounts</span><input ref={searchRef} value={query} onChange={(event) => changeRegistryQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) event.preventDefault(); }} placeholder="Search by email or group" />{query && <button type="button" className="clear-search" aria-label="Clear search" onClick={() => { changeRegistryQuery(""); searchRef.current?.focus(); }}><X size={15} /></button>}</label><div className="toolbar-right"><label className="page-size-control"><span>Per page</span><select aria-label="Accounts per page" value={pageSize} onChange={(event) => { setPageSize(event.target.value); setPage(1); }}><option value="25">25</option><option value="50">50</option><option value="100">100</option><option value="all">All</option></select></label>{auth.permissions.is_admin && <button type="button" className="button secondary small profile-export-button" onClick={() => void exportSelectedProfiles()} disabled={!selectedAccountIds.length || selectedAccountIds.length > 100 || profileExporting} title="Downloads selected Chrome profiles, including cookies and site data. Close selected browsers first. Maximum 100 per export." aria-busy={profileExporting}>{profileExporting ? <span className="button-spinner" /> : <Download size={14} />}{profileExporting ? "Preparing ZIP…" : `Download profiles${selectedAccountIds.length ? ` (${selectedAccountIds.length})` : ""}`}</button>}<button type="button" className="icon-button" aria-label="Refresh accounts" onClick={reload}><RefreshCw size={16} /></button></div><div className="registry-filter-row" role="group" aria-label="Filter accounts by status">{registryFilters.map((filter) => <button type="button" key={filter.value} className={`registry-filter-button${statusFilter === filter.value ? " selected" : ""}${filter.value === "NONEXISTENT" ? " nonexistent-filter" : ""}`} aria-pressed={statusFilter === filter.value} onClick={() => changeRegistryFilter(filter.value)}>{filter.label}<span>{statusCounts[filter.value] ?? 0}</span></button>)}</div></div>
            <RegistryActionBar accounts={accounts} selectedAccountIds={selectedAccountIds} actionScope={actionScope} group={currentGroup} onScopeChange={setActionScope} onAction={runRegistryAction} onStopGroup={stopGroupProcesses} onStopAll={stopAllProcesses} />
            {error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={reload}>Retry</button></div>}
            <div className="result-meta" role="status"><span>{loading ? "Refreshing account registry…" : `${total} ${total === 1 ? "account" : "accounts"} in this view`}</span>{query && <span className="search-context">for “{query}”</span>}</div>
            <AccountTable accounts={accounts} profileSizes={profileSizes} loading={loading} canOperate={auth.permissions.can_operate_accounts} selectedAccountIds={selectedAccountIds} onSelectionChange={setSelectedAccountIds} onOpen={setActiveAccount} onAdd={() => setOnboardingOpen(true)} onContextAction={handleAccountContextAction} />
            <div className="table-footer registry-pagination"><span>Showing {firstVisible}–{lastVisible} of {total}</span><div className="pagination-controls"><span className="pagination-note">Page {page} of {pageCount}</span><button type="button" className="button secondary small" disabled={page <= 1 || loading} onClick={() => setPage((current) => Math.max(1, current - 1))}>Previous</button><button type="button" className="button secondary small" disabled={page >= pageCount || loading} onClick={() => setPage((current) => Math.min(pageCount, current + 1))}>Next</button></div></div>
          </section>
          <footer className="page-footer"><span>Northstar Operations</span><span>Phase 1 registry · v0.1</span><a href="/docs" onClick={(event) => event.preventDefault()}>API docs <ArrowRight size={13} /></a></footer>
          </div>}
        </div>
      </main>
      {isOnboardingOpen && <OnboardingModal groups={groups} initialGroup={selectedGroup !== "all" ? selectedGroup : groups[0]?.id ?? ""} onClose={() => setOnboardingOpen(false)} onSuccess={(message) => { setOnboardingOpen(false); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
      {isContactLibraryOpen && <ContactsImportModal accounts={[]} onClose={() => setContactLibraryOpen(false)} onPrepared={(message) => notify("success", message)} onError={(message) => notify("error", message)} />}
      {activeAccount && <AccountDetail account={activeAccount} groups={groups} onClose={() => setActiveAccount(null)} onSessionOpened={setActiveSession} onUpdated={() => { setActiveAccount(null); reload(); }} onPrepared={(message) => notify("info", message)} onError={(message) => notify("error", message)} onResumePlan={(plan) => void resumeCredentialPlan(plan)} onCancelPlan={(plan) => void cancelCredentialPlan(plan)} />}
      {activeSession && activeView !== "desktop" && <SessionWorkspace session={activeSession} credentialPlan={credentialPlan && credentialPlan.session_id === activeSession.id ? credentialPlan : null} onUpdate={(session) => { setActiveSession(session); reload(); }} onConfirmPlan={() => setCredentialPlanConfirmOpen(true)} onClose={() => { setActiveSession(null); reload(); }} onError={(message) => notify("error", message)} />}
      {presentedOperationDialogs}
      {activeJob && <LoginJobWorkspace job={activeJob} onUpdate={(job) => { setActiveJob(job); reload(); }} onClose={() => { setActiveJob(null); reload(); }} onError={(message) => notify("error", message)} />}
      <ToastRegion toasts={toasts} />
    </div>
  );
}

function isInfrastructureView(view: WorkspaceView): view is InfrastructureView { return ["sessions", "proxies", "profiles", "credentials"].includes(view); }

function WorkspacePageHeader({ eyebrow, title, description, icon, action }: { eyebrow: string; title: string; description: string; icon?: React.ReactNode; action?: React.ReactNode }) {
  return <section className="page-heading workspace-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> {eyebrow}</div><h1>{title}</h1><p>{description}</p></div>{action && <div className="heading-actions">{action}</div>}</section>;
}

function OverviewPage({ groups, refreshKey, onRefresh, onNavigate }: { groups: Group[]; refreshKey: number; onRefresh: () => void; onNavigate: (view: WorkspaceView) => void }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [proxies, setProxies] = useState<ProxyRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { let cancelled = false; setLoading(true); setError(""); Promise.all([api<{ items: Account[]; total: number }>("/api/v1/accounts?page=1&page_size=100"), api<Session[]>("/api/v1/sessions?latest_per_account=true&limit=100"), api<ProxyRecord[]>("/api/v1/proxies")]).then(([accountData, sessionData, proxyData]) => { if (cancelled) return; setAccounts(accountData.items); setTotal(accountData.total); setSessions(sessionData); setProxies(proxyData); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Overview data is unavailable."); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [refreshKey]);
  const authenticated = accounts.filter((account) => account.login_status === "AUTHENTICATED").length;
  const issues = accounts.filter((account) => ["SETUP_ERROR", "ACCOUNT_NOT_FOUND", "NONEXISTENT", "NEEDS_ATTENTION", "VERIFICATION_REQUIRED"].includes(account.readiness_status) || ["ACCOUNT_NOT_FOUND", "NONEXISTENT", "NEEDS_ATTENTION", "VERIFICATION_REQUIRED"].includes(account.login_status)).length;
  const openSessions = sessions.filter((session) => isOpenSessionState(session.state)).length;
  const recent = [...sessions].sort((a, b) => new Date(b.last_event_at).getTime() - new Date(a.last_event_at).getTime()).slice(0, 5);
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="CONTROL ROOM" title="Overview" description="A live read of account readiness, session activity, and the boundaries that need operator attention." icon={<Activity size={20} />} action={<button type="button" className="button secondary" onClick={onRefresh} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh overview</button>} /><div className="overview-signal"><div className="overview-signal-mark"><Activity size={18} /></div><div><strong>{issues ? `${issues} account${issues === 1 ? "" : "s"} need attention` : "All visible accounts are within their current boundaries"}</strong><span>{loading ? "Refreshing live metadata…" : `${total} accounts across ${groups.length} groups · ${openSessions} open session${openSessions === 1 ? "" : "s"} · ${proxies.length} proxy endpoint${proxies.length === 1 ? "" : "s"}.`}</span></div><button type="button" className="text-button" onClick={() => onNavigate(issues ? "issues" : "accounts")}>{issues ? "Review issues" : "Open registry"} <ArrowRight size={14} /></button></div>{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={onRefresh}>Retry</button></div>}<section className="summary-grid" aria-label="Overview summary"><SummaryCard label="Accounts" value={total.toString()} note="Active registry records" icon={<Database size={17} />} /><SummaryCard label="Authenticated" value={authenticated.toString()} note="Profile-backed evidence" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Open sessions" value={openSessions.toString()} note="Currently in use" icon={<Clock3 size={17} />} tone="amber" /><SummaryCard label="Account groups" value={groups.length.toString()} note="Organizational boundaries" icon={<UsersRound size={17} />} /></section><div className="overview-grid"><section className="workspace-panel"><div className="panel-header"><div><h2>Recent activity</h2><p>Latest stored session evidence, not inferred health.</p></div><button type="button" className="text-button" onClick={() => onNavigate("sessions")}>Open sessions <ArrowRight size={14} /></button></div>{recent.length ? <div className="activity-list">{recent.map((session) => <button type="button" className="activity-row" key={session.id} onClick={() => onNavigate("sessions")}><span className="activity-icon"><Clock3 size={15} /></span><span><strong>{session.account_email}</strong><small>{session.action ?? labelStatus(session.purpose ?? "login")} · {session.status_detail ?? labelStatus(session.state)}</small></span><time>{formatDateTime(session.last_event_at)}</time><ArrowRight size={14} /></button>)}</div> : <div className="workspace-empty"><Clock3 size={20} /><strong>No session activity yet</strong><span>Open the registry to start a profile-backed operation.</span><button type="button" className="button secondary small" onClick={() => onNavigate("accounts")}>Open account registry</button></div>}</section><section className="workspace-panel"><div className="panel-header"><div><h2>Operating boundaries</h2><p>Where the console is ready for the next decision.</p></div></div><div className="boundary-list"><button type="button" className="boundary-row" onClick={() => onNavigate("groups")}><span className="boundary-mark"><UsersRound size={15} /></span><span><strong>Groups</strong><small>Keep account pools near the 300-account operating target.</small></span><span className="boundary-value">{groups.length}</span></button><button type="button" className="boundary-row" onClick={() => onNavigate("profiles")}><span className="boundary-mark"><Layers3 size={15} /></span><span><strong>Profiles</strong><small>Inspect only profiles that actually exist on disk.</small></span><span className="boundary-value">Open</span></button><button type="button" className="boundary-row" onClick={() => onNavigate("proxies")}><span className="boundary-mark"><KeyRound size={15} /></span><span><strong>Proxy inventory</strong><small>Review assignment and location metadata.</small></span><span className="boundary-value">{proxies.length}</span></button></div></section></div><footer className="page-footer"><span>Northstar Operations</span><span>Control room · live metadata</span></footer></div>;
}

function GroupsPage({ groups, refreshKey, onRefresh, onOpenGroup, canManage, onNotify }: { groups: Group[]; refreshKey: number; onRefresh: () => void; onOpenGroup: (groupId: string) => void; canManage: boolean; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [createOpen, setCreateOpen] = useState(false);
  const [editingGroup, setEditingGroup] = useState<Group | null>(null);
  const [deletingGroup, setDeletingGroup] = useState<Group | null>(null);
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE STRUCTURE" title="Account groups" description="Create clear operating boundaries for the account fleet and inspect capacity before dispatching work." action={<><button type="button" className="button secondary" onClick={onRefresh}><RefreshCw size={16} /> Refresh groups</button>{canManage && <button type="button" className="button primary" onClick={() => setCreateOpen(true)}><Plus size={16} /> Create group</button>}</>} /><section className="groups-intro"><div className="groups-intro-icon"><UsersRound size={19} /></div><div><strong>One group, one operating boundary</strong><span>New groups default to a 300-account capacity. Capacity is organizational guidance; login concurrency remains controlled by the batch dispatcher.</span></div><span className="handoff-state">{groups.length} ACTIVE</span></section><section className="workspace-panel"><div className="panel-header"><div><h2>Group directory</h2><p>{groups.length ? "Select a group to inspect its accounts." : "Create the first group to organize account onboarding."}</p></div><span className="inventory-count">Refreshed from the API</span></div>{groups.length ? <div className="group-card-grid">{groups.map((group) => { const utilization = Math.min(100, Math.round((group.account_count / group.capacity) * 100)); return <article className="group-card" key={group.id}><div className="group-card-top"><span className="group-card-mark"><UsersRound size={16} /></span><InventoryStatus status={group.status} /></div><h3>{group.name}</h3><p>{group.description || "No description provided."}</p><div className="capacity-line"><span>Account capacity</span><strong>{group.account_count} / {group.capacity}</strong></div><div className="capacity-meter" aria-label={`${group.account_count} of ${group.capacity} accounts`}><span style={{ width: `${utilization}%` }} /></div><div className="group-card-meta"><span>{group.ready_count} ready</span><span>{group.issue_count} issues</span>{canManage && <><button type="button" className="text-button" onClick={() => setEditingGroup(group)}><Pencil size={13} /> Edit</button><button type="button" className="text-button danger-text" onClick={() => setDeletingGroup(group)}><Trash2 size={13} /> Delete</button></>}<button type="button" className="text-button" onClick={() => onOpenGroup(group.id)}>Open accounts <ArrowRight size={14} /></button></div></article>; })}</div> : <div className="workspace-empty"><UsersRound size={20} /><strong>No groups yet</strong><span>Groups are required before an account can be onboarded.</span>{canManage && <button type="button" className="button primary small" onClick={() => setCreateOpen(true)}><Plus size={15} /> Create first group</button>}</div>}</section><footer className="page-footer"><span>Northstar Operations</span><span>Group directory · {refreshKey ? "live" : "initial"}</span></footer>{canManage && createOpen && <CreateGroupModal onClose={() => setCreateOpen(false)} onCreated={(group) => { setCreateOpen(false); onNotify("success", `${group.name} created with capacity for ${group.capacity} accounts.`); onRefresh(); }} onError={(message) => onNotify("error", message)} />}{canManage && editingGroup && <EditGroupModal group={editingGroup} onClose={() => setEditingGroup(null)} onUpdated={(group) => { setEditingGroup(null); onNotify("success", `${group.name} updated.`); onRefresh(); }} onError={(message) => onNotify("error", message)} />}{canManage && deletingGroup && <DeleteGroupModal group={deletingGroup} onClose={() => setDeletingGroup(null)} onDeleted={() => { setDeletingGroup(null); onNotify("success", `${deletingGroup.name} deleted.`); onRefresh(); }} />}</div>;
}

function CreateGroupModal({ onClose, onCreated, onError }: { onClose: () => void; onCreated: (group: Group) => void; onError: (message: string) => void }) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [capacity, setCapacity] = useState("300");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!name.trim()) { setError("Enter a group name."); return; } const parsedCapacity = Number(capacity); if (!Number.isInteger(parsedCapacity) || parsedCapacity < 1 || parsedCapacity > 5000) { setError("Capacity must be a whole number between 1 and 5,000."); return; } setPending(true); setError(""); try { onCreated(await api<Group>("/api/v1/groups", { method: "POST", body: JSON.stringify({ name: name.trim(), description: description.trim() || null, capacity: parsedCapacity }) })); } catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); setPending(false); } };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal group-modal" role="dialog" aria-modal="true" aria-labelledby="create-group-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE STRUCTURE</div><h2 id="create-group-title">Create account group</h2><p>Define the operating boundary before adding accounts.</p></div><button type="button" className="icon-button" aria-label="Close create group dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="create-group-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="field"><label htmlFor="group-name">Group name <span className="required">Required</span></label><input id="group-name" value={name} onChange={(event) => { setName(event.target.value); setError(""); }} placeholder="e.g. Customer Support" autoFocus disabled={pending} /></div><div className="field"><label htmlFor="group-description">Description</label><textarea className="resize-none workspace-textarea" id="group-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="What is this account pool used for?" disabled={pending} /></div><div className="field"><label htmlFor="group-capacity">Account capacity <span className="required">Required</span></label><input id="group-capacity" type="number" min="1" max="5000" step="1" value={capacity} onChange={(event) => setCapacity(event.target.value)} disabled={pending} /><small>Default is 300 accounts per group.</small></div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Safe organizational change</strong><span>Creating a group does not alter existing accounts, profiles, proxies, credentials, or sessions.</span></div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="create-group-form" className="button primary" disabled={pending}>{pending ? "Creating group…" : "Create group"}</button></div></section></div>;
}

function EditGroupModal({ group, onClose, onUpdated, onError }: { group: Group; onClose: () => void; onUpdated: (group: Group) => void; onError: (message: string) => void }) {
  const [name, setName] = useState(group.name);
  const [description, setDescription] = useState(group.description ?? "");
  const [capacity, setCapacity] = useState(String(group.capacity));
  const [groupStatus, setGroupStatus] = useState(group.status === "PAUSED" ? "PAUSED" : "ACTIVE");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const parsedCapacity = Number(capacity);
    if (!name.trim()) { setError("Enter a group name."); return; }
    if (!Number.isInteger(parsedCapacity) || parsedCapacity < group.account_count || parsedCapacity > 5000) { setError(`Capacity must be a whole number between ${group.account_count} and 5,000.`); return; }
    setPending(true); setError("");
    try { onUpdated(await api<Group>(`/api/v1/groups/${group.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim(), description: description.trim() || null, capacity: parsedCapacity, status: groupStatus }) })); }
    catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal group-modal" role="dialog" aria-modal="true" aria-labelledby="edit-group-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GROUP CONTROLS</div><h2 id="edit-group-title">Edit group</h2><p>Update this boundary without changing its account records.</p></div><button type="button" className="icon-button" aria-label="Close edit group dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="edit-group-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="field"><label htmlFor="edit-group-name">Group name <span className="required">Required</span></label><input id="edit-group-name" value={name} onChange={(event) => setName(event.target.value)} disabled={pending} /></div><div className="field"><label htmlFor="edit-group-description">Description</label><textarea className="resize-none workspace-textarea" id="edit-group-description" value={description} onChange={(event) => setDescription(event.target.value)} disabled={pending} /></div><div className="form-grid"><div className="field"><label htmlFor="edit-group-capacity">Account capacity</label><input id="edit-group-capacity" type="number" min={group.account_count} max="5000" step="1" value={capacity} onChange={(event) => setCapacity(event.target.value)} disabled={pending} /><small>At least {group.account_count} for the accounts already in this group.</small></div><div className="field"><label htmlFor="edit-group-status">Group state</label><select id="edit-group-status" value={groupStatus} onChange={(event) => setGroupStatus(event.target.value)} disabled={pending}><option value="ACTIVE">Active</option><option value="PAUSED">Paused</option></select></div></div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Safe structural change</strong><span>This edits group metadata only. Existing account, proxy, profile, credential, and session records remain intact.</span></div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="edit-group-form" className="button primary" disabled={pending}>{pending ? "Saving group…" : "Save group"}</button></div></section></div>;
}

function DeleteGroupModal({ group, onClose, onDeleted }: { group: Group; onClose: () => void; onDeleted: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const cancelRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    cancelRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !pending) { event.preventDefault(); onClose(); }
      if (event.key === "Tab" && modalRef.current) {
        const controls = Array.from(modalRef.current.querySelectorAll<HTMLButtonElement>("button:not([disabled])"));
        if (!controls.length) return;
        if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previous?.focus(); };
  }, [onClose, pending]);
  const remove = async () => {
    setPending(true); setError("");
    try { await api<void>(`/api/v1/groups/${encodeURIComponent(group.id)}`, { method: "DELETE" }); onDeleted(); }
    catch (cause) { setError((cause as Error).message || "The group could not be deleted."); setPending(false); }
  };
  return createPortal(<div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section ref={modalRef} className="modal group-modal" role="dialog" aria-modal="true" aria-labelledby="delete-group-title"><div className="modal-header"><div><span className="eyebrow">GROUP CONTROLS</span><h2 id="delete-group-title">Delete account group</h2><p>Remove an unused group from the workspace directory.</p></div><button type="button" className="icon-button" aria-label="Close delete group dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><div className="modal-body"><div className="group-delete-target"><UsersRound size={18} /><span><strong>{group.name}</strong><small>{group.account_count} active account{group.account_count === 1 ? "" : "s"}</small></span></div><p className="group-delete-guidance">A group can be deleted only when it has no accounts, including archived accounts, and no login or newsletter jobs. Move its active accounts to another group first. Employee access to this group will be removed.</p>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}</div><div className="modal-footer"><button ref={cancelRef} type="button" className="button secondary" onClick={onClose} disabled={pending}>Keep group</button><button type="button" className="button danger" onClick={() => void remove()} disabled={pending || group.account_count > 0}>{pending ? "Deleting group…" : "Delete group"}</button></div></section></div>, document.body);
}

function LegacyEmployeesPage() {
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE ACCESS" title="Employees" description="Keep the people-and-permission boundary visible while account ownership workflows are being connected." action={<button type="button" className="button secondary" disabled><UserRound size={16} /> Directory connection</button>} /><section className="workspace-panel employees-panel"><div className="panel-header"><div><h2>Access model</h2><p>These roles describe the hand-off model without pretending that identity permissions are configured.</p></div><InventoryStatus status="NOT_CONFIGURED" /></div><div className="role-grid"><div className="role-card"><span className="role-mark"><ShieldCheck size={17} /></span><strong>Administrator</strong><p>Owns workspace structure, security policy, and recovery decisions.</p><span>Full workspace scope</span></div><div className="role-card"><span className="role-mark"><Database size={17} /></span><strong>Account operator</strong><p>Works inside assigned groups and reviews account-level outcomes.</p><span>Group-scoped operations</span></div><div className="role-card"><span className="role-mark"><ClipboardCheck size={17} /></span><strong>Auditor</strong><p>Reads sessions, issues, and audit evidence without changing records.</p><span>Read-only evidence</span></div></div><div className="workspace-empty compact"><UserRound size={20} /><strong>No employee records connected</strong><span>Connect the organization identity directory before inviting people or assigning group permissions.</span></div></section><footer className="page-footer"><span>Northstar Operations</span><span>Access model · policy boundary</span></footer></div>;
}

function IssuesPage({ refreshKey, onRefresh, onOpenAccount }: { refreshKey: number; onRefresh: () => void; onOpenAccount: (accountId: string) => void }) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { let cancelled = false; setLoading(true); api<{ items: Account[] }>("/api/v1/accounts?page=1&page_size=100").then((data) => { if (!cancelled) setAccounts(data.items); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Issues are unavailable."); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [refreshKey]);
  const issues = accounts.filter((account) => account.readiness_status === "SETUP_ERROR" || ["ACCOUNT_NOT_FOUND", "NONEXISTENT", "NEEDS_ATTENTION", "VERIFICATION_REQUIRED"].includes(account.login_status) || ["ERROR", "UNREACHABLE", "OFFLINE"].includes(account.proxy.status) || ["ERROR", "CORRUPT"].includes(account.profile.status));
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="EXCEPTIONS" title="Issues" description="A focused queue of account conditions that need an operator decision or a safe retry." action={<button type="button" className="button secondary" onClick={onRefresh} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh issues</button>} /><section className="issues-summary"><div><span className="detail-label">Open conditions</span><strong>{issues.length}</strong><small>Derived from current account, proxy, and profile status.</small></div><div><span className="detail-label">Scope</span><strong>Active records</strong><small>Archived accounts are excluded by the API.</small></div></section><section className="workspace-panel"><div className="panel-header"><div><h2>Attention queue</h2><p>Each item links back to the existing account detail surface.</p></div><span className="inventory-count">{loading ? "Refreshing…" : `${issues.length} open`}</span></div>{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={onRefresh}>Retry</button></div>}{!loading && !issues.length ? <div className="workspace-empty"><Check size={20} /><strong>No open issues</strong><span>The current account snapshot contains no known attention states.</span></div> : <div className="issue-list">{issues.map((account) => <button type="button" className="issue-row" key={account.id} onClick={() => onOpenAccount(account.id)}><span className="issue-mark"><XCircle size={16} /></span><span><strong>{account.email}</strong><small>{account.group_name} · {account.login_status !== "NOT_STARTED" ? labelStatus(account.login_status) : labelStatus(account.readiness_status)}</small></span><span className="issue-reason">{account.profile.status === "CORRUPT" ? "Profile needs review" : account.proxy.status === "ERROR" ? "Proxy error" : labelStatus(account.login_status)}</span><ArrowRight size={15} /></button>)}</div>}</section><footer className="page-footer"><span>Northstar Operations</span><span>Issue queue · evidence-based</span></footer></div>;
}

function LegacySettingsReferencePage() {
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE CONFIGURATION" title="Settings" description="Review the runtime boundaries and product conventions that keep the operations console predictable." action={<span className="settings-state"><span className="pulse-dot" /> Development workspace</span>} /><div className="settings-grid"><section className="workspace-panel"><div className="panel-header"><div><h2>Workspace identity</h2><p>Visible context used throughout the console.</p></div></div><div className="settings-list"><div className="settings-row"><span><strong>Workspace</strong><small>Organization scope for the current admin console.</small></span><code>Northstar Operations</code></div><div className="settings-row"><span><strong>Default group capacity</strong><small>Guidance for new account groups.</small></span><code>300 accounts</code></div><div className="settings-row"><span><strong>Data boundary</strong><small>Secrets are never rendered in registry surfaces.</small></span><InventoryStatus status="ENCRYPTED" /></div></div></section><section className="workspace-panel"><div className="panel-header"><div><h2>Product conventions</h2><p>Stable rules shared by every active route.</p></div></div><div className="settings-list"><div className="settings-row"><span><strong>Account actions</strong><small>Actions stay beside the registry they affect.</small></span><InventoryStatus status="ACTIVE" /></div><div className="settings-row"><span><strong>Browser boundary</strong><small>Profile-backed work is separate from the SaaS console.</small></span><InventoryStatus status="ISOLATED" /></div><div className="settings-row"><span><strong>Audit trail</strong><small>Mutations record a server-side event before success is shown.</small></span><InventoryStatus status="ENABLED" /></div></div></section></div><div className="security-callout settings-note"><SlidersHorizontal size={17} /><div><strong>Runtime-managed configuration</strong><span>Environment, API authentication, worker concurrency, and browser launch policy are intentionally managed outside this page so the console cannot silently change operational safety settings.</span></div></div><footer className="page-footer"><span>Northstar Operations</span><span>Configuration reference · read-only</span></footer></div>;
}

function EmployeesDirectoryLegacy({ refreshKey, onRefresh, onNotify }: { refreshKey: number; onRefresh: () => void; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [deactivating, setDeactivating] = useState<Employee | null>(null);
  const [accessEmployee, setAccessEmployee] = useState<Employee | null>(null);
  const debouncedQuery = useDebouncedValue(query);
  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim());
    if (status !== "ALL") params.set("status", status);
    setLoading(true); setError("");
    api<Employee[]>(`/api/v1/employees${params.toString() ? `?${params.toString()}` : ""}`).then(setEmployees).catch((requestError) => setError((requestError as Error).message || "Employees are unavailable.")).finally(() => setLoading(false));
  }, [debouncedQuery, status]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const updateStatus = async (employee: Employee, nextStatus: string) => {
    try { await api<Employee>(`/api/v1/employees/${employee.id}`, { method: "PATCH", body: JSON.stringify({ status: nextStatus }) }); onNotify("success", `${employee.full_name} is now ${labelStatus(nextStatus).toLowerCase()}.`); load(); }
    catch (requestError) { onNotify("error", (requestError as Error).message); }
  };
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE ACCESS" title="Employees" description="Create and govern the people who can operate this workspace. Changes are organization-scoped and audited." action={<><button type="button" className="button secondary" onClick={() => { onRefresh(); load(); }} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh employees</button><button type="button" className="button primary" onClick={() => setCreateOpen(true)}><Plus size={16} /> Create employee</button></>} /><section className="groups-intro"><div className="groups-intro-icon"><UserRound size={19} /></div><div><strong>Access is explicit</strong><span>Invite operators, change their role, suspend access temporarily, or deactivate a record while preserving its audit history.</span></div><span className="handoff-state">{employees.filter((employee) => employee.status === "ACTIVE").length} ACTIVE</span></section><section className="workspace-panel employees-panel"><div className="panel-header"><div><h2>Employee directory</h2><p>Deactivated employees remain visible when you choose All statuses.</p></div><span className="inventory-count">{loading ? "Refreshing…" : `${employees.length} records`}</span></div><div className="toolbar infra-toolbar"><label className="search-field"><Search size={17} /><span className="sr-only">Search employees</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, email, or role" />{query && <button type="button" className="clear-search" aria-label="Clear employee search" onClick={() => setQuery("")}><X size={15} /></button>}</label><div className="toolbar-right"><label className="filter-select"><Filter size={15} /><span className="sr-only">Filter employee status</span><select aria-label="Filter employee status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="ALL">All statuses</option><option value="ACTIVE">Active</option><option value="INVITED">Invited</option><option value="SUSPENDED">Suspended</option><option value="DEACTIVATED">Deactivated</option></select></label><button type="button" className="icon-button" aria-label="Refresh employees" onClick={load}><RefreshCw size={16} /></button></div></div>{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={load}>Retry</button></div>}{loading ? <div className="table-state loading-state"><span className="spinner" /><strong>Loading employee directory</strong></div> : !employees.length ? <div className="workspace-empty compact"><UserRound size={20} /><strong>No employees match this view</strong><span>Create an employee or adjust the search and status filter.</span><button type="button" className="button primary small" onClick={() => setCreateOpen(true)}><Plus size={15} /> Create employee</button></div> : <div className="table-scroll"><table className="employee-table"><caption className="sr-only">Employee directory</caption><thead><tr><th scope="col">Employee</th><th scope="col">Role</th><th scope="col">Status</th><th scope="col">Updated</th><th scope="col">Actions</th></tr></thead><tbody>{employees.map((employee) => <tr key={employee.id}><td><div className="employee-cell"><span className="account-avatar">{employee.full_name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase()}</span><span><strong>{employee.full_name}</strong><small>{employee.email}</small></span></div></td><td><span className="role-chip">{labelStatus(employee.role)}</span></td><td><InventoryStatus status={employee.status} /></td><td><span className="metadata-cell">{formatDateTime(employee.updated_at)}</span></td><td><div className="employee-actions"><button type="button" className="text-button" onClick={() => setEditing(employee)}><Pencil size={13} /> Edit</button>{employee.status === "SUSPENDED" ? <button type="button" className="text-button" onClick={() => void updateStatus(employee, "ACTIVE")}><Power size={13} /> Reactivate</button> : employee.status === "ACTIVE" || employee.status === "INVITED" ? <button type="button" className="text-button" onClick={() => void updateStatus(employee, "SUSPENDED")}><Power size={13} /> Suspend</button> : null}{employee.status !== "DEACTIVATED" && <button type="button" className="text-button danger-text" onClick={() => setDeactivating(employee)}><Trash2 size={13} /> Deactivate</button>}</div></td></tr>)}</tbody></table></div>}</section><footer className="page-footer"><span>Northstar Operations</span><span>Employee directory · audited</span></footer>{createOpen && <EmployeeModal mode="create" onClose={() => setCreateOpen(false)} onSaved={(employee) => { setCreateOpen(false); onNotify("success", `${employee.full_name} was invited as ${labelStatus(employee.role)}.`); load(); }} onError={(message) => onNotify("error", message)} />}{editing && <EmployeeModal mode="edit" employee={editing} onClose={() => setEditing(null)} onSaved={(employee) => { setEditing(null); onNotify("success", `${employee.full_name} updated.`); load(); }} onError={(message) => onNotify("error", message)} />}{deactivating && <DeactivateEmployeeModal employee={deactivating} onClose={() => setDeactivating(null)} onDone={(employee) => { setDeactivating(null); onNotify("success", `${employee.full_name} was deactivated.`); load(); }} onError={(message) => onNotify("error", message)} />}</div>;
}

function EmployeesPage({ groups, refreshKey, onRefresh, onNotify }: { groups: Group[]; refreshKey: number; onRefresh: () => void; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [employees, setEmployees] = useState<Employee[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [editing, setEditing] = useState<Employee | null>(null);
  const [accessEmployee, setAccessEmployee] = useState<Employee | null>(null);
  const debouncedQuery = useDebouncedValue(query);
  const load = useCallback(() => {
    const params = new URLSearchParams();
    if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim());
    if (status !== "ALL") params.set("status", status);
    setLoading(true); setError("");
    api<Employee[]>(`/api/v1/employees${params.toString() ? `?${params.toString()}` : ""}`).then(setEmployees).catch((requestError) => setError((requestError as Error).message || "Employees are unavailable.")).finally(() => setLoading(false));
  }, [debouncedQuery, status]);
  useEffect(() => { load(); }, [load, refreshKey]);
  const updateStatus = async (employee: Employee, nextStatus: string) => {
    try { const saved = await api<Employee>(`/api/v1/employees/${employee.id}`, { method: "PATCH", body: JSON.stringify({ status: nextStatus }) }); setEmployees((current) => current.map((item) => item.id === saved.id ? saved : item)); onNotify("success", `${employee.full_name} is now ${labelStatus(nextStatus).toLowerCase()}.`); }
    catch (requestError) { onNotify("error", (requestError as Error).message); }
  };
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE ACCESS" title="Employees" description="Create and govern the people who can operate this workspace. Changes are organization-scoped and audited." action={<><button type="button" className="button secondary" onClick={() => { onRefresh(); load(); }} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh employees</button><button type="button" className="button primary" onClick={() => setCreateOpen(true)}><Plus size={16} /> Create employee</button></>} /><section className="groups-intro"><div className="groups-intro-icon"><UsersRound size={19} /></div><div><strong>Access is explicit</strong><span>Assign each employee to only the groups they need, with a clear level for viewing, operating, or managing that group.</span></div><span className="handoff-state">{employees.filter((employee) => employee.status === "ACTIVE").length} ACTIVE</span></section><section className="workspace-panel employees-panel"><div className="panel-header"><div><h2>Employee directory</h2><p>Group access is separate from the employee role and every change is recorded in the audit trail.</p></div><span className="inventory-count">{loading ? "Refreshing…" : `${employees.length} records`}</span></div><div className="toolbar infra-toolbar"><label className="search-field"><Search size={17} /><span className="sr-only">Search employees</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search by name, email, or role" />{query && <button type="button" className="clear-search" aria-label="Clear employee search" onClick={() => setQuery("")}><X size={15} /></button>}</label><div className="toolbar-right"><label className="filter-select"><Filter size={15} /><span className="sr-only">Filter employee status</span><select aria-label="Filter employee status" value={status} onChange={(event) => setStatus(event.target.value)}><option value="ALL">All statuses</option><option value="ACTIVE">Active</option><option value="INVITED">Invited</option><option value="SUSPENDED">Suspended</option><option value="DEACTIVATED">Deactivated</option></select></label><button type="button" className="icon-button" aria-label="Refresh employees" onClick={load}><RefreshCw size={16} /></button></div></div>{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={load}>Retry</button></div>}{loading ? <div className="table-state loading-state"><span className="spinner" /><strong>Loading employee directory</strong></div> : !employees.length ? <div className="workspace-empty compact"><UserRound size={20} /><strong>No employees match this view</strong><span>Create an employee or adjust the search and status filter.</span><button type="button" className="button primary small" onClick={() => setCreateOpen(true)}><Plus size={15} /> Create employee</button></div> : <div className="table-scroll"><table className="employee-table"><caption className="sr-only">Employee directory</caption><thead><tr><th>Employee</th><th>Role</th><th>Group access</th><th>Status</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{employees.map((employee) => { const access = employee.group_access ?? []; return <tr key={employee.id}><td><div className="employee-cell"><span className="account-avatar">{employee.full_name.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase()}</span><span><strong>{employee.full_name}</strong><small>{employee.email}</small></span></div></td><td><span className="role-chip">{labelStatus(employee.role)}</span></td><td><div className="employee-group-summary">{access.length ? access.slice(0, 2).map((item) => <span className="employee-group-chip" key={item.group_id}>{item.group_name}<small>{labelStatus(item.access_level)}</small></span>) : <span className="metadata-cell">No groups</span>}{access.length > 2 && <span className="table-subtext">+{access.length - 2} more</span>}</div></td><td><InventoryStatus status={employee.status} /></td><td><span className="metadata-cell">{formatDateTime(employee.updated_at)}</span></td><td><div className="employee-actions"><button type="button" className="text-button" onClick={() => setAccessEmployee(employee)}><UsersRound size={13} /> Manage access</button><button type="button" className="text-button" onClick={() => setEditing(employee)}><Pencil size={13} /> Edit</button>{employee.status === "SUSPENDED" ? <button type="button" className="text-button" onClick={() => void updateStatus(employee, "ACTIVE")}><Power size={13} /> Reactivate</button> : employee.status === "ACTIVE" || employee.status === "INVITED" ? <button type="button" className="text-button" onClick={() => void updateStatus(employee, "SUSPENDED")}><Power size={13} /> Suspend</button> : null}</div></td></tr>; })}</tbody></table></div>}</section><footer className="page-footer"><span>Northstar Operations</span><span>Employee directory · audited</span></footer>{createOpen && <EmployeeModal mode="create" onClose={() => setCreateOpen(false)} onSaved={(employee) => { setCreateOpen(false); onNotify("success", `${employee.full_name} was invited as ${labelStatus(employee.role)}.`); load(); }} onError={(message) => onNotify("error", message)} />}{editing && <EmployeeModal mode="edit" employee={editing} onClose={() => setEditing(null)} onSaved={(employee) => { setEditing(null); onNotify("success", `${employee.full_name} updated.`); load(); }} onError={(message) => onNotify("error", message)} />}{accessEmployee && <EmployeeGroupAccessModal employee={accessEmployee} groups={groups} onClose={() => setAccessEmployee(null)} onSaved={(employee) => { setAccessEmployee(null); setEmployees((current) => current.map((item) => item.id === employee.id ? employee : item)); onNotify("success", `${employee.full_name} group access saved and audited.`); }} onError={(message) => onNotify("error", message)} />}</div>;
}

function EmployeeGroupAccessModal({ employee, groups, onClose, onSaved, onError }: { employee: Employee; groups: Group[]; onClose: () => void; onSaved: (employee: Employee) => void; onError: (message: string) => void }) {
  const [access, setAccess] = useState<Record<string, EmployeeGroupAccess["access_level"]>>(() => Object.fromEntries((employee.group_access ?? []).map((item) => [item.group_id, item.access_level])));
  const [pending, setPending] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => { let cancelled = false; api<EmployeeGroupAccess[]>(`/api/v1/employees/${employee.id}/group-access`).then((items) => { if (!cancelled) setAccess(Object.fromEntries(items.map((item) => [item.group_id, item.access_level]))); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [employee.id]);
  const save = async () => { setPending(true); setError(""); try { onSaved(await api<Employee>(`/api/v1/employees/${employee.id}/group-access`, { method: "PUT", body: JSON.stringify({ assignments: Object.entries(access).map(([group_id, access_level]) => ({ group_id, access_level })) }) })); } catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); setPending(false); } };
  const toggle = (groupId: string, checked: boolean) => setAccess((current) => { const next = { ...current }; if (checked) next[groupId] = current[groupId] ?? "OPERATOR"; else delete next[groupId]; return next; });
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal employee-modal group-access-modal" role="dialog" aria-modal="true" aria-labelledby="group-access-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GROUP AUTHORIZATION</div><h2 id="group-access-title">Manage group access</h2><p>{employee.full_name} · {employee.email}</p></div><button type="button" className="icon-button" aria-label="Close group access dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><div className="modal-body"><div className="security-callout"><ShieldCheck size={17} /><div><strong>Group access is enforced and audited</strong><span>Choose which account groups this employee can access and at what level. A group can be shared with multiple employees; each employee’s permissions are independent.</span></div></div>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{loading ? <div className="table-state loading-state"><span className="spinner" /><strong>Loading current group access</strong></div> : groups.length ? <div className="group-access-list">{groups.map((group) => <label className="group-access-row" key={group.id}><input type="checkbox" checked={Boolean(access[group.id])} onChange={(event) => toggle(group.id, event.target.checked)} disabled={pending} /><span><strong>{group.name}</strong><small>{group.account_count} accounts · {group.description || "No description"}</small></span><select aria-label={`Access level for ${group.name}`} value={access[group.id] ?? "OPERATOR"} onChange={(event) => setAccess((current) => ({ ...current, [group.id]: event.target.value as EmployeeGroupAccess["access_level"] }))} disabled={pending || !access[group.id]}><option value="VIEWER">View only</option><option value="OPERATOR">Operate accounts</option><option value="MANAGER">Manage group</option></select></label>)}</div> : <div className="workspace-empty compact"><UsersRound size={20} /><strong>No groups available</strong><span>Create an account group before assigning employee access.</span></div>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="button" className="button primary" onClick={() => void save()} disabled={pending || loading}>{pending ? "Saving access…" : `Save access · ${Object.keys(access).length}`}</button></div></section></div>;
}

function EmployeeModal({ mode, employee, onClose, onSaved, onError }: { mode: "create" | "edit"; employee?: Employee; onClose: () => void; onSaved: (employee: Employee) => void; onError: (message: string) => void }) {
  const [name, setName] = useState(employee?.full_name ?? "");
  const [email, setEmail] = useState(employee?.email ?? "");
  const [role, setRole] = useState(employee?.role ?? "ACCOUNT_OPERATOR");
  const [status, setStatus] = useState(employee?.status ?? "INVITED");
  const [notes, setNotes] = useState(employee?.notes ?? "");
  const [loginPassword, setLoginPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim() || !email.trim()) { setError("Name and email are required."); return; }
    if (loginPassword && loginPassword.length < 12) { setError("Sign-in password must be at least 12 characters."); return; }
    setPending(true); setError("");
    const payload = { full_name: name.trim(), email: email.trim(), role, notes: notes.trim() || null, ...(mode === "edit" ? { status } : {}) };
    try {
      const saved = await api<Employee>(mode === "create" ? "/api/v1/employees" : `/api/v1/employees/${employee!.id}`, { method: mode === "create" ? "POST" : "PATCH", body: JSON.stringify(payload) });
      if (loginPassword) await api<void>(`/api/v1/employees/${saved.id}/password`, { method: "PUT", body: JSON.stringify({ password: loginPassword }) });
      onSaved(saved);
    } catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal employee-modal" role="dialog" aria-modal="true" aria-labelledby="employee-modal-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE ACCESS</div><h2 id="employee-modal-title">{mode === "create" ? "Create employee" : "Edit employee"}</h2><p>{mode === "create" ? "Add a person to the organization access directory." : "Update identity, role, or access state."}</p></div><button type="button" className="icon-button" aria-label="Close employee dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="employee-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="form-grid"><div className="field"><label htmlFor="employee-name">Full name <span className="required">Required</span></label><input id="employee-name" value={name} onChange={(event) => setName(event.target.value)} autoFocus disabled={pending} /></div><div className="field"><label htmlFor="employee-email">Work email <span className="required">Required</span></label><input id="employee-email" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="off" disabled={pending} /></div><div className="field"><label htmlFor="employee-role">Role</label><select id="employee-role" value={role} onChange={(event) => setRole(event.target.value)} disabled={pending}><option value="ADMINISTRATOR">Administrator</option><option value="ACCOUNT_OPERATOR">Account operator</option><option value="AUDITOR">Auditor</option></select></div>{mode === "edit" && <div className="field"><label htmlFor="employee-status">Access status</label><select id="employee-status" value={status} onChange={(event) => setStatus(event.target.value)} disabled={pending}><option value="ACTIVE">Active</option><option value="INVITED">Invited</option><option value="SUSPENDED">Suspended</option><option value="DEACTIVATED">Deactivated</option></select></div>}</div><div className="field"><label htmlFor="employee-login-password">Set sign-in password</label><input id="employee-login-password" type="password" value={loginPassword} onChange={(event) => setLoginPassword(event.target.value)} autoComplete="new-password" minLength={12} placeholder="Leave blank to keep the current password" disabled={pending} /><small className="field-caption">At least 12 characters. Never returned or written to audit logs.</small></div><div className="field"><label htmlFor="employee-notes">Notes</label><textarea className="resize-none workspace-textarea" id="employee-notes" value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="Optional hand-off or access note" disabled={pending} /></div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Audited workspace access</strong><span>Employee identity, password changes, and group access changes are audited without exposing secrets.</span></div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="employee-form" className="button primary" disabled={pending}>{pending ? "Saving employee…" : mode === "create" ? "Create employee" : "Save employee"}</button></div></section></div>;
}

function DeactivateEmployeeModal({ employee, onClose, onDone, onError }: { employee: Employee; onClose: () => void; onDone: (employee: Employee) => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState(false);
  const deactivate = async () => { setPending(true); try { onDone(await api<Employee>(`/api/v1/employees/${employee.id}`, { method: "PATCH", body: JSON.stringify({ status: "DEACTIVATED" }) })); } catch (requestError) { const message = (requestError as Error).message; onError(message); setPending(false); } };
  return <div className="modal-backdrop" role="presentation"><section className="modal employee-modal" role="dialog" aria-modal="true" aria-labelledby="deactivate-employee-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCESS CONTROL</div><h2 id="deactivate-employee-title">Deactivate employee?</h2><p>{employee.full_name} · {employee.email}</p></div><button type="button" className="icon-button" aria-label="Close deactivation dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><div className="modal-body"><div className="delete-warning"><Trash2 size={18} /><div><strong>Access will be disabled</strong><span>The employee record stays in the directory for audit history. You can see it under Deactivated and edit the record later.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="button" className="button danger" onClick={() => void deactivate()} disabled={pending}>{pending ? "Deactivating…" : "Deactivate employee"}</button></div></section></div>;
}

function LegacySettingsPage({ onNotify }: { onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [settings, setSettings] = useState<WorkspaceSettings | null>(null);
  const [form, setForm] = useState<Partial<WorkspaceSettings>>({});
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => { setLoading(true); setError(""); api<WorkspaceSettings>("/api/v1/settings").then((value) => { setSettings(value); setForm(value); }).catch((requestError) => setError((requestError as Error).message || "Settings are unavailable.")).finally(() => setLoading(false)); }, []);
  useEffect(() => { load(); }, [load]);
  const setValue = <K extends keyof WorkspaceSettings>(key: K, value: WorkspaceSettings[K]) => setForm((current) => ({ ...current, [key]: value }));
  const save = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(""); try { const saved = await api<WorkspaceSettings>("/api/v1/settings", { method: "PUT", body: JSON.stringify({ default_group_capacity: Number(form.default_group_capacity), default_login_concurrency: Number(form.default_login_concurrency), max_concurrent_operations: Number(form.max_concurrent_operations), max_active_profiles: Number(form.max_active_profiles), observer_interval_ms: Number(form.observer_interval_ms), session_idle_timeout_minutes: Number(form.session_idle_timeout_minutes), auto_close_completed_sessions: Boolean(form.auto_close_completed_sessions), retain_audit_days: Number(form.retain_audit_days) }) }); setSettings(saved); setForm(saved); onNotify("success", "Workspace settings saved and audited."); } catch (requestError) { const message = (requestError as Error).message; setError(message); onNotify("error", message); } finally { setPending(false); } };
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE CONFIGURATION" title="Settings" description="Control organization policy and orchestration limits from one place. Sensitive runtime secrets remain outside the UI." action={<><button type="button" className="button secondary" onClick={load} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh settings</button><span className="settings-state"><span className="pulse-dot" /> {settings?.organization_name ?? "Workspace"}</span></>} />{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={load}>Retry</button></div>}{loading && !settings ? <div className="workspace-panel"><div className="table-state loading-state"><span className="spinner" /><strong>Loading workspace settings</strong></div></div> : settings && <form className="settings-form" onSubmit={save} noValidate><div className="settings-grid"><section className="workspace-panel"><div className="panel-header"><div><h2>Operation limits</h2><p>Guardrails used when future workers dispatch account operations.</p></div></div><div className="settings-fields"><SettingsNumber id="default-group-capacity" label="Default group capacity" note="Suggested account boundary for new groups." value={form.default_group_capacity} min={1} max={5000} onChange={(value) => setValue("default_group_capacity", value)} /><SettingsNumber id="default-login-concurrency" label="Default login concurrency" note="Profiles opened per login batch by default." value={form.default_login_concurrency} min={1} max={50} onChange={(value) => setValue("default_login_concurrency", value)} /><SettingsNumber id="max-concurrent-operations" label="Max concurrent operations" note="Upper policy limit for bulk account actions." value={form.max_concurrent_operations} min={1} max={50} onChange={(value) => setValue("max_concurrent_operations", value)} /><SettingsNumber id="max-active-profiles" label="Max active profiles" note="Maximum saved profiles eligible for simultaneous work." value={form.max_active_profiles} min={1} max={1000} onChange={(value) => setValue("max_active_profiles", value)} /></div></section><section className="workspace-panel"><div className="panel-header"><div><h2>Session behavior</h2><p>Keep browser work bounded and observable.</p></div></div><div className="settings-fields"><SettingsNumber id="observer-interval" label="Observer interval" note="How often the observer checks an active session." suffix="ms" value={form.observer_interval_ms} min={500} max={60000} onChange={(value) => setValue("observer_interval_ms", value)} /><SettingsNumber id="session-timeout" label="Idle session timeout" note="Maximum idle time before a session is eligible for closure." suffix="min" value={form.session_idle_timeout_minutes} min={5} max={1440} onChange={(value) => setValue("session_idle_timeout_minutes", value)} /><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.auto_close_completed_sessions)} onChange={(event) => setValue("auto_close_completed_sessions", event.target.checked)} /><span><strong>Close completed sessions automatically</strong><small>Release profile locks after a verified terminal result.</small></span></label></div></section></div><section className="workspace-panel settings-retention"><div className="panel-header"><div><h2>Audit retention</h2><p>How long workspace mutation evidence should be retained by policy.</p></div><SettingsNumber id="audit-retention" label="Retention window" note="Minimum 30 days; retained records stay available for audit." suffix="days" value={form.retain_audit_days} min={30} max={3650} onChange={(value) => setValue("retain_audit_days", value)} /></div></section><div className="settings-runtime"><section className="workspace-panel"><div className="panel-header"><div><h2>Runtime status</h2><p>Read-only process configuration. Changes here require the deployment environment.</p></div></div><div className="settings-list"><div className="settings-row"><span><strong>Environment</strong><small>Current API process mode.</small></span><code>{settings.runtime_environment}</code></div><div className="settings-row"><span><strong>Browser launch</strong><small>Whether the local browser runtime is enabled.</small></span><InventoryStatus status={settings.runtime_browser_launch_enabled ? "ENABLED" : "DISABLED"} /></div><div className="settings-row"><span><strong>Observer / worker</strong><small>Process-level automation flags.</small></span><code>{settings.runtime_observer_enabled ? "Observer on" : "Observer off"} · {settings.runtime_worker_enabled ? "Worker on" : "Worker off"}</code></div><div className="settings-row"><span><strong>Redis</strong><small>Queue and lock backend availability.</small></span><InventoryStatus status={settings.runtime_redis_configured ? "CONFIGURED" : "NOT_CONFIGURED"} /></div></div></section><div className="security-callout"><SlidersHorizontal size={17} /><div><strong>Safe configuration boundary</strong><span>Database URLs, API tokens, encryption keys, proxy passwords, and browser executable settings remain deployment-managed and are never returned by this endpoint.</span></div></div></div><div className="settings-savebar"><span>{settings.updated_at ? `Last saved ${formatDateTime(settings.updated_at)}` : "Not saved yet"}</span><button type="submit" className="button primary" disabled={pending}>{pending ? "Saving settings…" : "Save settings"}</button></div></form>}<footer className="page-footer"><span>Northstar Operations</span><span>Workspace policy · audited</span></footer></div>;
}

function BackupProgress({ kind, stage, percent }: { kind: "download" | "restore"; stage: string; percent: number | null }) {
  return <div className="backup-progress" role="status" aria-live="polite">
    <div className="backup-progress-header"><span className="backup-progress-icon">{kind === "restore" ? <Upload size={17} /> : <Download size={17} />}</span><span><strong>{kind === "restore" ? "Restoring workspace" : "Creating workspace backup"}</strong><small>{stage}</small></span><b>{percent === null ? "Working" : `${percent}%`}</b></div>
    <div className={`backup-progress-track${percent === null ? " indeterminate" : ""}`} role="progressbar" aria-label={stage} aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent ?? undefined}><span style={percent === null ? undefined : { width: `${percent}%` }} /></div>
    <p>{percent === null ? "Preparing the next measurable step…" : "Progress for the current step, measured from processed items or bytes."}</p>
  </div>;
}

type BackupComponents = { database: boolean; profiles: boolean; deployment_config: boolean };
type StoredBackup = { name: string; kind: "workspace" | "safety"; created_at: string; size_bytes: number; encrypted: boolean; table_count: number | null; record_count: number | null; profile_files: number | null; created_by: string | null; components: BackupComponents };
const formatArchiveSize = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(2)} GB` : formatBytes(bytes);

function BackupOptionsModal({ passphrase, confirmation, includeProfiles, includeRuntimeConfig, onPassphrase, onConfirmation, onProfiles, onRuntimeConfig, onClose, onConfirm }: { passphrase: string; confirmation: string; includeProfiles: boolean; includeRuntimeConfig: boolean; onPassphrase: (value: string) => void; onConfirmation: (value: string) => void; onProfiles: (value: boolean) => void; onRuntimeConfig: (value: boolean) => void; onClose: () => void; onConfirm: () => void }) {
  const closeRef = useRef<HTMLButtonElement>(null);
  const modalRef = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); onClose(); }
      if (event.key === "Tab" && modalRef.current) {
        const controls = Array.from(modalRef.current.querySelectorAll<HTMLElement>('button:not([disabled]), input:not([disabled])'));
        if (!controls.length) return;
        if (event.shiftKey && document.activeElement === controls[0]) { event.preventDefault(); controls[controls.length - 1].focus(); }
        else if (!event.shiftKey && document.activeElement === controls[controls.length - 1]) { event.preventDefault(); controls[0].focus(); }
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => { document.removeEventListener("keydown", onKeyDown); previous?.focus(); };
  }, [onClose]);
  const ready = passphrase.length >= 12 && passphrase === confirmation;
  return createPortal(<div className="modal-backdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section ref={modalRef} className="modal backup-options-modal" role="dialog" aria-modal="true" aria-labelledby="backup-options-title"><div className="modal-header"><div><span className="eyebrow">NEW ENCRYPTED ARCHIVE</span><h2 id="backup-options-title">Choose backup contents</h2><p>Keep the full workspace or leave out the largest and most sensitive optional files.</p></div><button ref={closeRef} type="button" className="icon-button" onClick={onClose} aria-label="Close backup options"><X size={18} /></button></div><div className="modal-body backup-options-body"><div className="backup-option locked"><span className="backup-option-icon"><Database size={18} /></span><span><strong>Workspace database</strong><small>Accounts, groups, employees, newsletters, proxies, audit history, and encrypted credentials. Required for restore.</small></span><span className="backup-required">Required</span></div><label className={`backup-option ${includeProfiles ? "selected" : ""}`}><input type="checkbox" checked={includeProfiles} onChange={(event) => onProfiles(event.target.checked)} /><span className="backup-option-icon"><Layers3 size={18} /></span><span><strong>Browser profiles</strong><small>Saved Chrome sessions and local browser data. If omitted, restore keeps existing profile files; some accounts may need to sign in again.</small></span></label><label className={`backup-option ${includeRuntimeConfig ? "selected" : ""}`}><input type="checkbox" checked={includeRuntimeConfig} onChange={(event) => onRuntimeConfig(event.target.checked)} /><span className="backup-option-icon"><LockKeyhole size={18} /></span><span><strong>Deployment configuration</strong><small>Runtime secrets and files such as northstar.env and clouds.yaml. If excluded, keep the source SECRET_KEY separately to recover encrypted credentials on a new server.</small></span></label><div className="backup-options-passphrase"><div className="field"><label htmlFor="backup-options-passphrase">Backup passphrase</label><input id="backup-options-passphrase" type="password" autoComplete="new-password" minLength={12} value={passphrase} onChange={(event) => onPassphrase(event.target.value)} placeholder="At least 12 characters" /></div><div className="field"><label htmlFor="backup-options-confirm">Confirm passphrase</label><input id="backup-options-confirm" type="password" autoComplete="new-password" minLength={12} value={confirmation} onChange={(event) => onConfirmation(event.target.value)} placeholder="Enter it again" /></div>{confirmation && passphrase !== confirmation && <small className="backup-passphrase-error" role="alert">The passphrases do not match.</small>}<small>Keep this passphrase separately. Northstar cannot recover it.</small></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button primary" onClick={onConfirm} disabled={!ready}><Download size={16} /> Create & download backup</button></div></section></div>, document.body);
}

function SavedBackupCatalog({ items, loading, error, busy, onRefresh, onDownload }: { items: StoredBackup[]; loading: boolean; error: string; busy: boolean; onRefresh: () => void; onDownload: (name: string) => void }) {
  return <section className="backup-catalog" aria-label="Saved backups">
    <div className="backup-catalog-heading"><div><span className="eyebrow">ENCRYPTED ARCHIVE LIBRARY</span><h3>Saved backups <span>{items.length}</span></h3><p>Archives remain on this server until an administrator removes them from server storage. Keep an off-server copy too.</p></div><button type="button" className="button secondary" onClick={onRefresh} disabled={loading}><RefreshCw size={15} className={loading ? "spin-icon" : ""} /> Refresh list</button></div>
    {error && <div className="inline-error" role="alert"><XCircle size={16} /> {error}</div>}
    {loading && !items.length ? <div className="backup-catalog-state"><span className="spinner" /> Loading saved backups…</div> : !items.length ? <div className="backup-catalog-state"><Database size={22} /><strong>No saved backups yet</strong><span>Generate a backup above to add it here.</span></div> : <div className="backup-catalog-list">{items.map((item) => <article className="backup-catalog-item" key={item.name}><div className="backup-catalog-item-main"><span className="backup-catalog-item-icon">{item.kind === "safety" ? <ShieldCheck size={18} /> : <Database size={18} />}</span><div><div className="backup-catalog-title"><strong>{item.kind === "safety" ? "Pre-restore safety copy" : "Workspace backup"}</strong><span>{item.encrypted ? "Encrypted" : "Archive"}</span></div><code title={item.name}>{item.name}</code><small>Created {formatDateTime(item.created_at)}{item.created_by ? ` · by ${item.created_by}` : ""}</small><small>Includes database{item.components?.profiles ? " · browser profiles" : ""}{item.components?.deployment_config ? " · deployment configuration" : ""}</small></div></div><div className="backup-catalog-metrics"><span><strong>{formatArchiveSize(item.size_bytes)}</strong><small>Archive size</small></span><span><strong>{item.table_count ?? "—"}</strong><small>Data tables</small></span><span><strong>{item.record_count?.toLocaleString() ?? "—"}</strong><small>Records</small></span><span><strong>{item.profile_files?.toLocaleString() ?? "—"}</strong><small>Profile files</small></span></div><button type="button" className="button secondary backup-catalog-download" onClick={() => onDownload(item.name)} disabled={busy}><Download size={16} /> Download</button></article>)}</div>}
  </section>;
}

function SettingsPage({ onNotify }: { onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [settings, setSettings] = useState<WorkspaceSettings | null>(null);
  const [form, setForm] = useState<Partial<WorkspaceSettings>>({});
  const [loading, setLoading] = useState(true);
  const [pending, setPending] = useState(false);
  const [testing, setTesting] = useState<"telegram" | "email" | "report" | null>(null);
  const [clearOvh, setClearOvh] = useState(false);
  const [error, setError] = useState("");
  const [activeSettingsTab, setActiveSettingsTab] = useState<"operations" | "integrations" | "newsletter" | "notifications" | "backups">("operations");
  const [backupPassphrase, setBackupPassphrase] = useState("");
  const [backupPassphraseConfirm, setBackupPassphraseConfirm] = useState("");
  const [showBackupOptions, setShowBackupOptions] = useState(false);
  const [includeBackupProfiles, setIncludeBackupProfiles] = useState(true);
  const [includeBackupRuntime, setIncludeBackupRuntime] = useState(true);
  const [restorePassphrase, setRestorePassphrase] = useState("");
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreConfirmed, setRestoreConfirmed] = useState(false);
  const [backupBusy, setBackupBusy] = useState<"download" | "restore" | null>(null);
  const [backupProgress, setBackupProgress] = useState<{ stage: string; percent: number | null } | null>(null);
  const [safetyBackupName, setSafetyBackupName] = useState("");
  const [backupCatalog, setBackupCatalog] = useState<StoredBackup[]>([]);
  const [backupCatalogLoading, setBackupCatalogLoading] = useState(false);
  const [backupCatalogError, setBackupCatalogError] = useState("");
  const closeBackupOptions = useCallback(() => { setShowBackupOptions(false); setBackupPassphrase(""); setBackupPassphraseConfirm(""); }, []);
  const loadBackupCatalog = useCallback(async () => {
    setBackupCatalogLoading(true); setBackupCatalogError("");
    try { const result = await api<{ items: StoredBackup[] }>("/api/v1/backups/catalog"); setBackupCatalog(result.items); }
    catch (cause) { setBackupCatalogError((cause as Error).message || "Saved backups could not be loaded."); }
    finally { setBackupCatalogLoading(false); }
  }, []);
  useEffect(() => { if (activeSettingsTab === "backups") void loadBackupCatalog(); }, [activeSettingsTab, loadBackupCatalog]);
  const load = useCallback(() => { setLoading(true); setError(""); api<WorkspaceSettings>("/api/v1/settings").then((value) => { setSettings(value); setForm({ ...value, telegram_bot_token: "", telegram_chat_id: "", email_recipient_email: "", resend_api_key: "", smtp_password: "", openrouter_api_key: "", openai_api_key: "", two_captcha_api_key: "", ovh_application_key: "", ovh_application_secret: "", ovh_consumer_key: "" }); setClearOvh(false); }).catch((requestError) => setError((requestError as Error).message || "Settings are unavailable.")).finally(() => setLoading(false)); }, []);
  useEffect(() => { load(); }, [load]);
  const setValue = <K extends keyof WorkspaceSettings>(key: K, value: WorkspaceSettings[K]) => setForm((current) => ({ ...current, [key]: value }));
  const save = async (event: FormEvent) => { event.preventDefault(); setPending(true); setError(""); try { const payload: Record<string, unknown> = { default_group_capacity: Number(form.default_group_capacity), default_login_concurrency: Number(form.default_login_concurrency), max_concurrent_operations: Number(form.max_concurrent_operations), max_active_profiles: Number(form.max_active_profiles), observer_interval_ms: Number(form.observer_interval_ms), session_idle_timeout_minutes: Number(form.session_idle_timeout_minutes), auto_close_completed_sessions: Boolean(form.auto_close_completed_sessions), retain_audit_days: Number(form.retain_audit_days), two_captcha_enabled: Boolean(form.two_captcha_enabled), ovh_endpoint: form.ovh_endpoint ?? "ovh-eu", newsletter_agent_enabled: Boolean(form.newsletter_agent_enabled), newsletter_agent_provider: form.newsletter_agent_provider ?? "openrouter", newsletter_agent_model: form.newsletter_agent_model?.trim() || "openrouter/free", newsletter_agent_temperature: Number(form.newsletter_agent_temperature ?? 0), newsletter_agent_max_steps: Number(form.newsletter_agent_max_steps ?? 12), newsletter_agent_timeout_seconds: Number(form.newsletter_agent_timeout_seconds ?? 30), newsletter_agent_shadow_mode: Boolean(form.newsletter_agent_shadow_mode), newsletter_agent_live_execution_enabled: Boolean(form.newsletter_agent_live_execution_enabled), newsletter_agent_learning_mode: Boolean(form.newsletter_agent_learning_mode), newsletter_agent_chain: Array.isArray(form.newsletter_agent_chain) ? form.newsletter_agent_chain : [], telegram_enabled: Boolean(form.telegram_enabled), email_enabled: Boolean(form.email_enabled), email_provider: form.email_provider ?? "NONE", email_from_address: form.email_from_address || null, smtp_host: form.smtp_host || null, smtp_port: Number(form.smtp_port) || 587, smtp_username: form.smtp_username || null, smtp_use_tls: Boolean(form.smtp_use_tls), notify_employee_logins: Boolean(form.notify_employee_logins), notify_group_reports: Boolean(form.notify_group_reports), notify_account_status: Boolean(form.notify_account_status), notify_operation_failures: Boolean(form.notify_operation_failures) }; if (form.telegram_chat_id?.trim()) payload.telegram_chat_id = form.telegram_chat_id.trim(); if (form.email_recipient_email?.trim()) payload.email_recipient_email = form.email_recipient_email.trim(); if (form.telegram_bot_token?.trim()) payload.telegram_bot_token = form.telegram_bot_token.trim(); if (form.resend_api_key?.trim()) payload.resend_api_key = form.resend_api_key.trim(); if (form.smtp_password?.trim()) payload.smtp_password = form.smtp_password; if (form.openrouter_api_key?.trim()) payload.openrouter_api_key = form.openrouter_api_key.trim(); if (form.openai_api_key?.trim()) payload.openai_api_key = form.openai_api_key.trim(); if (form.two_captcha_api_key?.trim()) payload.two_captcha_api_key = form.two_captcha_api_key.trim(); if (clearOvh) payload.ovh_clear_credentials = true; else if (form.ovh_application_key || form.ovh_application_secret || form.ovh_consumer_key) { if (!form.ovh_application_key?.trim() || !form.ovh_application_secret?.trim() || !form.ovh_consumer_key?.trim()) throw new Error("Enter all three OVH API credentials together."); payload.ovh_application_key = form.ovh_application_key.trim(); payload.ovh_application_secret = form.ovh_application_secret.trim(); payload.ovh_consumer_key = form.ovh_consumer_key.trim(); } const saved = await api<WorkspaceSettings>("/api/v1/settings", { method: "PUT", body: JSON.stringify(payload) }); setSettings(saved); setForm({ ...saved, telegram_bot_token: "", telegram_chat_id: "", email_recipient_email: "", resend_api_key: "", smtp_password: "", openrouter_api_key: "", openai_api_key: "", two_captcha_api_key: "", ovh_application_key: "", ovh_application_secret: "", ovh_consumer_key: "" }); setClearOvh(false); onNotify("success", "Workspace settings saved securely."); } catch (requestError) { const message = (requestError as Error).message; setError(message); onNotify("error", message); } finally { setPending(false); } };
  const runAction = async (kind: "telegram" | "email" | "report") => { setTesting(kind); try { if (kind === "report") await api<void>("/api/v1/notifications/status-report", { method: "POST" }); else await api<void>("/api/v1/notifications/test", { method: "POST", body: JSON.stringify({ channel: kind }) }); onNotify("success", kind === "report" ? "The current group status report was sent." : `${kind === "telegram" ? "Telegram" : "Email"} test notification sent.`); } catch (requestError) { const message = (requestError as Error).message; setError(message); onNotify("error", message); } finally { setTesting(null); } };
  const saveDownload = (blob: Blob, name: string) => { const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = name; document.body.appendChild(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000); };
  const watchBackup = (operationId: string) => window.setInterval(() => {
    fetch(`/api/v1/backups/progress/${operationId}`, { credentials: "include" }).then((response) => response.ok ? response.json() : null).then((value) => {
      if (value?.state === "running") setBackupProgress({ stage: value.stage, percent: value.percent });
    }).catch(() => undefined);
  }, 650);
  const readBackupDownload = async (response: Response) => {
    const total = Number(response.headers.get("content-length")) || 0;
    if (!response.body) { const blob = await response.blob(); setBackupProgress({ stage: "Downloading encrypted archive", percent: 100 }); return blob; }
    const reader = response.body.getReader(); const chunks: Uint8Array[] = []; let received = 0;
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      chunks.push(value); received += value.byteLength;
      setBackupProgress({ stage: "Downloading encrypted archive", percent: total ? Math.min(100, Math.round(received * 100 / total)) : null });
    }
    setBackupProgress({ stage: "Downloading encrypted archive", percent: 100 });
    return new Blob(chunks as BlobPart[], { type: "application/octet-stream" });
  };
  const uploadBackup = (data: FormData, operationId: string): Promise<{ status: number; payload: Record<string, unknown> }> => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest(); xhr.open("POST", "/api/v1/backups/restore"); xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) setBackupProgress({ stage: "Uploading encrypted archive", percent: Math.round(event.loaded * 100 / event.total) }); };
    xhr.upload.onload = () => setBackupProgress({ stage: "Waiting for server verification", percent: null });
    xhr.onload = () => { let payload: Record<string, unknown> = {}; try { payload = JSON.parse(xhr.responseText); } catch { /* handled by status below */ } resolve({ status: xhr.status, payload }); };
    xhr.onerror = () => reject(new Error("Backup upload failed. Check the network connection."));
    data.append("operation_id", operationId); xhr.send(data);
  });
  const downloadBackup = async (passphrase: string, savedName?: string, options?: { profiles: boolean; runtime: boolean }) => {
    if (!savedName && passphrase.length < 12) { onNotify("error", "Use a backup passphrase with at least 12 characters."); return false; }
    if (!savedName && passphrase !== backupPassphraseConfirm) { onNotify("error", "The backup passphrases do not match."); return false; }
    setBackupBusy("download");
    setBackupProgress({ stage: savedName ? "Preparing saved backup download" : "Preparing encrypted backup", percent: null });
    const operationId = crypto.randomUUID();
    const progressTimer = savedName ? null : watchBackup(operationId);
    try {
      const response = savedName ? await fetch(`/api/v1/backups/archive/${encodeURIComponent(savedName)}`, { credentials: "include" }) : await fetch("/api/v1/backups", { method: "POST", credentials: "include", body: (() => { const data = new FormData(); data.append("passphrase", passphrase); data.append("operation_id", operationId); data.append("include_profiles", String(options?.profiles ?? true)); data.append("include_runtime_config", String(options?.runtime ?? true)); return data; })() });
      if (!response.ok) { const payload = await response.json().catch(() => ({})); throw new Error(typeof payload.detail === "string" ? payload.detail : `Backup download failed (HTTP ${response.status}).`); }
      if (progressTimer !== null) window.clearInterval(progressTimer);
      const disposition = response.headers.get("content-disposition") ?? "";
      const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] ?? "northstar-backup.nstar";
      saveDownload(await readBackupDownload(response), filename);
      if (!savedName) { setBackupPassphrase(""); setBackupPassphraseConfirm(""); void loadBackupCatalog(); }
      onNotify("success", savedName ? "Saved encrypted backup downloaded." : "Encrypted workspace backup saved on the server and downloaded.");
      return true;
    } catch (requestError) { onNotify("error", (requestError as Error).message || "Backup download failed."); return false; }
    finally { if (progressTimer !== null) window.clearInterval(progressTimer); if (!savedName) { setBackupPassphrase(""); setBackupPassphraseConfirm(""); } setBackupBusy(null); setTimeout(() => setBackupProgress(null), 2500); }
  };
  const restoreWorkspace = async () => {
    if (!restoreFile) { onNotify("error", "Choose a Northstar backup file first."); return; }
    if (restorePassphrase.length < 12) { onNotify("error", "Enter the backup passphrase (at least 12 characters)."); return; }
    if (!restoreConfirmed) { onNotify("error", "Confirm that restore replaces the current workspace data."); return; }
    setBackupBusy("restore");
    setBackupProgress({ stage: "Uploading encrypted archive", percent: 0 });
    const operationId = crypto.randomUUID();
    const progressTimer = watchBackup(operationId);
    try {
      const data = new FormData(); data.append("file", restoreFile); data.append("passphrase", restorePassphrase);
      const { status, payload } = await uploadBackup(data, operationId);
      if (status < 200 || status >= 300) throw new Error(typeof payload.detail === "string" ? payload.detail : `Restore failed (HTTP ${status}).`);
      setBackupProgress({ stage: "Workspace restored", percent: 100 });
      setSafetyBackupName(typeof payload.safety_backup === "string" ? payload.safety_backup : "");
      setRestoreFile(null); setRestoreConfirmed(false);
      onNotify("success", `Backup restored: ${payload.table_count ?? "all"} data tables. ${payload.profile_root_restored ? "Browser profiles restored." : "Existing browser profiles kept."} ${payload.deployment_config_staged ? `Deployment configuration staged at ${payload.deployment_config_staged}.` : "No deployment configuration was included."} Sign in again if the saved session was replaced.`);
      void loadBackupCatalog();
      if (typeof payload.safety_backup === "string") await downloadBackup("", payload.safety_backup);
      setRestorePassphrase("");
      setTimeout(() => window.location.reload(), 1800);
    } catch (requestError) { onNotify("error", (requestError as Error).message || "Restore failed."); }
    finally { window.clearInterval(progressTimer); setBackupBusy(null); setTimeout(() => setBackupProgress(null), 2500); }
  };
  return <div className="workspace-page"><WorkspacePageHeader eyebrow="WORKSPACE CONFIGURATION" title="Settings" description="Control workspace policy, notification delivery, and operational reporting from one place." action={<><button type="button" className="button secondary" onClick={load} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh settings</button><span className="settings-state"><span className="pulse-dot" /> {settings?.organization_name ?? "Workspace"}</span></>} />{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={load}>Retry</button></div>}{loading && !settings ? <div className="workspace-panel"><div className="table-state loading-state"><span className="spinner" /><strong>Loading workspace settings</strong></div></div> : settings && <form className="settings-form" onSubmit={save} noValidate><div className="settings-tabs" role="group" aria-label="Settings categories">{([{ id: "operations", label: "Operations", hint: "Limits, sessions, audit, runtime", icon: <SlidersHorizontal size={16} /> }, { id: "integrations", label: "Integrations", hint: "OVHcloud and 2Captcha", icon: <KeyRound size={16} /> }, { id: "newsletter", label: "Newsletter agent", hint: "Models and adaptive behavior", icon: <Mail size={16} /> }, { id: "notifications", label: "Notifications", hint: "Delivery channels and events", icon: <Bell size={16} /> }, { id: "backups", label: "Backups", hint: "Encrypted export and restore", icon: <Database size={16} /> }] as const).map((tab) => <button type="button" key={tab.id} className={`settings-tab ${activeSettingsTab === tab.id ? "active" : ""}`} aria-pressed={activeSettingsTab === tab.id} onClick={() => setActiveSettingsTab(tab.id)}><span className="settings-tab-icon">{tab.icon}</span><span className="settings-tab-copy"><strong>{tab.label}</strong><small>{tab.hint}</small></span><ArrowRight className="settings-tab-arrow" size={14} /></button>)}</div><div className="settings-tab-caption" aria-live="polite">{activeSettingsTab === "operations" ? "Workspace limits and runtime behavior" : activeSettingsTab === "integrations" ? "Provider credentials and verification services" : activeSettingsTab === "newsletter" ? "Adaptive signup configuration and model fallbacks" : activeSettingsTab === "notifications" ? "Notification channels, recipients, and event preferences" : "Create a protected copy or restore a saved workspace"}</div><div className="settings-grid" hidden={activeSettingsTab !== "operations"}><section className="workspace-panel" hidden={activeSettingsTab !== "operations"}><div className="panel-header"><div><h2>Operation limits</h2><p>Guardrails used when workers dispatch account operations.</p></div></div><div className="settings-fields"><SettingsNumber id="default-group-capacity" label="Default group capacity" note="Suggested account boundary for new groups." value={form.default_group_capacity} min={1} max={5000} onChange={(value) => setValue("default_group_capacity", value)} /><SettingsNumber id="default-login-concurrency" label="Default login concurrency" note="Profiles opened per login batch by default." value={form.default_login_concurrency} min={1} max={50} onChange={(value) => setValue("default_login_concurrency", value)} /><SettingsNumber id="max-concurrent-operations" label="Max concurrent operations" note="Upper policy limit for bulk account actions." value={form.max_concurrent_operations} min={1} max={50} onChange={(value) => setValue("max_concurrent_operations", value)} /><SettingsNumber id="max-active-profiles" label="Max active profiles" note="Maximum saved profiles eligible for simultaneous work." value={form.max_active_profiles} min={1} max={1000} onChange={(value) => setValue("max_active_profiles", value)} /></div></section><section className="workspace-panel" hidden={activeSettingsTab !== "operations"}><div className="panel-header"><div><h2>Session behavior</h2><p>Keep browser work bounded and observable.</p></div></div><div className="settings-fields"><SettingsNumber id="observer-interval" label="Observer interval" note="How often the observer checks an active session." suffix="ms" value={form.observer_interval_ms} min={500} max={60000} onChange={(value) => setValue("observer_interval_ms", value)} /><SettingsNumber id="session-timeout" label="Idle session timeout" note="Maximum idle time before a session is eligible for closure." suffix="min" value={form.session_idle_timeout_minutes} min={5} max={1440} onChange={(value) => setValue("session_idle_timeout_minutes", value)} /><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.auto_close_completed_sessions)} onChange={(event) => setValue("auto_close_completed_sessions", event.target.checked)} /><span><strong>Close completed sessions automatically</strong><small>Release profile locks after a verified terminal result.</small></span></label></div></section></div><section className="workspace-panel notification-panel" hidden={activeSettingsTab !== "integrations"}><div className="panel-header"><div><h2>OVHcloud API</h2><p>Connect VPS services and Public Cloud instances for live status and power control.</p></div><span className={`settings-chip ${settings.ovh_credentials_configured ? "ready" : ""}`}>{settings.ovh_credentials_configured ? "Configured" : "Not configured"}</span></div><div className="settings-fields"><div className="field"><label htmlFor="ovh-endpoint">API region</label><select id="ovh-endpoint" value={form.ovh_endpoint ?? "ovh-eu"} onChange={(event) => setValue("ovh_endpoint", event.target.value as WorkspaceSettings["ovh_endpoint"])}><option value="ovh-eu">Europe</option><option value="ovh-ca">Canada</option><option value="ovh-us">United States</option></select></div><div className="ovh-credential-grid"><div className="field"><label htmlFor="ovh-app-key">Application key</label><input id="ovh-app-key" type="password" autoComplete="off" value={form.ovh_application_key ?? ""} onChange={(event) => setValue("ovh_application_key", event.target.value)} placeholder={settings.ovh_credentials_configured ? "Saved · enter to replace" : "Enter application key"} disabled={clearOvh} /></div><div className="field"><label htmlFor="ovh-app-secret">Application secret</label><input id="ovh-app-secret" type="password" autoComplete="off" value={form.ovh_application_secret ?? ""} onChange={(event) => setValue("ovh_application_secret", event.target.value)} placeholder={settings.ovh_credentials_configured ? "Saved · enter to replace" : "Enter application secret"} disabled={clearOvh} /></div><div className="field"><label htmlFor="ovh-consumer-key">Consumer key</label><input id="ovh-consumer-key" type="password" autoComplete="off" value={form.ovh_consumer_key ?? ""} onChange={(event) => setValue("ovh_consumer_key", event.target.value)} placeholder={settings.ovh_credentials_configured ? "Saved · enter to replace" : "Enter consumer key"} disabled={clearOvh} /></div></div><p className="field-hint">The selected region must match the OVHcloud account region. Grant GET access to /vps, /vps/*, /cloud/project, and /cloud/project/*/instance. For power control, also grant POST on /vps/*/start, /vps/*/stop, /cloud/project/*/instance/*/start, and /cloud/project/*/instance/*/stop. A 401 points to credentials, signature, or region; a 403 means the consumer key does not allow the requested path.</p>{settings.ovh_credentials_configured && <label className="setting-toggle"><input type="checkbox" checked={clearOvh} onChange={(event) => setClearOvh(event.target.checked)} /><span><strong>Remove saved OVH credentials</strong><small>Applies when you save Settings.</small></span></label>}</div></section><section className="workspace-panel notification-panel" hidden={activeSettingsTab !== "integrations"}><div className="panel-header"><div><h2>2Captcha</h2><p>Solve supported reCAPTCHA v2 and standalone Turnstile widgets in active browser sessions.</p></div><span className={`settings-chip ${settings.two_captcha_enabled && settings.two_captcha_api_key_configured ? "ready" : ""}`}>{settings.two_captcha_enabled && settings.two_captcha_api_key_configured ? "Enabled" : "Manual fallback"}</span></div><div className="settings-fields"><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.two_captcha_enabled)} onChange={(event) => setValue("two_captcha_enabled", event.target.checked)} /><span><strong>Enable 2Captcha</strong><small>Only recognized widgets are sent to the provider and may incur a per-task charge. Phone verification, unsupported, and IP-bound challenges stay manual.</small></span></label><div className="field"><label htmlFor="two-captcha-key">2Captcha API key</label><input id="two-captcha-key" type="password" value={form.two_captcha_api_key ?? ""} onChange={(event) => setValue("two_captcha_api_key", event.target.value)} placeholder={settings.two_captcha_api_key_configured ? "Leave blank to keep the encrypted key" : "Enter your API key"} autoComplete="new-password" /></div></div></section><section className="workspace-panel notification-panel newsletter-agent-settings" hidden={activeSettingsTab !== "newsletter"}><div className="panel-header"><div><h2>Newsletter Agent</h2><p>Bounded agent decisions for adaptive newsletter learning. It receives redacted live controls only; it never receives credentials or unrestricted browser control.</p></div><span className={`settings-chip ${settings.newsletter_agent_enabled && (settings.openrouter_api_key_configured || settings.openai_api_key_configured) ? "ready" : ""}`}>{!settings.newsletter_agent_enabled ? "Disabled" : (settings.openrouter_api_key_configured || settings.openai_api_key_configured) ? (settings.newsletter_agent_live_execution_enabled ? "Live enabled" : "Shadow / fallback") : "Key required"}</span></div><div className="settings-fields"><div className="newsletter-agent-overview"><div className="newsletter-agent-section-title"><span><Mail size={16} /></span><div><strong>Agent availability</strong><small>Use bounded AI decisions to adapt to signup flows.</small></div></div><label className="setting-toggle newsletter-agent-enable-toggle"><input type="checkbox" checked={Boolean(form.newsletter_agent_enabled)} onChange={(event) => setValue("newsletter_agent_enabled", event.target.checked)} /><span><strong>Enable Newsletter Agent</strong><small>Adaptive learning asks the bounded agent to choose validated live controls before recording a redacted website skill.</small></span></label></div><AgentChainEditor chain={Array.isArray(form.newsletter_agent_chain) ? form.newsletter_agent_chain : []} onChange={(value) => setValue("newsletter_agent_chain", value)} /><div className="form-grid notification-fields newsletter-agent-credentials"><div className="field"><label htmlFor="newsletter-agent-provider">Provider</label><select id="newsletter-agent-provider" value={form.newsletter_agent_provider ?? "openrouter"} onChange={(event) => setValue("newsletter_agent_provider", event.target.value as "openrouter")}><option value="openrouter">OpenRouter</option></select></div><div className="field"><label htmlFor="newsletter-agent-model">Model</label><input id="newsletter-agent-model" value={form.newsletter_agent_model ?? "openrouter/free"} onChange={(event) => setValue("newsletter_agent_model", event.target.value)} placeholder="openrouter/free" /></div><div className="field"><label htmlFor="openrouter-api-key">OpenRouter API key</label><input id="openrouter-api-key" type="password" value={form.openrouter_api_key ?? ""} onChange={(event) => setValue("openrouter_api_key", event.target.value)} placeholder={(settings.openrouter_api_key_configured || settings.openai_api_key_configured) ? "Leave blank to keep the saved key" : "Enter key once; it is encrypted"} autoComplete="new-password" /></div><div className="field"><label htmlFor="openai-api-key">OpenAI API key</label><input id="openai-api-key" type="password" value={form.openai_api_key ?? ""} onChange={(event) => setValue("openai_api_key", event.target.value)} placeholder={settings.openai_api_key_configured ? "Leave blank to keep the saved key" : "Enter key once; it is encrypted"} autoComplete="new-password" /></div><div className="field"><label htmlFor="newsletter-agent-temperature">Temperature</label><input id="newsletter-agent-temperature" type="number" min="0" max="1" step="0.1" value={form.newsletter_agent_temperature ?? 0} onChange={(event) => setValue("newsletter_agent_temperature", Number(event.target.value))} /><small>Deterministic routing is recommended.</small></div><SettingsNumber id="newsletter-agent-max-steps" label="Maximum steps" note="Hard limit before manual review." value={form.newsletter_agent_max_steps} min={1} max={24} onChange={(value) => setValue("newsletter_agent_max_steps", value)} /><SettingsNumber id="newsletter-agent-timeout" label="Timeout" note="Maximum provider wait per decision." suffix="sec" value={form.newsletter_agent_timeout_seconds} min={2} max={120} onChange={(value) => setValue("newsletter_agent_timeout_seconds", value)} /></div><div className="newsletter-agent-guardrails"><div className="newsletter-agent-section-title"><span><ShieldCheck size={16} /></span><div><strong>Execution guardrails</strong><small>Control what the agent may observe and decide.</small></div></div><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.newsletter_agent_shadow_mode)} onChange={(event) => setValue("newsletter_agent_shadow_mode", event.target.checked)} /><span><strong>Shadow mode</strong><small>Record bounded suggestions without allowing them to affect browser actions.</small></span></label><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.newsletter_agent_live_execution_enabled)} onChange={(event) => setValue("newsletter_agent_live_execution_enabled", event.target.checked)} disabled={!form.newsletter_agent_enabled || !(settings.openrouter_api_key_configured || settings.openai_api_key_configured)} /><span><strong>Allow live agent decisions</strong><small>Only verified, visible, enabled controls may be selected. CAPTCHA, anti-bot challenges, arbitrary navigation, and Gmail confirmation remain outside the agent.</small></span></label><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.newsletter_agent_learning_mode)} onChange={(event) => setValue("newsletter_agent_learning_mode", event.target.checked)} disabled={!form.newsletter_agent_enabled || !(settings.openrouter_api_key_configured || settings.openai_api_key_configured)} /><span><strong>Learn this website on the first successful run</strong><small>Record redacted control decisions as a versioned website skill. Future runs replay the saved skill before asking the agent again.</small></span></label><div className="security-callout"><ShieldCheck size={17} /><div><strong>Safe rollout</strong><span>Start with shadow mode and one newsletter/account. Bulk execution remains a separate operator decision.</span></div></div></div></div></section><section className="workspace-panel notification-panel" hidden={activeSettingsTab !== "notifications"}><div className="panel-header"><div><h2>Telegram notifications</h2><p>Receive employee sign-ins, group status, account status, and failed-operation notices in a Telegram chat.</p></div><span className={`settings-chip ${settings.telegram_configured ? "ready" : ""}`}>{settings.telegram_configured ? "Configured" : "Not configured"}</span></div><div className="settings-fields"><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.telegram_enabled)} onChange={(event) => setValue("telegram_enabled", event.target.checked)} /><span><strong>Enable Telegram delivery</strong><small>Use a Telegram bot created through BotFather.</small></span></label><div className="form-grid notification-fields"><div className="field"><label htmlFor="telegram-token">Bot token</label><input id="telegram-token" type="password" value={form.telegram_bot_token ?? ""} onChange={(event) => setValue("telegram_bot_token", event.target.value)} placeholder={settings.telegram_configured ? "Leave blank to keep the saved token" : "123456:bot-token"} autoComplete="new-password" /></div><div className="field"><label htmlFor="telegram-chat-id">Chat ID</label><input id="telegram-chat-id" value={form.telegram_chat_id ?? ""} onChange={(event) => setValue("telegram_chat_id", event.target.value)} placeholder={settings.telegram_chat_id ?? "e.g. -1001234567890"} /></div></div><div className="notification-actions"><button type="button" className="button secondary" onClick={() => void runAction("telegram")} disabled={testing !== null || !settings.telegram_configured}>{testing === "telegram" ? "Sending…" : "Send Telegram test"}</button></div></div></section><section className="workspace-panel notification-panel" hidden={activeSettingsTab !== "notifications"}><div className="panel-header"><div><h2>Email notifications & password reset</h2><p>Choose Resend or an SMTP provider such as Gmail or Zoho. Credentials are encrypted and masked after saving.</p></div><span className={`settings-chip ${settings.email_configured ? "ready" : ""}`}>{settings.email_configured ? `${settings.email_provider} ready` : "Not configured"}</span></div><div className="settings-fields"><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.email_enabled)} onChange={(event) => setValue("email_enabled", event.target.checked)} /><span><strong>Enable email delivery</strong><small>The same provider is used for notifications and employee password-reset emails.</small></span></label><div className="form-grid notification-fields"><div className="field"><label htmlFor="email-provider">Provider</label><select id="email-provider" value={form.email_provider ?? "NONE"} onChange={(event) => setValue("email_provider", event.target.value as WorkspaceSettings["email_provider"])}><option value="NONE">Select provider</option><option value="RESEND">Resend</option><option value="SMTP">SMTP · Gmail / Zoho / other</option></select></div><div className="field"><label htmlFor="notification-recipient">Notification recipient</label><input id="notification-recipient" type="email" value={form.email_recipient_email ?? ""} onChange={(event) => setValue("email_recipient_email", event.target.value)} placeholder={settings.email_recipient_email ?? "alerts@company.com"} /></div><div className="field"><label htmlFor="email-from">From address</label><input id="email-from" type="email" value={form.email_from_address ?? ""} onChange={(event) => setValue("email_from_address", event.target.value)} placeholder="notifications@company.com" /></div>{form.email_provider === "RESEND" && <div className="field"><label htmlFor="resend-key">Resend API key</label><input id="resend-key" type="password" value={form.resend_api_key ?? ""} onChange={(event) => setValue("resend_api_key", event.target.value)} placeholder={settings.email_configured ? "Leave blank to keep the saved key" : "re_…"} autoComplete="new-password" /></div>}{form.email_provider === "SMTP" && <><div className="field"><label htmlFor="smtp-host">SMTP host</label><input id="smtp-host" value={form.smtp_host ?? ""} onChange={(event) => setValue("smtp_host", event.target.value)} placeholder="smtp.gmail.com" /></div><div className="field"><label htmlFor="smtp-port">SMTP port</label><input id="smtp-port" type="number" min="1" max="65535" value={form.smtp_port ?? 587} onChange={(event) => setValue("smtp_port", Number(event.target.value))} /></div><div className="field"><label htmlFor="smtp-user">SMTP username</label><input id="smtp-user" value={form.smtp_username ?? ""} onChange={(event) => setValue("smtp_username", event.target.value)} placeholder="your mailbox" /></div><div className="field"><label htmlFor="smtp-password">SMTP password / app password</label><input id="smtp-password" type="password" value={form.smtp_password ?? ""} onChange={(event) => setValue("smtp_password", event.target.value)} placeholder={settings.email_configured ? "Leave blank to keep the saved password" : "App password"} autoComplete="new-password" /></div><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.smtp_use_tls)} onChange={(event) => setValue("smtp_use_tls", event.target.checked)} /><span><strong>Use TLS</strong><small>Port 587 uses STARTTLS; port 465 uses SSL/TLS automatically. Gmail requires a Google app password and its mailbox as the username/from address.</small></span></label></>}</div><div className="notification-actions"><button type="button" className="button secondary" onClick={() => void runAction("email")} disabled={testing !== null || !settings.email_configured}>{testing === "email" ? "Sending…" : "Send email test"}</button></div></div></section><section className="workspace-panel notification-panel" hidden={activeSettingsTab !== "notifications"}><div className="panel-header"><div><h2>Notification events</h2><p>Choose which operational events reach the configured channels.</p></div></div><div className="settings-fields notification-events"><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.notify_employee_logins)} onChange={(event) => setValue("notify_employee_logins", event.target.checked)} /><span><strong>Employee sign-ins</strong><small>Login and workspace access activity.</small></span></label><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.notify_group_reports)} onChange={(event) => setValue("notify_group_reports", event.target.checked)} /><span><strong>Group status reports</strong><small>Active sessions and account counts by group.</small></span></label><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.notify_account_status)} onChange={(event) => setValue("notify_account_status", event.target.checked)} /><span><strong>Account status changes</strong><small>Authentication and account readiness changes.</small></span></label><label className="setting-toggle"><input type="checkbox" checked={Boolean(form.notify_operation_failures)} onChange={(event) => setValue("notify_operation_failures", event.target.checked)} /><span><strong>Failed operations</strong><small>Operations that stop or need review.</small></span></label></div><div className="notification-actions report-actions"><button type="button" className="button secondary" onClick={() => void runAction("report")} disabled={testing !== null || (!settings.telegram_configured && !settings.email_configured)}>{testing === "report" ? "Preparing report…" : "Send current group status report"}</button></div></section><section className="workspace-panel settings-retention" hidden={activeSettingsTab !== "operations"}><div className="panel-header"><div><h2>Audit retention</h2><p>How long workspace mutation evidence should be retained by policy.</p></div><SettingsNumber id="audit-retention" label="Retention window" note="Minimum 30 days; retained records stay available for audit." suffix="days" value={form.retain_audit_days} min={30} max={3650} onChange={(value) => setValue("retain_audit_days", value)} /></div></section><div className="settings-runtime" hidden={activeSettingsTab !== "operations"}><section className="workspace-panel"><div className="panel-header"><div><h2>Runtime status</h2><p>Read-only process configuration. Changes here require the deployment environment.</p></div></div><div className="settings-list"><div className="settings-row"><span><strong>Environment</strong><small>Current API process mode.</small></span><code>{settings.runtime_environment}</code></div><div className="settings-row"><span><strong>Browser launch</strong><small>Whether the local browser runtime is enabled.</small></span><InventoryStatus status={settings.runtime_browser_launch_enabled ? "ENABLED" : "DISABLED"} /></div><div className="settings-row"><span><strong>Observer / worker</strong><small>Process-level automation flags.</small></span><code>{settings.runtime_observer_enabled ? "Observer on" : "Observer off"} · {settings.runtime_worker_enabled ? "Worker on" : "Worker off"}</code></div><div className="settings-row"><span><strong>Redis</strong><small>Queue and lock backend availability.</small></span><InventoryStatus status={settings.runtime_redis_configured ? "CONFIGURED" : "NOT_CONFIGURED"} /></div></div></section><div className="security-callout"><SlidersHorizontal size={17} /><div><strong>Safe configuration boundary</strong><span>Provider tokens, SMTP passwords, proxy passwords, account credentials, and browser executable settings remain encrypted or deployment-managed.</span></div></div></div><section className="backup-workspace" hidden={activeSettingsTab !== "backups"} aria-label="Workspace backup and restore"><div className="backup-intro"><span className="backup-mark"><ShieldCheck size={20} /></span><div><h2>Workspace backup</h2><p>Protect the full workspace before maintenance or migration.</p></div><span className="settings-chip ready">Encrypted archive</span></div>{backupBusy && backupProgress && <BackupProgress kind={backupBusy} stage={backupProgress.stage} percent={backupProgress.percent} />}<div className="backup-grid"><article className="backup-card"><div className="backup-card-heading"><span className="backup-card-icon"><Download size={17} /></span><div><h3>Generate and download</h3><p>Choose which components to protect. The workspace database is always included; browser profiles and deployment configuration are optional.</p></div></div><button type="button" className="button primary" onClick={() => { setIncludeBackupProfiles(true); setIncludeBackupRuntime(true); setShowBackupOptions(true); }} disabled={backupBusy !== null}><Download size={16} /> Configure new backup</button></article><article className="backup-card restore-card"><div className="backup-card-heading"><span className="backup-card-icon"><Upload size={17} /></span><div><h3>Upload and restore</h3><p>Restore an encrypted Northstar archive. The database is replaced; browser profiles are replaced only if included in the archive.</p></div></div><div className="field"><label htmlFor="backup-file">Backup file</label><input id="backup-file" type="file" accept=".nstar,application/octet-stream" onChange={(event) => { setRestoreFile(event.target.files?.[0] ?? null); setRestoreConfirmed(false); }} aria-describedby="backup-file-help" /><small id="backup-file-help">Maximum upload: 20 GB. Close active sessions and allow several times the archive size in free server disk space for verification and the safety copy.</small></div><div className="field"><label htmlFor="restore-passphrase">Backup passphrase</label><input id="restore-passphrase" type="password" autoComplete="current-password" value={restorePassphrase} onChange={(event) => setRestorePassphrase(event.target.value)} placeholder="Enter the passphrase used for this archive" /></div><label className="backup-confirm"><input type="checkbox" checked={restoreConfirmed} onChange={(event) => setRestoreConfirmed(event.target.checked)} /><span>I understand this replaces the current workspace data.</span></label><button type="button" className="button danger" onClick={() => void restoreWorkspace()} disabled={backupBusy !== null || !restoreFile || restorePassphrase.length < 12 || !restoreConfirmed}>{backupBusy === "restore" && <span className="button-spinner" />}{backupBusy === "restore" ? "Verifying and restoring…" : "Restore workspace backup"}</button>{safetyBackupName && <button type="button" className="button secondary" onClick={() => void downloadBackup("", safetyBackupName)} disabled={backupBusy !== null}><Download size={15} /> Download pre-restore safety copy</button>}</article></div><BulkProfileImportSection /><SavedBackupCatalog items={backupCatalog} loading={backupCatalogLoading} error={backupCatalogError} busy={backupBusy !== null} onRefresh={() => void loadBackupCatalog()} onDownload={(name) => void downloadBackup("", name)} /><div className="security-callout backup-security"><ShieldCheck size={17} /><div><strong>What is included</strong><span>Every backup includes all application database tables and encrypted credential records. You can choose whether to include browser profiles and protected deployment configuration files such as northstar.env/clouds.yaml. On a new server, apply the included runtime configuration securely and preserve its SECRET_KEY before starting the restored data; Northstar does not overwrite live system configuration during restore. Store downloaded archives and their passphrases separately.</span></div></div></section><div className="settings-savebar"><span>{settings.updated_at ? `Last saved ${formatDateTime(settings.updated_at)}` : "Not saved yet"}</span><button type="submit" className="button primary" disabled={pending}>{pending ? "Saving settings…" : "Save settings"}</button></div></form>}{showBackupOptions && <BackupOptionsModal passphrase={backupPassphrase} confirmation={backupPassphraseConfirm} includeProfiles={includeBackupProfiles} includeRuntimeConfig={includeBackupRuntime} onPassphrase={setBackupPassphrase} onConfirmation={setBackupPassphraseConfirm} onProfiles={setIncludeBackupProfiles} onRuntimeConfig={setIncludeBackupRuntime} onClose={closeBackupOptions} onConfirm={() => { setShowBackupOptions(false); void downloadBackup(backupPassphrase, undefined, { profiles: includeBackupProfiles, runtime: includeBackupRuntime }); }} />}<footer className="page-footer"><span>Northstar Operations</span><span>Workspace policy · audited</span></footer></div>;
}

function AgentChainEditor({ chain, onChange }: { chain: NewsletterAgentChainEntry[]; onChange: (value: NewsletterAgentChainEntry[]) => void }) {
  const ordered = [...chain].sort((left, right) => left.priority - right.priority);
  const update = (index: number, patch: Partial<NewsletterAgentChainEntry>) => onChange(ordered.map((entry, entryIndex) => entryIndex === index ? { ...entry, ...patch } : entry).map((entry, entryIndex) => ({ ...entry, priority: entryIndex + 1 })));
  const move = (index: number, direction: -1 | 1) => { const target = index + direction; if (target < 0 || target >= ordered.length) return; const next = [...ordered]; [next[index], next[target]] = [next[target], next[index]]; onChange(next.map((entry, entryIndex) => ({ ...entry, priority: entryIndex + 1 }))); };
  const add = () => { if (ordered.length >= 8) return; onChange([...ordered, { priority: ordered.length + 1, name: `Fallback ${ordered.length}`, enabled: false, provider: "openrouter", model: "", min_confidence: 0.8, max_calls: 1, timeout_seconds: 20, vision_enabled: false }]); };
  return <div className="agent-chain-editor"><div className="agent-chain-heading"><div><strong>Agent Chain</strong><small>Models are tried in priority order. The first complete DOM-validated plan wins; no later agent is called.</small></div><button type="button" className="button secondary" onClick={add} disabled={ordered.length >= 8}><Plus size={15} /> Add fallback</button></div>{ordered.map((entry, index) => <div className="agent-chain-card" key={`${entry.priority}-${entry.name}`}><div className="agent-chain-card-header"><div className="agent-chain-rank"><span className="drag-handle">☰</span><strong>{index === 0 ? "MAIN AGENT" : `FALLBACK ${index}`}</strong><span className={`settings-chip ${entry.health === "HEALTHY" || !entry.health ? "ready" : ""}`}>{entry.health ?? "HEALTHY"}</span></div><div className="agent-chain-reorder"><button type="button" className="icon-button" aria-label={`Move ${entry.name} up`} onClick={() => move(index, -1)} disabled={index === 0}>↑</button><button type="button" className="icon-button" aria-label={`Move ${entry.name} down`} onClick={() => move(index, 1)} disabled={index === ordered.length - 1}>↓</button>{ordered.length > 1 && <button type="button" className="icon-button" aria-label={`Remove ${entry.name}`} onClick={() => onChange(ordered.filter((_, entryIndex) => entryIndex !== index).map((item, itemIndex) => ({ ...item, priority: itemIndex + 1 })))}><X size={15} /></button>}</div></div><div className="agent-chain-fields"><label className="setting-toggle"><input type="checkbox" checked={entry.enabled} onChange={(event) => update(index, { enabled: event.target.checked })} /><span><strong>Enabled</strong><small>Eligible for this session's bounded fallback chain.</small></span></label><div className="field"><label>Display name</label><input value={entry.name} onChange={(event) => update(index, { name: event.target.value })} /></div><div className="field"><label>Provider</label><select value={entry.provider} onChange={(event) => update(index, { provider: event.target.value as NewsletterAgentChainEntry["provider"] })}><option value="openrouter">OpenRouter</option><option value="openai">OpenAI-compatible</option></select></div><div className="field agent-chain-model"><label>Model ID</label><input value={entry.model} onChange={(event) => update(index, { model: event.target.value })} placeholder="provider/model[:free]" /></div><div className="field"><label>Minimum confidence</label><input type="number" min="0" max="1" step="0.01" value={entry.min_confidence} onChange={(event) => update(index, { min_confidence: Number(event.target.value) })} /></div><div className="field"><label>Max calls</label><input type="number" min="1" max="8" value={entry.max_calls} onChange={(event) => update(index, { max_calls: Number(event.target.value) })} /></div><div className="field"><label>Timeout</label><input type="number" min="2" max="120" value={entry.timeout_seconds} onChange={(event) => update(index, { timeout_seconds: Number(event.target.value) })} /></div><label className="setting-toggle compact-toggle"><input type="checkbox" checked={entry.vision_enabled} onChange={(event) => update(index, { vision_enabled: event.target.checked })} /><span><strong>Vision allowed</strong><small>Only bounded page evidence.</small></span></label></div>{entry.last_error && <small className="field-caption">Last error: {entry.last_error} · {entry.consecutive_failures ?? 0} consecutive failures{entry.last_latency_ms ? ` · ${entry.last_latency_ms} ms` : ""}</small>}</div>)}</div>;
}

function SettingsNumber({ id, label, note, suffix, value, min, max, onChange }: { id: string; label: string; note: string; suffix?: string; value: number | undefined; min: number; max: number; onChange: (value: number) => void }) { return <div className="setting-number"><div><label htmlFor={id}>{label}</label><small>{note}</small></div><div className="setting-number-control"><input id={id} type="number" min={min} max={max} step="1" value={value ?? ""} onChange={(event) => onChange(Number(event.target.value))} />{suffix && <span>{suffix}</span>}</div></div>; }

function InfrastructurePage({ view, groups, refreshKey, onRefresh, canDeleteHistory, onDeleteHistory, onOpenAccount, onOpenSession, onEditCredentials, onPreparePlan, onNotify }: { view: InfrastructureView; groups: Group[]; refreshKey: number; onRefresh: () => void; canDeleteHistory: boolean; onDeleteHistory: () => Promise<void>; onOpenAccount: (accountId: string) => void; onOpenSession: (session: Session) => void; onEditCredentials: (account: Account, field: CredentialField) => void; onPreparePlan: (account: Account) => void; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  if (view === "sessions") return <SessionsPage groups={groups} refreshKey={refreshKey} onRefresh={onRefresh} canDeleteHistory={canDeleteHistory} onDeleteHistory={onDeleteHistory} onOpenAccount={onOpenAccount} onOpenSession={onOpenSession} />;
  if (view === "proxies") return <ProxiesPage groups={groups} refreshKey={refreshKey} onRefresh={onRefresh} onOpenAccount={onOpenAccount} />;
  if (view === "credentials") return <CredentialsPage groups={groups} refreshKey={refreshKey} onRefresh={onRefresh} onOpenAccount={onOpenAccount} onEditCredentials={onEditCredentials} onPreparePlan={onPreparePlan} onNotify={onNotify} />;
  return <ProfilesPage refreshKey={refreshKey} onRefresh={onRefresh} onOpenAccount={onOpenAccount} />;
}

function ProxyImportModal({ onClose, onImported }: { onClose: () => void; onImported: (result: ProxyBulkImportResult) => void }) {
  const [lines, setLines] = useState("");
  const [proxyGroups, setProxyGroups] = useState<ProxyGroupList[]>([]);
  const [groupChoice, setGroupChoice] = useState("");
  const [newGroupName, setNewGroupName] = useState("");
  const [revealEntries, setRevealEntries] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ProxyBulkImportResult | null>(null);
  const entryCount = lines.split(/\r?\n/).filter((line) => line.trim()).length;
  useEffect(() => { let cancelled = false; api<ProxyGroupList[]>("/api/v1/proxies/groups").then((groups) => { if (!cancelled) setProxyGroups(groups); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Proxy lists could not be loaded."); }); return () => { cancelled = true; }; }, []);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!entryCount) { setError("Paste at least one proxy connection."); return; }
    if (!groupChoice) { setError("Choose a proxy list or explicitly select ungrouped inventory."); return; }
    if (groupChoice === "__new__" && !newGroupName.trim()) { setError("Enter a name for the new proxy list."); return; }
    if (groupChoice === "__new__" && newGroupName.trim().length > 80) { setError("Proxy list names can be up to 80 characters."); return; }
    setPending(true);
    setError("");
    try {
      const group_name = groupChoice === "__ungrouped__" ? null : groupChoice === "__new__" ? newGroupName.trim().replace(/\s+/g, " ") : groupChoice;
      const imported = await api<ProxyBulkImportResult>("/api/v1/proxies/import", { method: "POST", body: JSON.stringify({ lines, group_name }) });
      setResult(imported);
      if (imported.imported) { setLines(""); onImported(imported); }
    } catch (requestError) {
      setError((requestError as Error).message || "The proxy import could not be completed.");
    } finally { setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}>
    <section className="modal proxy-modal proxy-import-modal" role="dialog" aria-modal="true" aria-labelledby="proxy-import-title">
      <div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROXY INVENTORY</div><h2 id="proxy-import-title">Import proxy endpoints</h2><p>Choose the proxy list for this batch. Connections stay encrypted in inventory.</p></div><button type="button" className="icon-button" aria-label="Close proxy import dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div>
      <form id="proxy-import-form" className="modal-body" onSubmit={submit} noValidate>
        {error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}
        <div className="security-callout"><LockKeyhole size={17} /><div><strong>Connections are encrypted at rest</strong><span>Raw connection entries are never returned in results, audit events, or the proxy inventory.</span></div></div>
        <div className="field proxy-import-group-field"><label htmlFor="proxy-import-group">Proxy list <span className="required">Required</span></label><select id="proxy-import-group" value={groupChoice} onChange={(event) => { setGroupChoice(event.target.value); setError(""); }} disabled={pending}><option value="" disabled>Select where these proxies belong</option><option value="__ungrouped__">Ungrouped inventory</option>{proxyGroups.map((group) => <option value={group.name} key={group.id}>{group.name} · {group.proxy_count} endpoints</option>)}<option value="__new__">Create a new proxy list…</option></select><small>Every valid endpoint in this import will be added to the selected list.</small></div>
        {groupChoice === "__new__" && <div className="field proxy-import-group-field"><label htmlFor="proxy-import-new-group">New proxy list name</label><input id="proxy-import-new-group" autoFocus maxLength={80} value={newGroupName} onChange={(event) => setNewGroupName(event.target.value)} placeholder="e.g. OVH US East" disabled={pending} /></div>}
        {!result || !result.imported ? <div className="field proxy-import-field">
          <div className="field-heading"><label htmlFor="proxy-import-lines">Proxy connections <span className="required">Required</span></label><button type="button" className="text-button" onClick={() => setRevealEntries((current) => !current)} disabled={pending}>{revealEntries ? <EyeOff size={14} /> : <Eye size={14} />}{revealEntries ? "Conceal entries" : "Reveal entries"}</button></div>
          <textarea className={`resize-none proxy-import-input ${revealEntries ? "" : "proxy-import-secret"}`} id="proxy-import-lines" value={lines} onChange={(event) => { setLines(event.target.value); setError(""); setResult(null); }} placeholder={"ip:port | Label | Region\nusername:password:ip:port | Label | Region"} aria-invalid={Boolean(error)} aria-describedby="proxy-import-help" autoComplete="off" spellCheck={false} disabled={pending} autoFocus />
          <small id="proxy-import-help">One endpoint per line, up to 500. Use <code>ip:port</code> or <code>username:password:ip:port</code>; label and region are optional after <code>|</code>. {entryCount ? `${entryCount} ${entryCount === 1 ? "entry" : "entries"} ready to validate.` : ""}</small>
        </div> : null}
        {result && <div className="proxy-import-report" role="status" aria-live="polite"><div className="proxy-import-summary"><span><strong>{result.imported}</strong> imported</span><span><strong>{result.duplicates}</strong> unchanged duplicates</span><span><strong>{result.invalid}</strong> invalid</span></div><div className="proxy-import-results" aria-label="Proxy import results">{result.rows.map((row) => <div className="proxy-import-result" key={`${row.line_number}-${row.status}`}><span className={`proxy-import-result-status ${row.status.toLowerCase()}`}>{labelStatus(row.status)}</span><span>Line {row.line_number} · {row.message}</span></div>)}</div></div>}
      </form>
      <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>{result ? "Close" : "Cancel"}</button>{!result || !result.imported ? <button type="submit" form="proxy-import-form" className="button primary" disabled={pending || !entryCount || !groupChoice || (groupChoice === "__new__" && !newGroupName.trim())}>{pending ? "Importing endpoints…" : "Import proxies"}</button> : null}</div>
    </section>
  </div>;
}

function InfrastructureHeader({ eyebrow, title, description, icon, onRefresh, loading, onManageProxyGroups }: { eyebrow: string; title: string; description: string; icon: React.ReactNode; onRefresh: () => void; loading: boolean; onManageProxyGroups?: () => void }) {
  const [proxyImportOpen, setProxyImportOpen] = useState(false);
  const [proxyListOpen, setProxyListOpen] = useState(false);
  const isProxyInventory = title === "Proxies";
  return <>
    <section className="page-heading infra-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> {eyebrow}</div><h1>{title}</h1><p>{description}</p></div><div className="heading-actions">{isProxyInventory && <><button type="button" className="button secondary" onClick={() => onManageProxyGroups?.()}><Layers3 size={16} /> Manage proxy groups</button><button type="button" className="button secondary" onClick={() => setProxyListOpen(true)}><Plus size={16} /> Create proxy list</button><button type="button" className="button primary" onClick={() => setProxyImportOpen(true)}><Plus size={16} /> Import proxies</button></>}<button type="button" className="button secondary" onClick={onRefresh} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh inventory</button></div></section>
    <section className="infra-hero"><div className="infra-hero-icon">{icon}</div><div><strong>Operational inventory</strong><span>Every record is account-scoped, auditable, and safe to inspect. Credentials and proxy passwords never appear in this view.</span></div><span className="handoff-state">LIVE METADATA</span></section>
    {proxyImportOpen && <ProxyImportModal onClose={() => setProxyImportOpen(false)} onImported={() => onRefresh()} />}
    {proxyListOpen && <CreateProxyListModal onClose={() => setProxyListOpen(false)} onCreated={() => { setProxyListOpen(false); onRefresh(); }} />}
  </>;
}

function CreateProxyListModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const normalized = name.trim().replace(/\s+/g, " ");
    if (!normalized) { setError("Enter a name for this proxy list."); return; }
    if (normalized.length > 80) { setError("Proxy list names can be up to 80 characters."); return; }
    setPending(true); setError("");
    try { await api<ProxyGroupList>("/api/v1/proxies/groups", { method: "POST", body: JSON.stringify({ name: normalized }) }); onCreated(); }
    catch (requestError) { setError((requestError as Error).message || "Could not create the proxy list."); }
    finally { setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal proxy-modal" role="dialog" aria-modal="true" aria-labelledby="proxy-list-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROXY ORGANIZATION</div><h2 id="proxy-list-title">Create proxy list</h2><p>Create a named list, then add endpoints by importing or managing proxy assignments.</p></div><button type="button" className="icon-button" aria-label="Close create proxy list dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="proxy-list-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="field"><label htmlFor="proxy-list-name">List name</label><input id="proxy-list-name" autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. US East proxies" disabled={pending} /></div><div className="security-callout"><KeyRound size={17} /><div><strong>Empty lists are supported</strong><span>This list will be available in the proxy-group selector immediately. You can add endpoints later.</span></div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="proxy-list-form" className="button primary" disabled={pending}>{pending ? "Creating list…" : "Create proxy list"}</button></div></section></div>;
}

function InventoryToolbar({ value, onChange, onClear, placeholder, status, onStatusChange, statuses, loading, onRefresh }: { value: string; onChange: (value: string) => void; onClear: () => void; placeholder: string; status: string; onStatusChange: (value: string) => void; statuses: { value: string; label: string }[]; loading: boolean; onRefresh: () => void }) {
  return <div className="toolbar infra-toolbar"><label className="search-field"><Search size={17} /><span className="sr-only">Search inventory</span><input value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} />{value && <button type="button" className="clear-search" aria-label="Clear search" onClick={onClear}><X size={15} /></button>}</label><div className="toolbar-right"><label className="filter-select"><Filter size={15} /><span className="sr-only">Filter inventory status</span><select aria-label="Filter inventory status" value={status} onChange={(event) => onStatusChange(event.target.value)}><option value="ALL">All statuses</option>{statuses.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label><button type="button" className="icon-button" aria-label="Refresh inventory" onClick={onRefresh} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /></button></div></div>;
}

function InventoryStatus({ status }: { status: string }) {
  const tone = ["AUTHENTICATED", "SAVED", "ASSIGNED", "HEALTHY", "UNLOCKED", "CLOSED", "COMPLETED", "CONTACTS_IMPORTED", "LABELS_MARKERS_COMPLETED", "SECURITY_COMPLETED", "ADDONS_COMPLETED", "MAIL_WORKFLOW_COMPLETED"].includes(status) ? "ok" : ["ERROR", "CORRUPT", "UNREACHABLE", "NONEXISTENT", "FAILED", "CONTACTS_IMPORT_FAILED", "LABELS_MARKERS_FAILED", "SECURITY_FAILED", "ADDONS_FAILED", "MAIL_WORKFLOW_FAILED"].includes(status) ? "danger" : ["OPEN", "RUNNING", "NEEDS_ATTENTION", "LOGIN_REQUIRED", "REQUIRED", "IN_PROGRESS", "INTERRUPTED", "NOT_FINISHED", "VERIFICATION_REQUIRED"].includes(status) ? "warning" : "neutral";
  return <span className={`inventory-status ${tone}`}><span className="inventory-status-dot" />{status === "NOT_STARTED" ? "Never used" : labelStatus(status)}</span>;
}

function OvhPowerStatus({ status }: { status: string }) {
  const normalized = status.trim().toUpperCase();
  const started = ["RUNNING", "ACTIVE", "ENABLED", "STARTED"].includes(normalized);
  const stopped = ["STOPPED", "OFF", "SHUTOFF", "HALTED", "SHELVED", "SUSPENDED"].includes(normalized);
  const label = started ? "Started" : stopped ? "Stopped" : normalized === "UNAVAILABLE" || normalized === "MISSING" || normalized === "UNKNOWN" ? "Status unavailable" : labelStatus(status);
  return <span className={`ovh-power-status ${started ? "started" : stopped ? "stopped" : "unknown"}`} role="status"><span aria-hidden="true" />{label}</span>;
}

function SessionsPage({ groups, refreshKey, onRefresh, canDeleteHistory, onDeleteHistory, onOpenAccount, onOpenSession }: { groups: Group[]; refreshKey: number; onRefresh: () => void; canDeleteHistory: boolean; onDeleteHistory: () => Promise<void>; onOpenAccount: (accountId: string) => void; onOpenSession: (session: Session) => void }) {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [action, setAction] = useState("ALL");
  const [groupId, setGroupId] = useState("all");
  const [scope, setScope] = useState<"latest" | "all">("latest");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  useEffect(() => { let cancelled = false; setLoading(true); setError(""); const params = new URLSearchParams({ limit: "100", latest_per_account: scope === "latest" ? "true" : "false" }); if (groupId !== "all") params.set("group_id", groupId); if (action !== "ALL") params.set("purpose", action); if (status !== "ALL") params.set("outcome", status); api<Session[]>(`/api/v1/sessions?${params.toString()}`).then((data) => { if (!cancelled) setSessions(data); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Sessions are unavailable."); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [refreshKey, groupId, scope, action, status]);
  const filtered = sessions.filter((session) => !query.trim() || `${session.account_email} ${session.id} ${session.state} ${session.action ?? ""} ${session.operation_status ?? ""} ${session.status_detail ?? ""}`.toLowerCase().includes(query.trim().toLowerCase()));
  const openCount = sessions.filter((session) => isOpenSessionState(session.state)).length;
  const savedCount = sessions.filter((session) => session.profile_saved).length;
  const completedCount = sessions.filter((session) => session.operation_status === "COMPLETED").length;
  const interruptedCount = sessions.filter((session) => session.operation_status === "INTERRUPTED").length;
  const unfinishedCount = sessions.filter((session) => session.operation_status === "NOT_FINISHED").length;
  const failedCount = sessions.filter((session) => session.operation_status === "FAILED").length;
  const actionOptions = [{ value: "login", label: "Login" }, { value: "reuse", label: "Reopen profile" }, { value: "contacts", label: "Import contacts" }, { value: "security", label: "Browser security" }, { value: "labels_markers", label: "Labels & Markers" }, { value: "mail", label: "Mail workflow" }, { value: "addons", label: "Gmail add-ons" }];
  const scopeLabel = scope === "latest" ? "Latest per account" : "All activity";
  return <div className="infra-page"><InfrastructureHeader eyebrow="RUNTIME CONTROL" title="Sessions" description="Review every profile-backed action and its evidence-based outcome for each account." icon={<Clock3 size={20} />} onRefresh={onRefresh} loading={loading} /><section className="summary-grid" aria-label="Session summary"><SummaryCard label={scope === "latest" ? "Accounts in view" : "Activity records"} value={sessions.length.toString()} note={scopeLabel} icon={<Clock3 size={17} />} /><SummaryCard label="Completed" value={completedCount.toString()} note="Recorded terminal success" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Interrupted" value={interruptedCount.toString()} note="Needs attention" icon={<Activity size={17} />} tone="amber" /><SummaryCard label="Not finished" value={unfinishedCount.toString()} note={`${failedCount} failed · ${openCount} open · ${savedCount} saved`} icon={<XCircle size={17} />} /></section><section className="infra-panel"><div className="panel-header"><div><h2>{scopeLabel}</h2><p>{scope === "latest" ? "One latest action per account. Switch to All activity to inspect completed and interrupted history." : "All stored session actions in the selected scope, ordered by most recent event."}</p></div><div className="session-context-controls"><div className="group-context"><span>GROUP CONTEXT</span><label className="select-wrap"><select aria-label="Filter sessions by account group" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="all">All groups</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count} accounts</option>)}</select><ChevronDown size={15} /></label></div><div className="group-context"><span>VIEW</span><label className="select-wrap"><select aria-label="Choose session view" value={scope} onChange={(event) => setScope(event.target.value as "latest" | "all")}><option value="latest">Latest per account</option><option value="all">All activity</option></select><ChevronDown size={15} /></label></div>{canDeleteHistory && <button type="button" className="button danger small session-history-delete" disabled={deleting} onClick={() => { if (!window.confirm("Delete all closed session history? Active sessions will stay open, and audit events are retained.")) return; setDeleting(true); void onDeleteHistory().catch((requestError) => setError((requestError as Error).message)).finally(() => setDeleting(false)); }}><Trash2 size={14} />{deleting ? "Deleting…" : "Delete history"}</button>}</div></div><div className="session-filter-row"><InventoryToolbar value={query} onChange={setQuery} onClear={() => setQuery("")} placeholder="Search account, action, session, or outcome" status={status} onStatusChange={setStatus} statuses={[{ value: "IN_PROGRESS", label: "In progress" }, { value: "COMPLETED", label: "Completed" }, { value: "INTERRUPTED", label: "Interrupted" }, { value: "NOT_FINISHED", label: "Not finished" }, { value: "FAILED", label: "Failed" }]} loading={loading} onRefresh={onRefresh} /><label className="filter-select session-action-filter"><Filter size={15} /><span className="sr-only">Filter sessions by action</span><select aria-label="Filter sessions by action" value={action} onChange={(event) => setAction(event.target.value)}><option value="ALL">All actions</option>{actionOptions.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label></div>{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={onRefresh}>Retry</button></div>}{!loading && !filtered.length ? <div className="table-state empty-state"><div className="empty-icon"><Clock3 size={21} /></div><strong>No session activity matches this view</strong><span>Try a different action, outcome, group, or view scope.</span></div> : <div className="table-scroll"><table className="infra-table sessions-table"><caption className="sr-only">{scopeLabel} session activity</caption><thead><tr><th>Account</th><th>Action</th><th>Outcome</th><th>Gmail state</th><th>Profile</th><th>Runtime</th><th>Last event</th><th><span className="sr-only">Open</span></th></tr></thead><tbody>{filtered.map((session) => <tr key={session.id}><td><button type="button" className="infra-link" onClick={() => onOpenAccount(session.account_id)}><strong>{session.account_email}</strong><small className="mono">{session.id}</small></button></td><td><strong className="session-action-label">{session.action ?? labelStatus(session.purpose ?? "login")}</strong><small className="table-subtext">{session.duration_seconds != null ? `${session.duration_seconds}s duration` : "Duration pending"}</small></td><td><InventoryStatus status={session.operation_status ?? "NOT_FINISHED"} /><small className="table-subtext">{session.status_detail ?? labelStatus(session.state)}</small></td><td><InventoryStatus status={session.login_state || session.state} /><small className="table-subtext">{labelStatus(session.state)}</small></td><td><span className="metadata-cell"><span className={`status-dot ${session.profile_saved ? "teal" : "neutral"}`} />{session.profile_saved ? "Saved" : "Reserved"}</span></td><td><span className="table-subtext">{labelStatus(session.runtime_type)}</span><small className="table-subtext">{session.browser_opened ? "Browser open" : "Browser closed"}</small></td><td><span className="table-subtext">{formatDateTime(session.last_event_at)}</span></td><td><button type="button" className="row-open" aria-label={`Open session for ${session.account_email}`} onClick={() => onOpenSession(session)}><ArrowRight size={16} /></button></td></tr>)}</tbody></table></div>}</section><footer className="page-footer"><span>Northstar Operations</span><span>Session ledger · {scopeLabel.toLowerCase()}</span></footer></div>;
}

function BrowserDesktopPage({ isAdmin, isActive, groups, refreshKey, onRefresh, onOpenSession, onRunOperation, onCompactProfiles, onOperationHost, onConfirmStopAll, navHidden, onToggleNavigation }: { isAdmin: boolean; isActive: boolean; groups: Group[]; refreshKey: number; onRefresh: () => void; onOpenSession: (session: Session) => void; onRunOperation: (kind: RegistryAction, accounts: Account[]) => void; onCompactProfiles: (accounts: Account[]) => void; onOperationHost: (node: HTMLElement | null) => void; onConfirmStopAll: () => void; navHidden: boolean; onToggleNavigation: () => void }) {
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [groupId, setGroupId] = useState(() => window.localStorage.getItem("northstar-desktop-group") || "");
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<DesktopAccountFilter>("ALL");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [visibleAccountLimit, setVisibleAccountLimit] = useState(100);
  const [scope, setScope] = useState<"selected" | "filtered">("selected");
  const [operation, setOperation] = useState<RegistryAction>("login");
  const [accountRailCollapsed, setAccountRailCollapsed] = useState(() => window.localStorage.getItem("northstar-desktop-accounts-collapsed") === "true");
  const [inspectorOpen, setInspectorOpen] = useState(() => window.localStorage.getItem("northstar-desktop-inspector-open") !== "false");
  const [inspectorWidth, setInspectorWidth] = useState(() => Math.max(230, Math.min(380, Number(window.localStorage.getItem("northstar-desktop-inspector-width")) || 280)));
  const [bulkCloseConfirmation, setBulkCloseConfirmation] = useState(false);
  const [closeAllConfirmation, setCloseAllConfirmation] = useState(false);
  const [bulkClosing, setBulkClosing] = useState(false);
  const [bulkCloseProgress, setBulkCloseProgress] = useState({ completed: 0, total: 0 });
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const [concurrency, setConcurrency] = useState(4);
  const [maxConcurrency, setMaxConcurrency] = useState(10);
  const [job, setJob] = useState<LoginJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [launching, setLaunching] = useState(false);
  const [focusing, setFocusing] = useState("");
  const [profileZipBusy, setProfileZipBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const viewerRef = useRef<HTMLIFrameElement>(null);
  const operationHostRef = useCallback((node: HTMLDivElement | null) => onOperationHost(node), [onOperationHost]);
  const liveBrowserCount = sessions.filter((session) => session.browser_alive).length;
  const searchedAccounts = searchDesktopAccounts(accounts, query);
  const activeByAccount = new Map(sessions.map((session) => [session.account_id, session]));
  const liveAccountIds = new Set(sessions.filter((session) => session.browser_alive).map((session) => session.account_id));
  const statusCounts = countDesktopAccounts(searchedAccounts, liveAccountIds);
  const filteredAccounts = filterDesktopAccounts(searchedAccounts, statusFilter, liveAccountIds);
  const displayedAccounts = filteredAccounts.slice(0, visibleAccountLimit);
  const selectedSession = sessions.find((session) => session.id === selectedSessionId) ?? sessions.find((session) => session.browser_alive) ?? null;
  const inspectedAccount = accounts.find((account) => selectedIds.includes(account.id)) ?? accounts.find((account) => account.id === selectedSession?.account_id) ?? null;
  const inspectedSession = inspectedAccount ? activeByAccount.get(inspectedAccount.id) ?? null : selectedSession;
  const visibleSelectedIds = new Set(filteredAccounts.filter((account) => selectedIds.includes(account.id)).map((account) => account.id));
  const operationTargets = scope === "selected" ? filteredAccounts.filter((account) => visibleSelectedIds.has(account.id)) : filteredAccounts;
  // Browser launches follow the left account checklist only. The inspector's
  // operation scope is independent and must not disable or redirect Login.
  const launchTargets = filteredAccounts.filter((account) => selectedIds.includes(account.id));
  const selectedOpenSessions = selectedLiveSessions(sessions, new Set(selectedIds), new Set(filteredAccounts.map((account) => account.id)));

  useEffect(() => { window.localStorage.setItem("northstar-desktop-accounts-collapsed", String(accountRailCollapsed)); }, [accountRailCollapsed]);
  useEffect(() => { window.localStorage.setItem("northstar-desktop-inspector-open", String(inspectorOpen)); }, [inspectorOpen]);
  useEffect(() => { window.localStorage.setItem("northstar-desktop-inspector-width", String(inspectorWidth)); }, [inspectorWidth]);
  useEffect(() => { setVisibleAccountLimit(100); }, [groupId, query, statusFilter]);

  useEffect(() => {
    if (!groupId && groups.length) setGroupId(groups[0].id);
    else if (groupId && groupId !== "all" && !groups.some((group) => group.id === groupId)) setGroupId(groups[0]?.id ?? "");
  }, [groups, groupId]);

  const loadAccounts = useCallback(async () => {
    if (!groupId) { setAccounts([]); return; }
    const params = new URLSearchParams({ page: "1", page_size: "10000" });
    if (groupId !== "all") params.set("group_id", groupId);
    const result = await api<{ items: Account[] }>(`/api/v1/accounts?${params.toString()}`);
    setAccounts(result.items);
  }, [groupId]);

  const loadSessions = useCallback(async () => {
    const params = new URLSearchParams({ latest_per_account: "false", limit: "100" });
    // Browser windows belong to the signed-in user's complete accessible
    // scope, not just the currently selected launch group. Keep them in the
    // switcher when the account rail changes groups.
    const rows = await api<Session[]>(`/api/v1/sessions?${params.toString()}`);
    const activeRows = rows.filter((session) => isOpenSessionState(session.state));
    setSessions(activeRows);
    setSelectedSessionId((current) => activeRows.some((session) => session.id === current && session.browser_alive)
      ? current
      : activeRows.find((session) => session.browser_alive)?.id ?? activeRows[0]?.id ?? "");
    return activeRows;
  }, [groupId]);

  useEffect(() => {
    if (!isActive) return;
    let cancelled = false;
    setLoading(true); setError("");
    Promise.all([
      api<{ enabled: boolean }>("/api/v1/desktop/status"),
      api<{ default_login_concurrency: number; max_active_profiles: number }>("/api/v1/desktop/runtime"),
      loadAccounts(), loadSessions(),
    ]).then(([status, runtime]) => {
      if (cancelled) return;
      setEnabled(status.enabled);
      const ceiling = Math.max(1, Math.min(10, runtime.max_active_profiles || 10));
      setMaxConcurrency(ceiling);
      setConcurrency((current) => Math.min(ceiling, Math.max(1, runtime.default_login_concurrency || current)));
    }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Browser desktop could not load."); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [isActive, refreshKey, loadAccounts, loadSessions]);

  useEffect(() => {
    if (!enabled || !isActive) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      void loadSessions().catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Session list refresh failed."); });
    }, 4000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [enabled, isActive, loadSessions]);

  useEffect(() => {
    if (!job || !["RUNNING", "QUEUED"].includes(job.status)) return;
    let cancelled = false;
    const timer = window.setInterval(() => {
      api<JobProgress>(`/api/v1/login-jobs/${job.id}/progress`).then((progress) => {
        if (!cancelled) setJob((current) => current?.id === progress.id ? { ...current, ...progress, items: progress.items } : current);
      }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Batch progress could not refresh."); });
    }, 3000);
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [job?.id, job?.status]);

  const focusSession = async (session: Session) => {
    if (!session.browser_alive) {
      setSelectedSessionId(session.id);
      setNotice(`Chrome is not detected for ${session.account_email}. Open session details to review the launch state.`);
      return;
    }
    setFocusing(session.id); setError(""); setNotice("");
    try {
      let lastError: unknown;
      for (let attempt = 0; attempt < 5; attempt += 1) {
        try {
          await api<{ focused: boolean }>(`/api/v1/sessions/${session.id}/focus`, { method: "POST" });
          setSelectedSessionId(session.id);
          return;
        } catch (requestError) {
          lastError = requestError;
          const message = (requestError as Error).message || "";
          if (!message.includes("window could not be focused") || attempt === 4) throw requestError;
          await new Promise((resolve) => window.setTimeout(resolve, 300 * (attempt + 1)));
        }
      }
      throw lastError;
    }
    catch (requestError) { setError((requestError as Error).message || "The Chrome window could not be focused. Use Alt+Tab in the VNC toolbar to switch windows."); }
    finally { setFocusing(""); }
  };

  const downloadInspectedProfile = async () => {
    if (!inspectedAccount || profileZipBusy || inspectedSession) return;
    setProfileZipBusy(true); setError(""); setNotice("");
    try {
      const query = new URLSearchParams({ account_ids: inspectedAccount.id });
      const link = document.createElement("a"); link.href = `/api/v1/profiles/export?${query.toString()}`; link.style.display = "none"; document.body.appendChild(link); link.click(); link.remove();
      setNotice(`Profile ZIP download started for ${inspectedAccount.email}.`);
    } catch (requestError) { setError((requestError as Error).message || "The account profile ZIP could not be created."); }
    finally { setProfileZipBusy(false); }
  };

  const startAccounts = async (chosen: Account[], forceLogin = false) => {
    if (!chosen.length) { setError(scope === "selected" ? "Select one or more accounts from this group first." : "No accounts match the current group and filters."); return; }
    setLaunching(true); setError(""); setNotice("");
    try {
      let existing = chosen.map((account) => activeByAccount.get(account.id)).filter((session): session is Session => Boolean(session));
      if (forceLogin) {
        const loginInProgress = new Set(["REQUIRED", "PROFILE_CHECK", "VERIFICATION_REQUIRED", "MFA_REQUIRED", "CAPTCHA", "UNKNOWN_PAGE"]);
        const keepRunningLogin = existing.filter((session) => session.purpose === "login" && loginInProgress.has(session.login_state));
        const restart = existing.filter((session) => !keepRunningLogin.includes(session));
        for (const session of restart) await api<Session>(`/api/v1/sessions/${session.id}/close`, { method: "POST" });
        if (restart.length) {
          const restartedIds = new Set(restart.map((session) => session.id));
          setSessions((current) => current.filter((session) => !restartedIds.has(session.id)));
          existing = keepRunningLogin;
        }
      }
      const live = existing.filter((session) => session.browser_alive);
      const orphaned = existing.filter((session) => !session.browser_alive);
      if (orphaned.length) {
        const stillStarting = orphaned.find((session) => Date.now() - new Date(session.opened_at).getTime() < 20_000);
        if (stillStarting) {
          setSelectedSessionId(stillStarting.id);
          setNotice(`Chrome is still starting for ${stillStarting.account_email}. Wait a few seconds, then retry if it remains offline.`);
          return;
        }
        for (const session of orphaned) {
          await api<Session>(`/api/v1/sessions/${session.id}/close`, { method: "POST" });
        }
        setSessions((current) => current.filter((session) => !orphaned.some((orphan) => orphan.id === session.id)));
      }
      if (live.length) {
        await focusSession(live[0]);
        setNotice(`${live[0].account_email} already has a live browser. It was kept open; starting the remaining selected accounts.`);
      }
      const liveIds = new Set(live.map((session) => session.account_id));
      const pendingAccounts = chosen.filter((account) => !liveIds.has(account.id));
      if (!pendingAccounts.length) return;
      if (pendingAccounts.length === 1) {
        const account = pendingAccounts[0];
        // A verified saved profile can be reused directly. Imported profiles,
        // verification-required accounts, and any other account needing review
        // must enter the visible Gmail login/check flow instead of being
        // rejected as an unauthenticated saved-profile operation.
        const purpose = forceLogin ? "login" : account.profile.status === "SAVED" && account.login_status === "AUTHENTICATED" ? "reuse" : "login";
        const session = await api<Session>(`/api/v1/accounts/${account.id}/sessions`, { method: "POST", body: JSON.stringify({ purpose }) });
        setSessions((current) => [session, ...current.filter((row) => row.account_id !== account.id)]);
        setSelectedSessionId(session.id);
        setNotice(forceLogin ? `Login check started for ${account.email}. Complete any sign-in or verification steps in the browser.` : `Browser session started for ${account.email}.`);
      } else {
        const created = await api<LoginJob>("/api/v1/login-jobs", { method: "POST", body: JSON.stringify({ group_id: null, account_ids: pendingAccounts.map((account) => account.id), concurrency_limit: concurrency, force_login: forceLogin }) });
        const started = await api<LoginJob>(`/api/v1/login-jobs/${created.id}/start`, { method: "POST" });
        setJob(started);
        const pendingIds = new Set(pendingAccounts.map((account) => account.id));
        const deadline = Date.now() + 15_000;
        let opened = await loadSessions();
        const livePendingCount = () => opened.filter((session) => pendingIds.has(session.account_id) && session.browser_alive).length;
        while (livePendingCount() < pendingAccounts.length && Date.now() < deadline) {
          await new Promise((resolve) => window.setTimeout(resolve, 500));
          opened = await loadSessions();
        }
        const target = opened.find((session) => pendingIds.has(session.account_id) && session.browser_alive);
        if (target) {
          try { await focusSession(target); }
          catch { setSelectedSessionId(target.id); }
        }
        const liveCount = livePendingCount();
        setNotice(liveCount
          ? `${forceLogin ? "Login check" : "Batch"} dispatched for ${pendingAccounts.length} accounts; ${liveCount} of ${pendingAccounts.length} browser windows are open. Select any account tab to switch windows.`
          : `${forceLogin ? "Login check" : "Batch"} dispatched for ${pendingAccounts.length} accounts, but no Chrome windows have appeared yet. Check batch progress and retry failed accounts.`);
      }
      if (!forceLogin) setSelectedIds([]);
      onRefresh();
    } catch (requestError) { setError((requestError as Error).message || "The selected browser sessions could not be started."); }
    finally { setLaunching(false); }
  };
  const runChosenOperation = async (requestedOperation: RegistryAction = operation) => {
    if (!operationTargets.length) { setError(scope === "selected" ? "Select one or more accounts first." : "No accounts match the current group and filters."); return; }
    if (requestedOperation === "login") { await startAccounts(operationTargets); return; }
    const requiresAuthenticated = ["contacts", "security", "labels", "apps", "mail", "addons", "alerts", "preferences"].includes(requestedOperation);
    if (requiresAuthenticated && !canRunDesktopOperation(requestedOperation, operationTargets)) {
      setError("This operation requires authenticated accounts. Filter to Authenticated or adjust the selection, then retry.");
      return;
    }
    setError(""); setNotice("");
    onRunOperation(requestedOperation, operationTargets);
  };
  const closeSelectedBrowsers = async () => {
    if (!bulkCloseConfirmation || !selectedOpenSessions.length || bulkClosing) return;
    const targets = [...selectedOpenSessions];
    setBulkClosing(true); setError(""); setNotice("");
    setBulkCloseProgress({ completed: 0, total: targets.length });
    let closed = 0;
    const failures: string[] = [];
    try {
      for (const session of targets) {
        try {
          await api<Session>(`/api/v1/sessions/${session.id}/close`, { method: "POST" });
          closed += 1;
        } catch (requestError) {
          failures.push(`${session.account_email}: ${(requestError as Error).message || "close failed"}`);
        }
        setBulkCloseProgress((current) => ({ ...current, completed: current.completed + 1 }));
      }
      setBulkCloseConfirmation(false);
      if (failures.length) {
        setError(`Closed ${closed} of ${targets.length} selected browser${targets.length === 1 ? "" : "s"}. ${failures.slice(0, 3).join("; ")}${failures.length > 3 ? `; and ${failures.length - 3} more` : ""}`);
      } else {
        setNotice(`Closed all ${closed} selected browser${closed === 1 ? "" : "s"}.`);
      }
      try { await loadSessions(); onRefresh(); }
      catch (requestError) { setError((requestError as Error).message || "The browser list could not refresh after closing sessions."); }
    } finally {
      setBulkClosing(false);
      setBulkCloseProgress({ completed: 0, total: 0 });
    }
  };
  const selectAll = () => setSelectedIds((current) => {
    const visibleIds = new Set(filteredAccounts.map((account) => account.id));
    const allVisibleSelected = filteredAccounts.length > 0 && filteredAccounts.every((account) => current.includes(account.id));
    return allVisibleSelected ? current.filter((id) => !visibleIds.has(id)) : [...new Set([...current, ...visibleIds])];
  });

  const toggleAccount = (accountId: string) => setSelectedIds((current) => current.includes(accountId) ? current.filter((id) => id !== accountId) : [...current, accountId]);
  const cancelJob = async () => {
    if (!job) return;
    setLaunching(true); setError("");
    try { setJob(await api<LoginJob>(`/api/v1/login-jobs/${job.id}/cancel`, { method: "POST" })); await loadSessions(); }
    catch (requestError) { setError((requestError as Error).message || "The batch could not be stopped."); }
    finally { setLaunching(false); }
  };

  const currentGroup = groups.find((group) => group.id === groupId);
  const jobCompleted = job?.items.filter((item) => ["SUCCEEDED", "ATTENTION", "FAILED", "CANCELLED"].includes(item.state)).length ?? 0;
  return <div className="browser-desktop-page">
    <WorkspacePageHeader eyebrow="SERVER BROWSER" title="Browser desktop" description="Manage account sessions, run scoped operations, and switch between live Chrome windows." icon={<Monitor size={20} />} action={<><button type="button" className="button secondary small desktop-nav-toggle" onClick={onToggleNavigation} aria-label={navHidden ? "Expand application navigation" : "Collapse application navigation"} title={navHidden ? "Expand application navigation" : "Collapse application navigation"}>{navHidden ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}{navHidden ? "Expand navigation" : "Collapse navigation"}</button><span className={`desktop-state ${enabled ? "ready" : enabled === false ? "off" : "unknown"}`}><i />{enabled ? "Desktop online" : enabled === false ? "Desktop unavailable" : "Checking"}</span></>} />
    {error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={() => { setError(""); void Promise.all([loadAccounts(), loadSessions()]).catch((requestError) => setError((requestError as Error).message)); }}>Retry</button></div>}
    {notice && <div className="desktop-notice" role="status"><Check size={16} /><span>{notice}</span><button type="button" className="icon-button" aria-label="Dismiss message" onClick={() => setNotice("")}><X size={15} /></button></div>}
    {enabled && <section className={`desktop-workspace${accountRailCollapsed ? " accounts-collapsed" : ""}${inspectorOpen ? " inspector-open" : ""}`} style={{ "--desktop-inspector-width": `${inspectorWidth}px` } as React.CSSProperties} aria-label="Browser desktop workspace">
      <aside className="desktop-account-rail" aria-label="Account launcher">
        {accountRailCollapsed ? <div className="desktop-rail-collapsed"><button type="button" className="icon-button" aria-label="Expand account launcher" title="Expand account launcher" onClick={() => setAccountRailCollapsed(false)}><PanelLeftOpen size={17} /></button><span className="desktop-rail-count" title={`${currentGroup?.account_count ?? accounts.length} accounts in scope`}><UsersRound size={15} />{currentGroup?.account_count ?? accounts.length}</span><span className="desktop-rail-selected" title={`${selectedIds.length} selected`}>{selectedIds.length}</span></div> : <>
        <div className="desktop-rail-heading"><div><span className="detail-label">ACCOUNT SCOPE</span><h2>Launch sessions</h2></div><span className="panel-count">{currentGroup?.account_count ?? accounts.length}</span><button type="button" className="icon-button desktop-rail-collapse" aria-label="Collapse account launcher" title="Collapse account launcher" onClick={() => setAccountRailCollapsed(true)}><PanelLeftClose size={16} /></button></div>
        <label className="desktop-group-select"><span>Account group</span><select aria-label="Choose an account group" value={groupId} onChange={(event) => { setGroupId(event.target.value); window.localStorage.setItem("northstar-desktop-group", event.target.value); setSelectedIds([]); setJob(null); setError(""); }}><option value="all">All groups</option>{groups.map((group) => <option value={group.id} key={group.id}>{group.name} · {group.account_count}</option>)}</select></label>
        <div className="desktop-selection-tools"><label className="desktop-search"><Search size={15} /><input aria-label="Search group accounts" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Find an account" />{query && <button type="button" aria-label="Clear account search" onClick={() => setQuery("")}><X size={13} /></button>}</label><button type="button" className="button secondary small desktop-select-all" onClick={selectAll} disabled={!filteredAccounts.length} aria-pressed={filteredAccounts.length > 0 && filteredAccounts.every((account) => selectedIds.includes(account.id))}>{filteredAccounts.length > 0 && filteredAccounts.every((account) => selectedIds.includes(account.id)) ? "Clear selection" : `Select all ${filteredAccounts.length}`}</button></div>
        <div className="desktop-account-filters" role="group" aria-label="Filter accounts by status">{([{ value: "ALL", label: "All" }, { value: "AUTHENTICATED", label: "Authenticated" }, { value: "VERIFICATION_REQUIRED", label: "Verification" }, { value: "NEVER_LOGGED_IN", label: "Never logged in" }, { value: "BROWSER_OPEN", label: "Browser open" }] as const).map((filter) => <button type="button" key={filter.value} aria-pressed={statusFilter === filter.value} className={statusFilter === filter.value ? "active" : ""} onClick={() => setStatusFilter(filter.value)}>{filter.label}<span>{statusCounts[filter.value]}</span></button>)}</div>
        <div className="desktop-list-meta"><span>Showing {displayedAccounts.length} of {filteredAccounts.length}</span><span>{visibleSelectedIds.size} selected here</span></div>
        <div className="desktop-account-list" aria-label="Accounts in selected group">
          {loading && !accounts.length ? <div className="desktop-list-state"><span className="spinner" /> Loading accounts…</div> : displayedAccounts.map((account) => {
            const session = activeByAccount.get(account.id);
            return <div className={`desktop-account-row ${selectedIds.includes(account.id) ? "selected" : ""}`} key={account.id}>
              <input type="checkbox" checked={selectedIds.includes(account.id)} onChange={() => toggleAccount(account.id)} aria-label={`Select ${account.email}`} />
              <span className={`status-dot ${session?.browser_alive ? "teal" : session ? "amber" : "neutral"}`} />
              <span className="desktop-account-copy"><strong>{account.email}</strong><small>{session ? session.browser_alive ? "Browser open" : "Session needs review" : account.profile.status === "SAVED" ? "Saved profile" : labelStatus(account.login_status)}</small></span>
              {session && <button type="button" className="desktop-account-focus" aria-label={session.browser_alive ? `Focus browser for ${account.email}` : `Review session for ${account.email}`} onClick={(event) => { event.preventDefault(); if (session.browser_alive) void focusSession(session); else onOpenSession(session); }}><ArrowRight size={14} /></button>}
            </div>;
          })}
          {!loading && !filteredAccounts.length && <div className="desktop-list-state">{accounts.length ? "No accounts match this search." : "This group has no accounts."}</div>}
          {displayedAccounts.length < filteredAccounts.length && <button type="button" className="desktop-load-more" onClick={() => setVisibleAccountLimit((limit) => Math.min(filteredAccounts.length, limit + 100))}>Load 100 more · {filteredAccounts.length - displayedAccounts.length} remaining</button>}
        </div>
        <div className="desktop-launch-controls">{(selectedOpenSessions.length > 0 || bulkClosing) && <section className="desktop-bulk-close" aria-label="Close selected browser sessions"><div><strong>{bulkClosing ? `Closing selected browsers · ${bulkCloseProgress.completed} of ${bulkCloseProgress.total}` : "Open browsers in selection"}</strong><span>{bulkClosing ? "Closing each selected window safely in sequence." : `${selectedOpenSessions.length} selected account${selectedOpenSessions.length === 1 ? "" : "s"} have a live browser.`}</span></div>{bulkCloseConfirmation ? <><p>Close Chrome for these selected accounts? Other browser windows stay open.</p><div><button type="button" className="button secondary small" onClick={() => setBulkCloseConfirmation(false)} disabled={bulkClosing}>Cancel</button><button type="button" className="button danger small" onClick={() => void closeSelectedBrowsers()} disabled={bulkClosing} aria-busy={bulkClosing}>{bulkClosing ? <span className="button-spinner" /> : <Power size={13} />}{bulkClosing ? `Closing ${bulkCloseProgress.completed}/${bulkCloseProgress.total}…` : `Confirm close (${selectedOpenSessions.length})`}</button></div></> : <button type="button" className="button danger small" onClick={() => setBulkCloseConfirmation(true)}><Power size={14} />Close selected browsers ({selectedOpenSessions.length})</button>}</section>}</div>
        <div className="desktop-launch-controls desktop-start-controls">
          <label className="desktop-start-concurrency">
            <span>Sessions at a time</span>
            <select aria-label="Sessions at a time" value={concurrency} onChange={(event) => setConcurrency(Math.max(1, Math.min(maxConcurrency, Number(event.target.value))))}>
              {Array.from({ length: maxConcurrency }, (_, index) => index + 1).map((value) => <option key={value} value={value}>{value} concurrent {value === 1 ? "session" : "sessions"}</option>)}
            </select>
          </label>
          <button type="button" className="button primary desktop-run-operation" onClick={() => void startAccounts(launchTargets)} disabled={launching || !launchTargets.length} aria-busy={launching} title={launchTargets.length ? `Open browsers for ${launchTargets.length} checked account${launchTargets.length === 1 ? "" : "s"}` : "Select accounts from the left list to start browser sessions."}>
            {launching ? <span className="button-spinner" /> : <Play size={15} />}
            {launching ? "Starting…" : "Start " + launchTargets.length + " session" + (launchTargets.length === 1 ? "" : "s")}
          </button>
        </div>
        </>}
      </aside>
      <div className="desktop-main">
        <section className="desktop-panel">
          <div className="desktop-viewer-toolbar"><div className="desktop-viewer-label"><span className="desktop-live-mark" /><div><span className="detail-label">LIVE SERVER DESKTOP</span><strong>{liveBrowserCount} live {liveBrowserCount === 1 ? "browser" : "browsers"}{currentGroup ? ` · ${currentGroup.name}` : ""}</strong></div></div><div className="desktop-viewer-actions"><button type="button" className="button secondary small desktop-inspector-toggle" aria-pressed={inspectorOpen} onClick={() => setInspectorOpen((open) => !open)}>{inspectorOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}{inspectorOpen ? "Hide details" : "Show details"}</button><div className="desktop-stop-all-control"><button type="button" className="button danger small" onClick={() => setCloseAllConfirmation((open) => !open)} aria-expanded={closeAllConfirmation} aria-haspopup="dialog" disabled={liveBrowserCount === 0}><Power size={14} />Close all ({liveBrowserCount})</button>{closeAllConfirmation && <div className="desktop-stop-all-confirm" role="alertdialog" aria-label="Confirm closing all active sessions"><strong>Close all {liveBrowserCount} active sessions?</strong><span>This closes your open browser sessions across groups.</span><div><button type="button" className="button secondary small" onClick={() => setCloseAllConfirmation(false)}>Cancel</button><button type="button" className="button danger small" onClick={() => { setCloseAllConfirmation(false); onConfirmStopAll(); }}>Confirm close</button></div></div>}</div><button type="button" className="button secondary small" onClick={() => void loadSessions()} aria-label="Refresh browser sessions"><RefreshCw size={14} /> Refresh</button><button type="button" className="button secondary small" onClick={() => void viewerRef.current?.requestFullscreen?.()}><Maximize2 size={14} /> Full screen</button></div></div>
          {sessions.length > 0 && <div className="desktop-session-switcher" role="group" aria-label="Open browser windows">{sessions.map((session) => <button type="button" aria-pressed={selectedSession?.id === session.id} className={`desktop-session-tab ${selectedSession?.id === session.id ? "active" : ""}`} key={session.id} onClick={() => void focusSession(session)} disabled={focusing === session.id} title={`${session.account_email} · ${session.browser_alive ? "Chrome running" : "Chrome not detected"}`}><span className={`status-dot ${session.browser_alive ? "teal" : "amber"}`} /><span>{session.account_email}</span>{focusing === session.id && <span className="button-spinner" />}</button>)}</div>}
          <div className="desktop-workarea"><iframe ref={viewerRef} title="Northstar live browser desktop" src="/desktop/vnc.html?autoconnect=1&resize=scale&path=websockify&show_dot=0" allow="clipboard-read; clipboard-write; fullscreen" /></div>
          <div className="desktop-viewer-footer"><span>{selectedSession ? <><strong>{selectedSession.account_email}</strong> · {selectedSession.browser_alive ? "Window selected" : "No live Chrome process detected"}</> : "No account window selected"}</span><span>Use the tabs above to switch windows. VNC keyboard and clipboard controls remain available in the viewer toolbar.</span></div>
        </section>
        {job && <section className="desktop-job-panel" aria-live="polite"><div className="desktop-job-heading"><div><span className="detail-label">SESSION QUEUE</span><h2>{job.status === "COMPLETED" ? "Batch complete" : job.status === "CANCELLED" ? "Batch stopped" : "Opening account browsers"}</h2><p>{job.running_count} open · {job.queued_count} queued · concurrency {job.concurrency_limit}</p></div><div className="desktop-job-actions">{["RUNNING", "QUEUED", "PAUSED"].includes(job.status) && <button type="button" className="button danger small" disabled={launching} onClick={() => void cancelJob()}><X size={14} /> Stop queue</button>}<button type="button" className="icon-button" aria-label="Dismiss batch status" onClick={() => setJob(null)}><X size={15} /></button></div></div><div className="desktop-job-progress"><span style={{ width: `${job.total_count ? Math.min(100, (jobCompleted / job.total_count) * 100) : 0}%` }} /></div><div className="desktop-job-summary"><span>{jobCompleted} of {job.total_count} resolved</span><strong>{job.running_count} running now</strong></div>{job.items.some((item) => item.state === "FAILED" || item.state === "ATTENTION") && <div className="desktop-job-issues">{job.items.filter((item) => item.state === "FAILED" || item.state === "ATTENTION").slice(0, 5).map((item) => <button type="button" key={item.id} onClick={() => { const session = sessions.find((row) => row.id === item.session_id); if (session) onOpenSession(session); }}><span className="status-dot amber" /><span><strong>{item.account_email}</strong><small>{item.paused_reason || item.last_error || labelStatus(item.state)}</small></span></button>)}</div>}</section>}
      </div>
      {inspectorOpen && <aside className="desktop-inspector" aria-label="Browser desktop inspector">
        <div className="desktop-inspector-heading"><div><span className="detail-label">INSPECTOR</span><h2>Operations &amp; account</h2></div><button type="button" className="icon-button" aria-label="Hide inspector" onClick={() => setInspectorOpen(false)}><X size={15} /></button></div>
        <label className="desktop-inspector-resize">Inspector width <input type="range" min="230" max="380" step="10" value={inspectorWidth} onChange={(event) => setInspectorWidth(Number(event.target.value))} aria-label="Inspector panel width" /><span>{inspectorWidth}px</span></label>
        <section className="desktop-inspector-operation" aria-label="Choose an operation for this scope">
          <div className="desktop-operation-heading"><div><span className="detail-label">{scope === "selected" ? "SELECTED ACCOUNTS" : "FILTERED GROUP SCOPE"}</span><strong>Choose an operation for this scope</strong></div><span>{operationTargets.length} {operationTargets.length === 1 ? "account" : "accounts"}</span></div>
          <label className="desktop-operation-row" htmlFor="desktop-scope"><span>Apply to</span><select id="desktop-scope" value={scope} onChange={(event) => setScope(event.target.value as "selected" | "filtered")}><option value="selected">Selected accounts ({visibleSelectedIds.size})</option><option value="filtered">All filtered accounts ({filteredAccounts.length})</option></select></label>
          <div className="desktop-inspector-operation-grid" role="group" aria-label="Available account operations">{registryActions.map((action) => {
            const disabled = !operationTargets.length || (["credentials", "upload_profile"].includes(action.kind) && operationTargets.length !== 1);
            return <button key={action.kind} type="button" className={`desktop-inspector-operation-button${operation === action.kind ? " active" : ""}${action.tone ? ` ${action.tone}` : ""}`} aria-pressed={operation === action.kind} disabled={disabled || launching} title={action.kind === "credentials" && operationTargets.length !== 1 ? "Credentials require exactly one account." : action.kind === "login" ? `Run the login check and sign-in flow for ${operationTargets.length} scoped account${operationTargets.length === 1 ? "" : "s"}.` : action.label} onClick={() => { setOperation(action.kind); if (action.kind === "login") void startAccounts(operationTargets, true); else void runChosenOperation(action.kind); }}><span>{action.icon}</span>{action.shortLabel}</button>;
          })}</div>
          <div className="desktop-operation-hint" role="status">{operation === "login" ? "Login starts browser sessions for the scope selected above. The left Start button launches checked accounts." : "Choose an operation above to open its existing setup in this inspector."}</div>
          <button type="button" className="button secondary small desktop-inspector-compact" onClick={() => onCompactProfiles(operationTargets)} disabled={!operationTargets.length} title={operationTargets.length === 1 ? `Compact ${operationTargets[0].email}'s closed profile` : `Compact ${operationTargets.length} selected profiles`}><Archive size={14} />Compact {operationTargets.length === 1 ? "profile" : "profiles"}</button>
          <small className="desktop-operation-help">Actions use only the accounts shown in this scope. Configure forms and confirmations stay in this panel.</small>
        </section>
        <div className="desktop-inspector-operation-host" ref={operationHostRef} />
        {inspectedAccount && isAdmin && ["SAVED", "IMPORTED"].includes(inspectedAccount.profile.status) && <div className="desktop-inspector-profile-tools"><button type="button" className="button secondary small desktop-inspector-export" onClick={() => void downloadInspectedProfile()} disabled={profileZipBusy || Boolean(inspectedSession)} title={inspectedSession ? "Close this account's browser before creating a consistent profile ZIP." : "Download this account's complete browser profile, including cookies and site data."} aria-busy={profileZipBusy}>{profileZipBusy ? <span className="button-spinner" /> : <Download size={14} />}{profileZipBusy ? "Creating profile ZIP…" : "Download profile ZIP"}</button></div>}
      </aside>}
    </section>}
    {enabled === false && <section className="desktop-setup-note"><Monitor size={21} /><div><strong>The server desktop is not enabled yet</strong><span>The Ubuntu installer configures the virtual display, window manager, and protected browser viewer.</span></div></section>}
    <div className="desktop-security-note"><ShieldCheck size={15} /><span>Your browser desktop and sessions are private to your signed-in operator. Closing this page does not close your sessions; account access still follows your assigned groups.</span></div>
  </div>;
}

function ProfilesPage({ refreshKey, onRefresh, onOpenAccount }: { refreshKey: number; onRefresh: () => void; onOpenAccount: (accountId: string) => void }) {
  const [profiles, setProfiles] = useState<BrowserProfile[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const debouncedQuery = useDebouncedValue(query);
  useEffect(() => { let cancelled = false; setLoading(true); setError(""); const params = new URLSearchParams({ limit: "500" }); if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim()); if (status !== "ALL") params.set("status", status); api<BrowserProfile[]>(`/api/v1/browser-profiles?${params.toString()}`).then((data) => { if (!cancelled) setProfiles(data); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Profiles are unavailable."); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [refreshKey, debouncedQuery, status]);
  const savedCount = profiles.filter((profile) => profile.authenticated_at).length;
  const openCount = profiles.filter((profile) => profile.status === "OPEN" || profile.active_session_id).length;
  return <div className="infra-page"><InfrastructureHeader eyebrow="PROFILE STORE" title="Browser profiles" description="Inspect persistent profile ownership, storage references, locks, and authentication evidence." icon={<Layers3 size={20} />} onRefresh={onRefresh} loading={loading} /><section className="summary-grid" aria-label="Browser profile summary"><SummaryCard label="Profiles" value={profiles.length.toString()} note="Organization inventory" icon={<Layers3 size={17} />} /><SummaryCard label="Authenticated" value={savedCount.toString()} note="Safe to reopen" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Open or locked" value={openCount.toString()} note="Needs runtime coordination" icon={<LockKeyhole size={17} />} tone="amber" /><SummaryCard label="Storage policy" value="Persistent" note="Account-owned directory" icon={<Database size={17} />} /></section><section className="infra-panel"><div className="panel-header"><div><h2>Profile inventory</h2><p>Storage references are metadata only; profile contents remain on the server.</p></div><span className="inventory-count">{loading ? "Refreshing…" : `${profiles.length} visible`}</span></div><InventoryToolbar value={query} onChange={setQuery} onClear={() => setQuery("")} placeholder="Search by account, profile ID, or storage reference" status={status} onStatusChange={setStatus} statuses={[{ value: "RESERVED", label: "Reserved" }, { value: "OPEN", label: "Open" }, { value: "SAVED", label: "Saved" }, { value: "ARCHIVED", label: "Archived" }]} loading={loading} onRefresh={onRefresh} />{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={onRefresh}>Retry</button></div>}{!loading && !profiles.length ? <div className="table-state empty-state"><div className="empty-icon"><Layers3 size={21} /></div><strong>No profiles match this view</strong><span>Profiles are created at account onboarding and become saved after connected evidence.</span></div> : <div className="table-scroll"><table className="infra-table"><caption className="sr-only">Persistent browser profile inventory</caption><thead><tr><th>Account</th><th>Profile status</th><th>Storage reference</th><th>Lock</th><th>Last opened</th><th>Authentication</th></tr></thead><tbody>{profiles.map((profile) => <tr key={profile.id}><td><button type="button" className="infra-link" onClick={() => onOpenAccount(profile.account_id)}><strong>{profile.account_email}</strong><small className="mono">{profile.id}</small></button></td><td><InventoryStatus status={profile.status} /></td><td><span className="mono profile-reference">{profile.storage_reference}</span></td><td><InventoryStatus status={profile.lock_state} />{profile.active_session_id && <small className="table-subtext">Session active</small>}</td><td><span className="table-subtext">{formatDateTime(profile.last_opened_at)}</span><small className="table-subtext">Closed {formatDateTime(profile.last_closed_at)}</small></td><td>{profile.authenticated_at ? <span className="auth-evidence"><Check size={14} />{formatDateTime(profile.authenticated_at)}</span> : <span className="table-subtext">No authenticated evidence</span>}</td></tr>)}</tbody></table></div>}</section><footer className="page-footer"><span>Northstar Operations</span><span>Profile store · persistent metadata</span></footer></div>;
}

function ProxiesPage({ groups, refreshKey, onRefresh, onOpenAccount }: { groups: Group[]; refreshKey: number; onRefresh: () => void; onOpenAccount: (accountId: string) => void }) {
  const [proxies, setProxies] = useState<ProxyRecord[]>([]);
  const [savedProxyGroups, setSavedProxyGroups] = useState<ProxyGroupList[]>([]);
  const [ovhProxyLinks, setOvhProxyLinks] = useState<OvhProxyGroupLink[]>([]);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [modal, setModal] = useState<"edit" | "assign" | "remove" | null>(null);
  const [bulkAssignOpen, setBulkAssignOpen] = useState(false);
  const [proxyGroupModalOpen, setProxyGroupModalOpen] = useState(false);
  const [proxyGroupsManagerOpen, setProxyGroupsManagerOpen] = useState(false);
  const [testPending, setTestPending] = useState(false);
  const [testNotice, setTestNotice] = useState<{ message: string; tone: "success" | "error" | "progress"; completed: number; total: number } | null>(null);
  const [testingIds, setTestingIds] = useState<string[]>([]);
  const [groupId, setGroupId] = useState("all");
  const [proxyGroupFilter, setProxyGroupFilter] = useState("all");
  const debouncedQuery = useDebouncedValue(query);
  useEffect(() => { setSelectedIds([]); }, [debouncedQuery, status, groupId, proxyGroupFilter]);
  useEffect(() => { let cancelled = false; setLoading(true); setError(""); const params = new URLSearchParams(); if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim()); if (status !== "ALL") params.set("status", status); if (groupId !== "all") params.set("group_id", groupId); api<ProxyRecord[]>(`/api/v1/proxies${params.toString() ? `?${params.toString()}` : ""}`).then((data) => { if (!cancelled) setProxies(data); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Proxies are unavailable."); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [refreshKey, debouncedQuery, status, groupId]);
  useEffect(() => { let cancelled = false; api<ProxyGroupList[]>("/api/v1/proxies/groups").then((result) => { if (!cancelled) setSavedProxyGroups(result); }).catch(() => { if (!cancelled) setSavedProxyGroups([]); }); return () => { cancelled = true; }; }, [refreshKey]);
  useEffect(() => { let cancelled = false; const refreshOvhLinks = () => { if (document.visibilityState !== "visible") return; api<{ items: OvhProxyGroupLink[] }>("/api/v1/infrastructure/ovh/proxy-groups").then((result) => { if (!cancelled) setOvhProxyLinks(result.items); }).catch(() => { if (!cancelled) setOvhProxyLinks([]); }); }; refreshOvhLinks(); const timer = window.setInterval(refreshOvhLinks, 60000); return () => { cancelled = true; window.clearInterval(timer); }; }, [refreshKey]);
  const assignedAccounts = proxies.reduce((sum, proxy) => sum + proxy.account_count, 0);
  const authenticatedAccounts = proxies.reduce((sum, proxy) => sum + proxy.authenticated_count, 0);
  const proxyGroups = [...new Set([...savedProxyGroups.map((group) => group.name), ...proxies.map((proxy) => proxy.proxy_group).filter((name): name is string => !!name)])].sort((a, b) => a.localeCompare(b));
  const visibleProxies = proxyGroupFilter === "all" ? proxies : proxyGroupFilter === "ungrouped" ? proxies.filter((proxy) => !proxy.proxy_group) : proxies.filter((proxy) => proxy.proxy_group === proxyGroupFilter);
  const visibleOvhLinks = proxyGroupFilter === "all" ? ovhProxyLinks : proxyGroupFilter === "ungrouped" ? [] : ovhProxyLinks.filter((link) => link.proxy_group === proxyGroupFilter);
  const startedOvhCount = visibleOvhLinks.filter((link) => ["RUNNING", "ACTIVE", "ENABLED", "STARTED"].includes(link.status.trim().toUpperCase())).length;
  const stoppedOvhCount = visibleOvhLinks.filter((link) => ["STOPPED", "OFF", "SHUTOFF", "HALTED", "SHELVED", "SUSPENDED"].includes(link.status.trim().toUpperCase())).length;
  const selectedProxies = proxies.filter((proxy) => selectedIds.includes(proxy.id));
  const allSelected = visibleProxies.length > 0 && visibleProxies.every((proxy) => selectedIds.includes(proxy.id));
  const toggleAll = () => setSelectedIds(allSelected ? selectedIds.filter((id) => !visibleProxies.some((proxy) => proxy.id === id)) : [...new Set([...selectedIds, ...visibleProxies.map((proxy) => proxy.id)])]);
  const toggleOne = (proxyId: string) => setSelectedIds((current) => current.includes(proxyId) ? current.filter((id) => id !== proxyId) : [...current, proxyId]);
  const runTest = async () => {
    const proxyIds = selectedProxies.map((proxy) => proxy.id);
    if (!proxyIds.length || testPending) return;
    let completed = 0;
    let healthy = 0;
    let attention = 0;
    const active = new Set<string>();
    const progressMessage = () => `Bulk proxy test in progress · ${completed} of ${proxyIds.length} selected endpoints tested · ${active.size} checking now · ${healthy} healthy${attention ? ` · ${attention} need attention` : ""}.`;
    setTestPending(true);
    setTestNotice({ message: progressMessage(), tone: "progress", completed, total: proxyIds.length });
    try {
      const failure = await runProxyTestQueue(
        proxyIds,
        async (proxyId) => {
          const results = await api<ProxyTestResponse[]>("/api/v1/proxies/test", { method: "POST", body: JSON.stringify({ proxy_ids: [proxyId] }) });
          if (results.length !== 1) throw new Error("The proxy test returned no endpoint result.");
          return results[0];
        },
        (proxyId) => {
          active.add(proxyId);
          setTestingIds([...active]);
          setTestNotice({ message: progressMessage(), tone: "progress", completed, total: proxyIds.length });
        },
        (result) => {
          active.delete(result.proxy_id);
          completed += 1;
          if (result.status === "HEALTHY") healthy += 1;
          else attention += 1;
          setTestingIds([...active]);
          setProxies((current) => current.map((proxy) => proxy.id === result.proxy_id ? { ...proxy, test_status: result.status, test_error: result.error, public_ip: result.public_ip, provider: result.provider, country: result.country, city: result.city, latency_ms: result.latency_ms, last_tested_at: result.tested_at } : proxy));
          setTestNotice({ message: progressMessage(), tone: "progress", completed, total: proxyIds.length });
        },
        (proxyId) => {
          active.delete(proxyId);
          setTestingIds([...active]);
        },
      );
      if (failure) throw failure;
      setTestNotice({ message: `Bulk proxy test complete · ${completed} of ${proxyIds.length} selected endpoints tested · ${healthy} healthy${attention ? ` · ${attention} need attention` : ""}.`, tone: attention ? "error" : "success", completed, total: proxyIds.length });
    } catch (requestError) {
      const remaining = proxyIds.length - completed;
      const detail = (requestError as Error).message || "The proxy checks could not be completed.";
      setTestNotice({ message: `Bulk proxy test stopped after ${completed} of ${proxyIds.length} selected endpoints. ${remaining} were not tested. ${detail}`, tone: "error", completed, total: proxyIds.length });
    }
    finally { setTestingIds([]); setTestPending(false); onRefresh(); }
  };
  const proxyContext = (proxy: ProxyRecord) => { const place = [proxy.city, proxy.country].filter(Boolean).join(", "); const network = proxy.provider || "Provider pending"; const ip = proxy.public_ip ? ` · ${proxy.public_ip}` : ""; return proxy.test_status === "NOT_TESTED" ? "Not tested" : proxy.test_status === "HEALTHY" ? `${proxy.latency_ms ?? "—"} ms · ${network}${place ? ` · ${place}` : ""}${ip}` : proxy.test_error || "Proxy check failed"; };
  const proxyGeo = (proxy: ProxyRecord) => proxy.test_status === "HEALTHY" ? [proxy.city, proxy.country].filter(Boolean).join(", ") || "Location unavailable" : proxy.test_status === "NOT_TESTED" ? "Not detected · test endpoint" : "Detection unavailable";
  return <div className="infra-page">
    <InfrastructureHeader eyebrow="NETWORK BOUNDARY" title="Proxies" description="Review proxy assignments, group ownership, and the OVH power state behind each group." icon={<KeyRound size={20} />} onRefresh={onRefresh} loading={loading} onManageProxyGroups={() => setProxyGroupsManagerOpen(true)} />
    <section className="summary-grid" aria-label="Proxy summary">
      <SummaryCard label="Proxy endpoints" value={proxies.length.toString()} note="Distinct assigned targets" icon={<KeyRound size={17} />} />
      <SummaryCard label="Assigned accounts" value={assignedAccounts.toString()} note="Account-scoped mappings" icon={<UsersRound size={17} />} tone="teal" />
      <SummaryCard label="Authenticated accounts" value={authenticatedAccounts.toString()} note="Saved profile evidence" icon={<Check size={17} />} />
      <SummaryCard label="Credential policy" value="Encrypted" note="Never returned by API" icon={<LockKeyhole size={17} />} tone="amber" />
    </section>
    <section className="infra-panel">
      <div className="panel-header"><div><h2>Proxy assignments</h2><p>OVH power state refreshes automatically every minute while this page is visible.</p></div><div className="proxy-panel-controls"><div className="group-context"><span>ACCOUNT GROUP</span><label className="select-wrap"><select aria-label="Filter proxies by account group" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="all">All groups</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count} accounts</option>)}</select><ChevronDown size={15} /></label></div><span className="inventory-count">{loading ? "Refreshing…" : `${proxies.length} endpoints`}</span></div></div>
      <div className="proxy-group-filter"><label htmlFor="proxy-group-filter">Proxy group</label><select id="proxy-group-filter" value={proxyGroupFilter} onChange={(event) => setProxyGroupFilter(event.target.value)}><option value="all">All proxy groups</option><option value="ungrouped">Ungrouped</option>{proxyGroups.map((name) => <option key={name} value={name}>{name}</option>)}</select><span>{visibleProxies.length} shown</span>{visibleOvhLinks.length > 0 && <div className="proxy-ovh-summary" aria-label="Linked OVH instance power state"><strong>OVH power</strong><span className="ovh-power-status started"><span aria-hidden="true" />{startedOvhCount} started</span><span className="ovh-power-status stopped"><span aria-hidden="true" />{stoppedOvhCount} stopped</span></div>}</div>
      <InventoryToolbar value={query} onChange={setQuery} onClear={() => setQuery("")} placeholder="Search by label, region, provider, or account" status={status} onStatusChange={setStatus} statuses={[{ value: "ASSIGNED", label: "Assigned" }, { value: "HEALTHY", label: "Healthy" }, { value: "UNREACHABLE", label: "Unreachable" }, { value: "NOT_TESTED", label: "Not tested" }]} loading={loading} onRefresh={onRefresh} />
      <div className="proxy-action-strip" aria-label="Proxy management actions"><span>{selectedIds.length ? `${selectedIds.length} selected` : "Select endpoints to manage assignments"}</span><div><button type="button" className="button secondary small" disabled={!selectedIds.length || testPending} onClick={runTest}>{testPending && <span className="button-spinner" />}Test selected</button><button type="button" className="button secondary small" disabled={selectedIds.length !== 1 || testPending} onClick={() => setModal("assign")}>Assign accounts</button><button type="button" className="button secondary small" disabled={!selectedIds.length || testPending} onClick={() => setBulkAssignOpen(true)}>Distribute to group</button><button type="button" className="button secondary small" disabled={!selectedIds.length || testPending} onClick={() => setProxyGroupModalOpen(true)}>Manage group</button><button type="button" className="button secondary small" disabled={selectedIds.length !== 1 || testPending} onClick={() => setModal("edit")}>Edit proxy</button><button type="button" className="button danger small" disabled={!selectedIds.length || testPending} onClick={() => setModal("remove")}>Remove</button></div></div>
      {testNotice && <div className={`proxy-test-notice ${testNotice.tone}`} role="status"><Check size={15} /><div className="proxy-test-notice-content"><span>{testNotice.message}</span>{testNotice.tone === "progress" && <progress aria-label="Proxy checks completed" value={testNotice.completed} max={testNotice.total} />}</div>{!testPending && <button type="button" className="icon-button" aria-label="Dismiss proxy test result" onClick={() => setTestNotice(null)}><X size={15} /></button>}</div>}
      {error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={() => setError("")}>Dismiss</button></div>}
      {!loading && !visibleProxies.length ? <div className="table-state empty-state"><div className="empty-icon"><KeyRound size={21} /></div><strong>No proxy endpoints match this group filter</strong><span>Assign a proxy from Add account or Edit metadata to see it here.</span></div> : <div className="table-scroll"><table className="infra-table"><caption className="sr-only">Account proxy assignments and linked OVH power state</caption><thead><tr><th className="select-column"><input type="checkbox" aria-label="Select all proxies" checked={allSelected} onChange={toggleAll} /></th><th>Proxy identity</th><th>Proxy group</th><th>OVH instance / power state</th><th>Assigned region</th><th>Detected geo</th><th>Accounts</th><th>Authenticated</th><th>Status</th><th>Assigned accounts</th></tr></thead><tbody>{visibleProxies.map((proxy) => { const linked = ovhProxyLinks.filter((link) => link.proxy_group === proxy.proxy_group); return <tr key={proxy.id} className={selectedIds.includes(proxy.id) ? "selected-row" : ""}><td className="select-column"><input type="checkbox" aria-label={`Select proxy ${proxy.label || proxy.id}`} checked={selectedIds.includes(proxy.id)} onChange={() => toggleOne(proxy.id)} /></td><td><strong className="mono">{proxy.label || "Unnamed endpoint"}</strong><small className="table-subtext mono">{proxy.id}</small></td><td>{proxy.proxy_group ? <span className="proxy-group-chip">{proxy.proxy_group}</span> : <span className="table-subtext">Ungrouped</span>}</td><td>{linked.length ? <div className="proxy-ovh-links">{linked.map((link) => <span className="proxy-ovh-link" key={link.id}><strong>{link.server_name}</strong><OvhPowerStatus status={link.status} />{link.ip && <small className="table-subtext mono">{link.ip}</small>}</span>)}</div> : <span className="table-subtext">{proxy.proxy_group ? "No OVH instance linked" : "—"}</span>}</td><td><span className="table-subtext">{proxy.region || "Region not set"}</span></td><td><strong className="proxy-geo-value">{proxyGeo(proxy)}</strong><small className="table-subtext">{proxy.test_status === "HEALTHY" ? `${proxy.provider || "Provider unavailable"}${proxy.public_ip ? ` · ${proxy.public_ip}` : ""}` : "Verified by proxy test"}</small></td><td><strong className="metric-number">{proxy.account_count}</strong></td><td><strong className="metric-number">{proxy.authenticated_count}</strong><small className="table-subtext">with saved evidence</small></td><td><InventoryStatus status={testingIds.includes(proxy.id) ? "IN_PROGRESS" : proxy.test_status === "NOT_TESTED" ? proxy.status : proxy.test_status} /><small className="table-subtext">{testingIds.includes(proxy.id) ? "Checking proxy now…" : proxyContext(proxy)}</small></td><td><div className="account-chip-list">{proxy.account_refs.slice(0, 4).map((reference) => <button type="button" className="account-chip" key={reference.id} onClick={() => onOpenAccount(reference.id)}>{reference.email}</button>)}{proxy.account_refs.length > 4 && <span className="table-subtext">+{proxy.account_refs.length - 4} more</span>}</div></td></tr>; })}</tbody></table></div>}
    </section>
    <footer className="page-footer"><span>Northstar Operations</span><span>Proxy inventory · OVH power state · refreshed every minute</span></footer>
    {proxyGroupModalOpen && <ProxyGroupModal proxies={selectedProxies} groupNames={proxyGroups} onClose={() => setProxyGroupModalOpen(false)} onDone={() => { setProxyGroupModalOpen(false); setSelectedIds([]); onRefresh(); }} />}
    {proxyGroupsManagerOpen && <ProxyGroupsManagerModal groups={savedProxyGroups} proxies={proxies} ovhLinks={ovhProxyLinks} onClose={() => setProxyGroupsManagerOpen(false)} onChanged={onRefresh} onManageMembers={(name) => { setProxyGroupFilter(name); setProxyGroupsManagerOpen(false); }} />}
    {bulkAssignOpen && <ProxyDistributionModal proxies={selectedProxies} groups={groups} onClose={() => setBulkAssignOpen(false)} onDone={(result) => { setBulkAssignOpen(false); setSelectedIds([]); setTestNotice({ message: `Proxy distribution complete · ${result.account_count} group accounts assigned across ${selectedProxies.length} endpoints · ${result.changed_count} mappings changed.`, tone: "success", completed: result.account_count, total: result.account_count }); onRefresh(); }} />}
    {modal && selectedProxies[0] && <ProxyManagementModal kind={modal} proxies={modal === "remove" ? selectedProxies : [selectedProxies[0]]} groups={groups} initialGroupId={groupId} onClose={() => setModal(null)} onDone={() => { setModal(null); setSelectedIds([]); onRefresh(); }} onError={(message) => setError(message)} />}
  </div>;
}

function ProxyGroupModal({ proxies, groupNames, onClose, onDone }: { proxies: ProxyRecord[]; groupNames: string[]; onClose: () => void; onDone: () => void }) {
  const current = proxies[0]?.proxy_group ?? "";
  const [choice, setChoice] = useState(current && groupNames.includes(current) ? current : "__new__");
  const [name, setName] = useState(current);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const selectedName = choice === "__ungrouped__" ? null : choice === "__new__" ? name.trim() : choice;
  const save = async () => {
    if (choice === "__new__" && (!name.trim() || name.trim().length > 80)) { setError("Enter a proxy group name up to 80 characters."); return; }
    setPending(true); setError("");
    try {
      await api("/api/v1/proxies/group", { method: "POST", body: JSON.stringify({ proxy_ids: proxies.map((proxy) => proxy.id), group_name: selectedName }) });
      onDone();
    } catch (requestError) { setError((requestError as Error).message || "Could not update proxy group."); }
    finally { setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal proxy-modal" role="dialog" aria-modal="true" aria-labelledby="proxy-group-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROXY ORGANIZATION</div><h2 id="proxy-group-title">Manage proxy group</h2><p>{proxies.length} selected endpoint{proxies.length === 1 ? "" : "s"}</p></div><button type="button" className="icon-button" aria-label="Close proxy group dialog" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="proxy-group-form" className="modal-body" onSubmit={(event) => { event.preventDefault(); void save(); }} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="field"><label htmlFor="proxy-group-choice">Proxy group</label><select id="proxy-group-choice" value={choice} onChange={(event) => setChoice(event.target.value)} disabled={pending}><option value="__new__">Create a new group</option>{groupNames.map((groupName) => <option value={groupName} key={groupName}>{groupName}</option>)}<option value="__ungrouped__">Remove from group</option></select></div>{choice === "__new__" && <div className="field"><label htmlFor="proxy-group-name">New group name</label><input id="proxy-group-name" maxLength={80} value={name} onChange={(event) => setName(event.target.value)} placeholder="e.g. US East" disabled={pending} /></div>}<div className="assignment-list">{proxies.map((proxy) => <div className="assignment-row" key={proxy.id}><span><strong>{proxy.label || proxy.id}</strong><small>Current group: {proxy.proxy_group || "Ungrouped"}</small></span></div>)}</div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="proxy-group-form" className="button primary" disabled={pending || !proxies.length}>{pending ? "Saving group…" : "Save proxy group"}</button></div></section></div>;
}

function ProxyGroupsManagerModal({ groups: initialGroups, proxies, ovhLinks, onClose, onChanged, onManageMembers }: { groups: ProxyGroupList[]; proxies: ProxyRecord[]; ovhLinks: OvhProxyGroupLink[]; onClose: () => void; onChanged: () => void; onManageMembers: (name: string) => void }) {
  const [groups, setGroups] = useState(initialGroups);
  const [editingId, setEditingId] = useState("");
  const [name, setName] = useState("");
  const [deleteId, setDeleteId] = useState("");
  const [pendingId, setPendingId] = useState("");
  const [error, setError] = useState("");
  const updateName = async (group: ProxyGroupList) => {
    const normalized = name.trim().replace(/\s+/g, " ");
    if (!normalized || normalized.length > 80) { setError("Enter a group name up to 80 characters."); return; }
    setPendingId(group.id); setError("");
    try {
      const updated = await api<ProxyGroupList & { renamed?: boolean }>(`/api/v1/proxies/groups/${encodeURIComponent(group.id)}`, { method: "PATCH", body: JSON.stringify({ name: normalized }) });
      setGroups((current) => current.map((row) => row.id === group.id ? { ...updated } : row));
      setEditingId(""); setName(""); onChanged();
    } catch (requestError) { setError((requestError as Error).message || "The group name could not be updated."); }
    finally { setPendingId(""); }
  };
  const deleteGroup = async (group: ProxyGroupList) => {
    setPendingId(group.id); setError("");
    try {
      await api(`/api/v1/proxies/groups/${encodeURIComponent(group.id)}`, { method: "DELETE" });
      setGroups((current) => current.filter((row) => row.id !== group.id)); setDeleteId(""); onChanged();
    } catch (requestError) { setError((requestError as Error).message || "The proxy group could not be deleted."); }
    finally { setPendingId(""); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pendingId) onClose(); }}><section className="modal proxy-modal proxy-groups-manager" role="dialog" aria-modal="true" aria-labelledby="proxy-groups-manager-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROXY ORGANIZATION</div><h2 id="proxy-groups-manager-title">Manage proxy groups</h2><p>Review group contents, rename groups, or change endpoint membership. Account counts follow the selected account group filter.</p></div><button type="button" className="icon-button" aria-label="Close proxy group manager" onClick={onClose} disabled={Boolean(pendingId)}><X size={18} /></button></div><div className="modal-body">{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{groups.length ? <div className="proxy-group-admin-list">{groups.map((group) => { const members = proxies.filter((proxy) => proxy.proxy_group === group.name); const accountCount = members.reduce((count, proxy) => count + proxy.account_count, 0); const authenticatedCount = members.reduce((count, proxy) => count + proxy.authenticated_count, 0); const healthyCount = members.filter((proxy) => proxy.test_status === "HEALTHY").length; const linkedServers = [...new Set(ovhLinks.filter((link) => link.proxy_group === group.name).map((link) => link.server_name))]; return <div className="proxy-group-admin-row" key={group.id}><div className="proxy-group-admin-main">{editingId === group.id ? <form className="proxy-group-rename-form" noValidate onSubmit={(event) => { event.preventDefault(); void updateName(group); }}><label className="sr-only" htmlFor={`proxy-group-rename-${group.id}`}>New name for {group.name}</label><input id={`proxy-group-rename-${group.id}`} autoFocus maxLength={80} value={name} onChange={(event) => setName(event.target.value)} disabled={pendingId === group.id} /><button type="submit" className="button primary small" disabled={pendingId === group.id}>{pendingId === group.id ? "Saving…" : "Save"}</button><button type="button" className="button secondary small" onClick={() => { setEditingId(""); setName(""); setError(""); }} disabled={pendingId === group.id}>Cancel</button></form> : <><strong>{group.name}</strong><small>{group.proxy_count} endpoints · {accountCount} assigned · {authenticatedCount} authenticated · {healthyCount} healthy</small><small>{linkedServers.length ? `OVH: ${linkedServers.join(", ")}` : "No linked OVH instance"}</small></>}</div><div className="proxy-group-admin-actions">{deleteId === group.id ? <><span className="proxy-group-delete-note">Endpoints will be ungrouped; account assignments and proxy records stay intact.</span><button type="button" className="button danger small" onClick={() => void deleteGroup(group)} disabled={pendingId === group.id}>{pendingId === group.id ? "Deleting…" : "Confirm delete"}</button><button type="button" className="button secondary small" onClick={() => setDeleteId("")} disabled={pendingId === group.id}>Cancel</button></> : editingId !== group.id && <><button type="button" className="button secondary small" onClick={() => onManageMembers(group.name)}>Manage endpoints</button><button type="button" className="button secondary small" onClick={() => { setEditingId(group.id); setName(group.name); setDeleteId(""); setError(""); }} aria-label={`Rename ${group.name}`}><Pencil size={14} /> Rename</button><button type="button" className="button danger small" onClick={() => { setDeleteId(group.id); setEditingId(""); setError(""); }} aria-label={`Delete ${group.name}`}><Trash2 size={14} /> Delete</button></>}</div></div>; })}</div> : <div className="workspace-empty compact"><KeyRound size={20} /><strong>No proxy groups yet</strong><span>Create a proxy list or assign an endpoint to a new group first.</span></div>}<div className="security-callout"><ShieldCheck size={17} /><div><strong>Endpoint membership is retained safely</strong><span>Deleting a group clears its group label and linked OVH group metadata only. Proxy credentials and accounts assigned to each endpoint are preserved.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={Boolean(pendingId)}>Done</button></div></section></div>;
}

function ProxyDistributionModal({ proxies, groups, onClose, onDone }: { proxies: ProxyRecord[]; groups: Group[]; onClose: () => void; onDone: (result: { account_count: number; changed_count: number }) => void }) {
  const [groupId, setGroupId] = useState(groups[0]?.id ?? "");
  const [distributionMode, setDistributionMode] = useState<"equal" | "limit">("equal");
  const [perProxy, setPerProxy] = useState("5");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const group = groups.find((item) => item.id === groupId);
  const accountCount = group?.account_count ?? 0;
  const evenBase = proxies.length ? Math.floor(accountCount / proxies.length) : 0;
  const remainder = proxies.length ? accountCount % proxies.length : 0;
  const projectedCounts = proxies.map((_, index) => {
    const evenShare = evenBase + (index < remainder ? 1 : 0);
    return distributionMode === "limit" ? Math.min(evenShare, Math.max(0, Number(perProxy) || 0)) : evenShare;
  });
  const estimatedTotal = projectedCounts.reduce((sum, value) => sum + value, 0);
  const unhealthy = proxies.filter((proxy) => proxy.test_status !== "HEALTHY");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const limit = Number(perProxy);
    if (!groupId || (distributionMode === "limit" && (!Number.isInteger(limit) || limit < 1 || limit > 300))) { setError("Select a group and enter 1–300 accounts per proxy when using a limit."); return; }
    setPending(true); setError("");
    try {
      const result = await api<{ account_count: number; changed_count: number }>("/api/v1/proxies/distribute-group", { method: "POST", body: JSON.stringify({ group_id: groupId, proxy_ids: proxies.map((proxy) => proxy.id), mode: distributionMode, ...(distributionMode === "limit" ? { accounts_per_proxy: limit } : {}) }) });
      onDone(result);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal proxy-modal" role="dialog" aria-modal="true" aria-labelledby="proxy-distribution-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GROUP DISTRIBUTION</div><h2 id="proxy-distribution-title">Distribute proxies to accounts</h2><p>{proxies.length} selected endpoint{proxies.length === 1 ? "" : "s"} · each group account gets one proxy</p></div><button type="button" className="icon-button" aria-label="Close proxy distribution" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="proxy-distribution-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{unhealthy.length > 0 && <div className="form-error" role="status"><XCircle size={17} /><span>Test the selected endpoints first. {unhealthy.length} {unhealthy.length === 1 ? "is" : "are"} not healthy.</span></div>}<div className="form-grid"><div className="field"><label htmlFor="proxy-distribution-group">Account group</label><select id="proxy-distribution-group" value={groupId} onChange={(event) => setGroupId(event.target.value)} disabled={pending}>{groups.map((item) => <option value={item.id} key={item.id}>{item.name} · {item.account_count} accounts</option>)}</select></div><div className="field"><label htmlFor="proxy-distribution-mode">Distribution method</label><select id="proxy-distribution-mode" value={distributionMode} onChange={(event) => setDistributionMode(event.target.value as "equal" | "limit")} disabled={pending}><option value="equal">Spread equally across selected proxies</option><option value="limit">Set a maximum per proxy</option></select></div>{distributionMode === "limit" && <div className="field"><label htmlFor="proxy-distribution-limit">Maximum accounts per proxy</label><input id="proxy-distribution-limit" type="number" min="1" max="300" value={perProxy} onChange={(event) => setPerProxy(event.target.value)} disabled={pending} /></div>}</div><div className="security-callout"><KeyRound size={17} /><div><strong>{accountCount} accounts in scope · {estimatedTotal} estimated assignments</strong><span>This operation only changes accounts in {group?.name ?? "the selected group"}. Each account receives exactly one selected proxy. {distributionMode === "equal" ? "The projected split is shown below; some endpoints may receive one extra account when the total does not divide evenly." : `The per-proxy limit is ${Number(perProxy) || 0}, including existing assignments outside this group. The estimate applies the limit to this group only; the server checks actual capacity before changing anything.`} Active sessions block the operation.</span></div></div><div className="assignment-list">{proxies.map((proxy, index) => <div className="assignment-row" key={proxy.id}><span><strong>{proxy.label || proxy.id}</strong><small>{projectedCounts[index]} projected from this group · {proxy.account_count} currently in view · {labelStatus(proxy.test_status)}</small></span></div>)}</div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="proxy-distribution-form" className="button primary" disabled={pending || !!unhealthy.length || !groupId}>{pending ? "Assigning…" : "Distribute accounts"}</button></div></section></div>;
}

function credentialAccountFor(record: CredentialRecord): Account {
  return { id: record.id, email: record.email, group_id: record.group_id, group_name: record.group_name, lifecycle_status: "ACTIVE", readiness_status: record.password_present ? "READY_FOR_LOGIN" : "SETUP_REQUIRED", login_status: record.login_status, gmail_status: "UNKNOWN", secret_status: record.password_present ? "STORED_ENCRYPTED" : "MISSING", recovery_masked: record.recovery_masked ?? "", two_fa_configured: record.two_fa_configured, proxy: { id: null, label: null, region: null, status: "UNASSIGNED" }, profile: { id: null, storage_reference: null, status: record.profile_status }, created_at: record.updated_at, updated_at: record.updated_at };
}

function CredentialsPage({ groups, refreshKey, onRefresh, onOpenAccount, onEditCredentials, onPreparePlan, onNotify }: { groups: Group[]; refreshKey: number; onRefresh: () => void; onOpenAccount: (accountId: string) => void; onEditCredentials: (account: Account, field: CredentialField) => void; onPreparePlan: (account: Account) => void; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [records, setRecords] = useState<CredentialRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("ALL");
  const [groupId, setGroupId] = useState("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [picker, setPicker] = useState<CredentialRecord | null>(null);
  const [rotate, setRotate] = useState<CredentialRecord | null>(null);
  const [copyPlain, setCopyPlain] = useState<CredentialRecord | null>(null);
  const [revealed, setRevealed] = useState<CredentialRotateResult | null>(null);
  const [history, setHistory] = useState<CredentialRecord | null>(null);
  const debouncedQuery = useDebouncedValue(query);
  useEffect(() => { let cancelled = false; setLoading(true); setError(""); const params = new URLSearchParams({ page: "1", page_size: "200" }); if (debouncedQuery.trim()) params.set("q", debouncedQuery.trim()); if (groupId !== "all") params.set("group_id", groupId); if (status !== "ALL") params.set("status", status); api<{ items: CredentialRecord[]; total: number }>(`/api/v1/credentials?${params.toString()}`).then((data) => { if (!cancelled) { setRecords(data.items); setTotal(data.total); } }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Credentials are unavailable."); }).finally(() => { if (!cancelled) setLoading(false); }); return () => { cancelled = true; }; }, [refreshKey, debouncedQuery, status, groupId]);
  const storedCount = records.filter((record) => record.password_present).length;
  const missingCount = records.filter((record) => !record.password_present).length;
  const requiredCount = records.filter((record) => record.login_status === "REQUIRED").length;
  return <div className="infra-page"><InfrastructureHeader eyebrow="CREDENTIAL VAULT" title="Credentials" description="Inspect and manage every account's encrypted credential set. Plaintext password material is revealed once and never re-read from the API." icon={<KeyRound size={20} />} onRefresh={onRefresh} loading={loading} /><section className="summary-grid" aria-label="Credential summary"><SummaryCard label="Credential records" value={records.length.toString()} note={total > records.length ? `${total} total across all groups` : "In this view"} icon={<KeyRound size={17} />} /><SummaryCard label="Encrypted" value={storedCount.toString()} note="Password present at rest" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Missing password" value={missingCount.toString()} note="Needs provisioning" icon={<XCircle size={17} />} tone={missingCount ? "amber" : "default"} /><SummaryCard label="Require fresh login" value={requiredCount.toString()} note="After credential changes" icon={<LockKeyhole size={17} />} /></section><section className="infra-panel"><div className="panel-header"><div><h2>Credential inventory</h2><p>Recovery values and 2FA state are masked metadata; rotation stores a fresh password and returns it exactly once.</p></div><div className="proxy-panel-controls"><div className="group-context"><span>GROUP SCOPE</span><label className="select-wrap"><select aria-label="Filter credentials by account group" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="all">All groups</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count} accounts</option>)}</select><ChevronDown size={15} /></label></div><span className="inventory-count">{loading ? "Refreshing…" : `${records.length} of ${total} records`}</span></div></div><InventoryToolbar value={query} onChange={setQuery} onClear={() => setQuery("")} placeholder="Search by account email or account ID" status={status} onStatusChange={setStatus} statuses={[{ value: "STORED_ENCRYPTED", label: "Stored encrypted" }, { value: "MISSING", label: "Missing password" }, { value: "REQUIRED", label: "Requires fresh login" }]} loading={loading} onRefresh={onRefresh} />{error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={onRefresh}>Retry</button></div>}{!loading && !records.length ? <div className="table-state empty-state"><div className="empty-icon"><KeyRound size={21} /></div><strong>No credential records match this view</strong><span>Records appear once accounts hold an encrypted secret set. Adjust the search or scope to see more.</span></div> : <div className="table-scroll"><table><thead><tr><th scope="col">Account</th><th scope="col">Group</th><th scope="col">Vault</th><th scope="col">Recovery</th><th scope="col">2FA</th><th scope="col">Revision</th><th scope="col">Updated</th><th scope="col">Actions</th></tr></thead><tbody>{records.map((record) => <tr key={record.id}><td><button type="button" className="account-link" onClick={() => onOpenAccount(record.id)}><span className="account-avatar">{record.email.slice(0, 2).toUpperCase()}</span><span><strong>{record.email}</strong><small>{record.id}</small></span></button></td><td><span className="group-tag"><span className="tag-dot" />{record.group_name}</span></td><td>{record.password_present ? <span className="security-cell"><LockKeyhole size={14} /> Encrypted</span> : <span className="metadata-cell"><span className="status-dot neutral" /> Missing</span>}</td><td>{record.recovery_masked ? <span className="metadata-cell">{record.recovery_masked}</span> : <span className="metadata-cell"><span className="status-dot neutral" /> Not stored</span>}</td><td>{record.two_fa_configured ? <span className="security-cell"><ShieldCheck size={14} /> Configured</span> : <span className="metadata-cell"><span className="status-dot neutral" /> Not configured</span>}</td><td><span className="metadata-cell"><span className={`status-dot ${record.credential_status === "STORED_ENCRYPTED" ? "teal" : "neutral"}`} /> v{record.active_version ?? "—"} · {record.latest_version}</span></td><td><span className="metadata-cell">{formatDateTime(record.updated_at)}</span></td><td><div className="row-actions"><button type="button" className="row-open" title={record.password_present ? "Copy the saved password" : "No saved password to copy"} disabled={!record.password_present} onClick={() => setCopyPlain(record)}><ClipboardCopy size={15} /></button><button type="button" className="row-open" title="Regenerate password (revealed once)" onClick={() => setRotate(record)}><KeyRound size={15} /></button><button type="button" className="row-open" title="Edit a credential field" onClick={() => setPicker(record)}><Pencil size={15} /></button><button type="button" className="row-open" title="Automated credential change plan" onClick={() => onPreparePlan(credentialAccountFor(record))}><ShieldCheck size={15} /></button><button type="button" className="row-open" title="Revision history" onClick={() => setHistory(record)}><Clock3 size={15} /></button></div></td></tr>)}</tbody></table></div>}{picker && <CredentialFieldPickerModal record={picker} onClose={() => setPicker(null)} onPick={(field) => { setPicker(null); onEditCredentials(credentialAccountFor(picker), field); }} />}{rotate && <CredentialRotateModal record={rotate} onClose={() => setRotate(null)} onRevealed={(result) => { setRotate(null); setRevealed(result); onNotify("info", `New password generated for ${result.email} — shown once; apply it to Google. The account now requires a fresh login.`); onRefresh(); }} onError={(message) => setError(message)} />}{revealed && <CredentialRevealedModal result={revealed} onClose={() => setRevealed(null)} onUpdated={() => { setRevealed(null); onRefresh(); }} />}{history && <CredentialHistoryModal record={history} onClose={() => setHistory(null)} />}{copyPlain && <CredentialCopyModal record={copyPlain} onClose={() => setCopyPlain(null)} onNotify={onNotify} />}</section></div>;
}

function CredentialFieldPickerModal({ record, onClose, onPick }: { record: CredentialRecord; onClose: () => void; onPick: (field: CredentialField) => void }) {
  const [field, setField] = useState<CredentialField>("password");
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal credential-modal" role="dialog" aria-modal="true" aria-labelledby="credential-field-picker-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> EDIT CREDENTIALS</div><h2 id="credential-field-picker-title">Edit a credential field</h2><p>{record.email}</p></div><button type="button" className="icon-button" aria-label="Close field picker" onClick={onClose}><X size={18} /></button></div><div className="modal-body"><div className="security-callout"><KeyRound size={17} /><div><strong>Choose a single field to replace</strong><span>Changes are encrypted into the vault, advance the revision, and invalidate the saved login profile.</span></div></div><div className="field"><label htmlFor="credential-field-picker-role">Field</label><select id="credential-field-picker-role" value={field} onChange={(event) => setField(event.target.value as CredentialField)}><option value="password">Password</option><option value="recovery">Recovery address</option><option value="two_fa">2FA secret</option></select></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button primary" onClick={() => onPick(field)}><Pencil size={16} /> Edit {field === "recovery" ? "recovery" : field === "two_fa" ? "2FA" : "password"}</button></div></section></div>;
}

function CredentialRotateModal({ record, onClose, onRevealed, onError }: { record: CredentialRecord; onClose: () => void; onRevealed: (result: CredentialRotateResult) => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const rotate = async () => { setPending(true); setError(""); try { onRevealed(await api<CredentialRotateResult>(`/api/v1/accounts/${record.id}/credentials/rotate`, { method: "POST" })); } catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); setPending(false); } };
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal credential-modal" role="dialog" aria-modal="true" aria-labelledby="rotate-confirm-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GET CREDENTIALS</div><h2 id="rotate-confirm-title">Generate a new password</h2><p>{record.email} · {record.credential_status === "STORED_ENCRYPTED" ? "encrypted vault" : "no password stored"}</p></div><button type="button" className="icon-button" aria-label="Close" onClick={onClose} disabled={pending}><X size={18} /></button></div><div className="modal-body">{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><KeyRound size={17} /><div><strong>The current password is never shown</strong><span>A fresh random password is generated, stored encrypted in the vault, and revealed here exactly once. Apply it in Google — or use the automated credential change plan — then the account needs a fresh login.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="button" className="button primary" onClick={() => void rotate()} disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}<KeyRound size={16} /> Generate & reveal</button></div></section></div>;
}

function CredentialRevealedModal({ result, onClose, onUpdated }: { result: CredentialRotateResult; onClose: () => void; onUpdated: () => void }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => { try { await navigator.clipboard?.writeText(result.password); setCopied(true); } catch { setCopied(false); } };
  return <div className="modal-backdrop credential-backdrop" role="presentation"><section className="modal credential-modal" role="dialog" aria-modal="true" aria-labelledby="revealed-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CREDENTIAL GENERATED</div><h2 id="revealed-title">New password — shown once</h2><p>{result.email} · revision v{result.version}</p></div></div><div className="modal-body"><div className="security-callout"><ShieldCheck size={17} /><div><strong>Copy it now, then apply it to Google</strong><span>It is stored encrypted at rest and will not be shown again. The account now requires a fresh login for the saved profile.</span></div></div><div className="provisioned-secret-display"><code>{result.password}</code><button type="button" className="text-button copy-button" onClick={() => void copy()}>{copied ? "Copied" : "Copy password"}</button></div><small className="field-caption">Recovery {result.recovery_masked ? `· ${result.recovery_masked}` : "not stored"} · 2FA {result.two_fa_configured ? "configured (unchanged)" : "not configured"}</small></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close</button><button type="button" className="button primary" onClick={() => { if (copied) onUpdated(); else { void copy().then(() => onUpdated()); } }}>Done & refresh inventory</button></div></section></div>;
}

function CredentialCopyModal({ record, onClose, onNotify }: { record: CredentialRecord; onClose: () => void; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [data, setData] = useState<CredentialPlaintext | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  useEffect(() => { let cancelled = false; api<CredentialPlaintext>(`/api/v1/accounts/${record.id}/credentials/plaintext`).then((result) => { if (!cancelled) { setData(result); setLoading(false); } }).catch((requestError) => { if (!cancelled) { setError((requestError as Error).message || "The saved password is unavailable."); setLoading(false); } }); return () => { cancelled = true; }; }, [record.id]);
  const copy = async () => { if (!data) return; try { await navigator.clipboard?.writeText(data.password); setCopied(true); onNotify("success", `Saved password copied to the clipboard for ${data.email}.`); } catch { onNotify("error", "Could not access the clipboard."); } };
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal credential-modal" role="dialog" aria-modal="true" aria-labelledby="copy-plain-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> SAVED PASSWORD</div><h2 id="copy-plain-title">Copy the saved password</h2><p>{record.email} · {record.active_version ? `active v${record.active_version}` : "no active revision"}</p></div><button type="button" className="icon-button" aria-label="Close" onClick={onClose}><X size={18} /></button></div><div className="modal-body">{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{loading && !data && <div className="table-state loading-state"><span className="spinner" /><strong>Opening the encrypted vault</strong></div>}{data && <><div className="security-callout"><KeyRound size={17} /><div><strong>The real saved password is revealed for copying</strong><span>This is the value stored in the vault — after an automated credential change it is the freshly applied Google password. The read is recorded in the account audit events.</span></div></div><div className="provisioned-secret-display"><code>{data.password}</code><button type="button" className="text-button copy-button" onClick={() => void copy()}>{copied ? "Copied" : "Copy password"}</button></div><small className="field-caption">Recovery {data.recovery_masked ? `· ${data.recovery_masked}` : "not stored"} · 2FA {data.two_fa_configured ? "configured" : "not configured"} · {(data.active_version ? `active v${data.active_version}` : "no active revision")} of {data.latest_version}</small></>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close</button></div></section></div>;
}

function CredentialHistoryModal({ record, onClose }: { record: CredentialRecord; onClose: () => void }) {
  const [rows, setRows] = useState<CredentialRevisionRow[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { let cancelled = false; api<CredentialRevisionRow[]>(`/api/v1/accounts/${record.id}/credential-revisions`).then((data) => { if (!cancelled) setRows(data); }).catch((requestError) => { if (!cancelled) setError((requestError as Error).message || "Revision history is unavailable."); }); return () => { cancelled = true; }; }, [record.id]);
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal credential-modal" role="dialog" aria-modal="true" aria-labelledby="history-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CREDENTIAL HISTORY</div><h2 id="history-title">Credential revisions</h2><p>{record.email} · {record.active_version ? `active v${record.active_version}` : "no active revision"}</p></div><button type="button" className="icon-button" aria-label="Close history" onClick={onClose}><X size={18} /></button></div><div className="modal-body">{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{!rows ? <div className="table-state loading-state"><span className="spinner" /><strong>Loading revision history</strong></div> : rows.length ? <div className="table-scroll"><table><thead><tr><th scope="col">Version</th><th scope="col">Status</th><th scope="col">Recovery</th><th scope="col">2FA</th><th scope="col">Stored</th></tr></thead><tbody>{rows.map((row) => <tr key={`${row.version}`}><td>{row.version === record.active_version ? <span className="status-badge ready">v{row.version}</span> : <span className="metadata-cell">v{row.version}</span>}</td><td><InventoryStatus status={row.status === "ACTIVE" ? "SAVED" : "RETIRED"} /></td><td>{row.recovery_masked ?? <span className="metadata-cell">None</span>}</td><td>{row.two_fa_configured ? "Configured" : <span className="metadata-cell">Not configured</span>}</td><td>{formatDateTime(row.created_at)}</td></tr>)}</tbody></table></div> : <div className="table-state empty-state"><div className="empty-icon"><KeyRound size={21} /></div><strong>No revisions recorded</strong><span>Revisions are created when credentials are stored or rotated.</span></div>}</div><div className="modal-footer"><button type="button" className="button primary" onClick={onClose}>Close</button></div></section></div>;
}

function ProxyManagementModal({ kind, proxies, groups, initialGroupId, onClose, onDone, onError }: { kind: "edit" | "assign" | "remove"; proxies: ProxyRecord[]; groups: Group[]; initialGroupId: string; onClose: () => void; onDone: () => void; onError: (message: string) => void }) {
  const proxy = proxies[0];
  const [connection, setConnection] = useState("");
  const [label, setLabel] = useState(proxy?.label ?? "");
  const [region, setRegion] = useState(proxy?.region ?? "");
  const [assignmentGroupId, setAssignmentGroupId] = useState(() => groups.some((group) => group.id === initialGroupId) ? initialGroupId : groups[0]?.id ?? "");
  const [accountQuery, setAccountQuery] = useState("");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [accountIds, setAccountIds] = useState<string[]>([]);
  const [visibleAccountLimit, setVisibleAccountLimit] = useState(150);
  const [confirmation, setConfirmation] = useState("");
  const [loadingAccounts, setLoadingAccounts] = useState(kind === "assign");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (kind !== "assign" || !assignmentGroupId) return;
    let cancelled = false;
    const load = async () => {
      setLoadingAccounts(true); setAccounts([]); setAccountIds([]); setError("");
      try {
        const result = await api<{ items: Account[]; total: number }>(`/api/v1/accounts?group_id=${encodeURIComponent(assignmentGroupId)}&page=1&page_size=10000`);
        if (!cancelled) { setAccounts(result.items); setAccountIds(result.items.filter((account) => account.proxy.id === proxy?.id).map((account) => account.id)); }
      } catch (requestError) { if (!cancelled) setError((requestError as Error).message || "Accounts are unavailable."); }
      finally { if (!cancelled) setLoadingAccounts(false); }
    };
    void load(); return () => { cancelled = true; };
  }, [kind, assignmentGroupId, proxy?.id]);
  if (!proxy) return null;
  const title = kind === "edit" ? "Edit proxy" : kind === "assign" ? "Manage proxy assignments" : "Remove proxy";
  const normalizedQuery = accountQuery.trim().toLowerCase();
  const filteredAccounts = accounts.filter((account) => !normalizedQuery || `${account.email} ${account.proxy.label ?? ""}`.toLowerCase().includes(normalizedQuery));
  const visibleAccounts = filteredAccounts.slice(0, visibleAccountLimit);
  const allVisibleSelected = visibleAccounts.length > 0 && visibleAccounts.every((account) => accountIds.includes(account.id));
  const toggleVisible = () => { const ids = visibleAccounts.map((account) => account.id); setAccountIds((current) => allVisibleSelected ? current.filter((id) => !ids.includes(id)) : [...new Set([...current, ...ids])]); };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (kind === "remove" && confirmation.trim().toUpperCase() !== "REMOVE") { setError("Type REMOVE to confirm this action."); return; }
    if (kind === "assign" && !assignmentGroupId) { setError("Choose an account group first."); return; }
    setPending(true); setError("");
    try {
      if (kind === "edit") await api(`/api/v1/proxies/${proxy.id}`, { method: "PATCH", body: JSON.stringify({ ...(connection.trim() ? { proxy: connection.trim() } : {}), label: label.trim() || null, region: region.trim() || null }) });
      if (kind === "assign") await api(`/api/v1/proxies/${proxy.id}/group-assignments`, { method: "POST", body: JSON.stringify({ group_id: assignmentGroupId, account_ids: accountIds, label: label.trim() || null, region: region.trim() || null }) });
      if (kind === "remove") await Promise.all(proxies.map((item) => api(`/api/v1/proxies/${item.id}`, { method: "DELETE", body: JSON.stringify({ confirmation: "REMOVE" }) })));
      onDone();
    } catch (requestError) { setError((requestError as Error).message); onError((requestError as Error).message); setPending(false); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal proxy-modal" role="dialog" aria-modal="true" aria-labelledby="proxy-management-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROXY MANAGEMENT</div><h2 id="proxy-management-title">{title}</h2><p>{kind === "remove" ? `${proxies.length} endpoint${proxies.length === 1 ? "" : "s"} · remove all account assignments` : `${proxy.label || proxy.id} · changes are audited per account`}</p></div><button type="button" className="icon-button" aria-label={`Close ${title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="proxy-management-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{kind === "edit" && <><div className="security-callout"><KeyRound size={17} /><div><strong>Update the shared proxy record</strong><span>Changing the connection updates every account currently using this endpoint. Leave it blank to keep the encrypted connection unchanged.</span></div></div><div className="form-grid"><div className="field"><label htmlFor="proxy-management-connection">New proxy connection</label><input id="proxy-management-connection" type="password" value={connection} onChange={(event) => setConnection(event.target.value)} placeholder="ip:port or username:password:ip:port" autoComplete="off" disabled={pending} /></div><div className="field"><label htmlFor="proxy-management-label">Label</label><input id="proxy-management-label" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="e.g. US-NY-0142" disabled={pending} /></div><div className="field"><label htmlFor="proxy-management-region">Region</label><input id="proxy-management-region" value={region} onChange={(event) => setRegion(event.target.value)} placeholder="e.g. New York, US" disabled={pending} /></div></div></>}{kind === "assign" && <><div className="security-callout"><UsersRound size={17} /><div><strong>Manage assignments by account group</strong><span>This replaces assignments only in the selected group. Other groups and owners remain unchanged; each account can use one proxy.</span></div></div><div className="proxy-assignment-toolbar"><label className="field"><span>Account group</span><select value={assignmentGroupId} onChange={(event) => { setAssignmentGroupId(event.target.value); setVisibleAccountLimit(150); }} disabled={pending || !groups.length}><option value="">Choose a group</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count} accounts</option>)}</select></label><label className="field proxy-assignment-search"><span>Search this group</span><input type="search" value={accountQuery} onChange={(event) => { setAccountQuery(event.target.value); setVisibleAccountLimit(150); }} placeholder="Email or current proxy" disabled={pending} /></label></div>{loadingAccounts ? <div className="table-state loading-state"><span className="spinner" /><strong>Loading this group’s accounts</strong></div> : <><div className="proxy-assignment-summary"><strong>{accountIds.length} selected</strong><span>{accounts.length} accounts · {filteredAccounts.length} matching</span><button type="button" className="text-button" onClick={toggleVisible} disabled={pending || !visibleAccounts.length}>{allVisibleSelected ? "Clear visible" : "Select visible"}</button><button type="button" className="text-button" onClick={() => setAccountIds([])} disabled={pending || !accountIds.length}>Clear group</button></div><div className="assignment-list">{visibleAccounts.map((account) => <label className="assignment-row" key={account.id}><input type="checkbox" checked={accountIds.includes(account.id)} onChange={() => setAccountIds((current) => current.includes(account.id) ? current.filter((id) => id !== account.id) : [...current, account.id])} disabled={pending} /><span><strong>{account.email}</strong><small>Current proxy: {account.proxy.label || "none"} · {account.proxy.status}</small></span></label>)}{!filteredAccounts.length && <div className="table-state empty-state"><strong>No accounts match this search</strong></div>}</div>{filteredAccounts.length > visibleAccounts.length && <button type="button" className="button secondary small proxy-show-more" onClick={() => setVisibleAccountLimit((limit) => limit + 150)}>Show more · {filteredAccounts.length - visibleAccounts.length} remaining</button>}</>}</>}{kind === "remove" && <><div className="delete-warning"><Trash2 size={18} /><div><strong>Remove proxy assignments</strong><span>{proxies.reduce((sum, item) => sum + item.account_count, 0)} account assignments will be cleared. The proxy health record and encrypted connection are removed from active use.</span></div></div><div className="field"><label htmlFor="proxy-remove-confirm">Type REMOVE to confirm</label><input id="proxy-remove-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="REMOVE" autoComplete="off" disabled={pending} /></div></>}</form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="proxy-management-form" className={`button ${kind === "remove" ? "danger" : "primary"}`} disabled={pending || loadingAccounts || (kind === "assign" && (!groups.length || !assignmentGroupId))}>{pending ? "Applying changes…" : kind === "remove" ? "Remove proxy" : kind === "assign" ? `Save assignments · ${accountIds.length}` : "Save proxy"}</button></div></section></div>;
}

function NavLabel({ children }: { children: string }) { return <div className="nav-label">{children}</div>; }
function NavItem({ icon, label, active, badge, mutedBadge, onClick }: { icon: React.ReactNode; label: string; active?: boolean; badge?: string | number; mutedBadge?: boolean; onClick?: () => void }) { return <button type="button" className={`nav-item ${active ? "active" : ""}`} aria-label={label} title={label} aria-current={active ? "page" : undefined} disabled={!onClick} onClick={onClick}>{icon}<span>{label}</span>{badge !== undefined && <span className={`nav-badge ${mutedBadge ? "muted" : ""}`}>{badge}</span>}</button>; }
function SummaryCard({ label, value, note, icon, tone = "default" }: { label: string; value: string; note: string; icon: React.ReactNode; tone?: string }) { return <div className={`summary-card ${tone}`}><div className="summary-top"><span>{label}</span><span className="summary-icon">{icon}</span></div><strong>{value}</strong><small>{note}</small></div>; }
function AttentionStrip({ accounts, onOpen }: { accounts: Account[]; onOpen: (account: Account) => void }) {
  return <section className={`attention-strip ${accounts.length ? "has-issues" : "clear"}`} aria-label="Accounts requiring attention"><div className="attention-heading"><div className="attention-mark"><ShieldCheck size={17} /></div><div><span className="detail-label">Attention center</span><strong>{accounts.length ? `${accounts.length} account${accounts.length === 1 ? "" : "s"} need review` : "No accounts need attention"}</strong></div></div>{accounts.length ? <div className="attention-items">{accounts.slice(0, 3).map((account) => <button type="button" className="attention-item" key={account.id} onClick={() => onOpen(account)}><span><strong>{account.email}</strong><small>{labelStatus(account.login_status)} · {account.group_name}</small></span><ArrowRight size={15} /></button>)}{accounts.length > 3 && <span className="attention-more">+{accounts.length - 3} more in the current view</span>}</div> : <span className="attention-clear-copy">Authentication, profile, and account records are clear in this view.</span>}</section>;
}

const registryActions: { kind: RegistryAction; label: string; shortLabel: string; icon: React.ReactNode; tone?: string }[] = [
  { kind: "login", label: "Login", shortLabel: "Login", icon: <Play size={15} /> },
  { kind: "credentials", label: "Credentials", shortLabel: "Credentials", icon: <KeyRound size={15} /> },
  { kind: "upload_profile", label: "Upload saved browser profile", shortLabel: "Upload profile", icon: <Upload size={15} /> },
  { kind: "tags", label: "Manage tags", shortLabel: "Tags", icon: <Tags size={15} /> },
  { kind: "contacts", label: "Contacts", shortLabel: "Contacts", icon: <AddressBook size={15} /> },
  { kind: "security", label: "Browser security", shortLabel: "Security", icon: <ShieldCheck size={15} /> },
  { kind: "labels", label: "Labels & Markers", shortLabel: "Labels & Markers", icon: <Tags size={15} /> },
  { kind: "apps", label: "Google apps", shortLabel: "Apps", icon: <Boxes size={15} /> },
  { kind: "mail", label: "Mail workflow", shortLabel: "Mail workflow", icon: <Mail size={15} /> },
  { kind: "addons", label: "Add-ons", shortLabel: "Add-ons", icon: <SlidersHorizontal size={15} /> },
  { kind: "alerts", label: "Google Alerts", shortLabel: "Alerts", icon: <Bell size={15} /> },
  { kind: "preferences", label: "Account preferences", shortLabel: "Preferences", icon: <Palette size={15} /> },
  { kind: "edit", label: "Edit metadata", shortLabel: "Edit", icon: <Pencil size={15} /> },
  { kind: "move", label: "Move group", shortLabel: "Move", icon: <MoveRight size={15} /> },
  { kind: "delete", label: "Archive", shortLabel: "Archive", icon: <Trash2 size={15} />, tone: "danger" },
];

function RegistryActionBar({ accounts, selectedAccountIds, actionScope, group, onScopeChange, onAction, onStopGroup, onStopAll }: { accounts: Account[]; selectedAccountIds: string[]; actionScope: ActionScope; group?: Group; onScopeChange: (scope: ActionScope) => void; onAction: (kind: RegistryAction) => void; onStopGroup: () => void; onStopAll: () => void }) {
  const selectedCount = selectedAccountIds.length;
  const scopeCount = actionScope === "group" ? group?.account_count ?? 0 : selectedCount;
  const credentialsDisabled = actionScope === "group" || selectedCount !== 1;
  return <section className={`registry-actions${selectedCount ? " has-selection" : ""}${actionScope === "group" ? " group-scope" : ""}`} aria-label="Account actions"><div className="registry-actions-head"><div><span className="detail-label">{selectedCount ? `${selectedCount} selected` : actionScope === "group" ? "Group actions" : "Bulk actions"}</span><strong>{scopeCount ? "Choose an operation for this scope" : "Select accounts to see available actions"}</strong></div><div className="scope-control" role="group" aria-label="Action scope"><button type="button" className={actionScope === "selected" ? "selected" : ""} onClick={() => onScopeChange("selected")}>Selected <span>{selectedCount}</span></button><button type="button" className={actionScope === "group" ? "selected" : ""} disabled={!group} onClick={() => onScopeChange("group")}>Entire group <span>{group?.account_count ?? 0}</span></button></div></div><div className="action-scope-note" role="status"><span className={`scope-dot ${scopeCount ? "active" : ""}`} />{scopeCount ? `${scopeCount} ${scopeCount === 1 ? "account" : "accounts"} in scope` : actionScope === "group" ? "Choose a group with accounts" : "Select accounts below to enable actions"}{credentialsDisabled && <small>Credentials require exactly one selected account.</small>}</div><div className="registry-action-grid">{registryActions.map((action) => <button key={action.kind} type="button" className={`registry-action ${action.tone ?? ""}`} disabled={!scopeCount || (["credentials", "upload_profile"].includes(action.kind) && credentialsDisabled)} title={["credentials", "upload_profile"].includes(action.kind) && credentialsDisabled ? "Select exactly one account" : action.label} onClick={() => onAction(action.kind)}><span className="registry-action-icon">{action.icon}</span><span>{action.shortLabel}</span></button>)}{actionScope === "group" && <button type="button" className="registry-action danger" disabled={!group?.account_count} title="Stop every active browser process in this group" onClick={onStopGroup}><span className="registry-action-icon"><Power size={16} /></span><span>Stop group processes</span></button>}<button type="button" className="registry-action danger" title="Stop all active browser sessions in your accessible workspace" onClick={onStopAll}><span className="registry-action-icon"><Power size={16} /></span><span>Stop all active sessions</span></button></div></section>;
}

function GroupStopConfirm({ group, onCancel, onConfirm }: { group: Group; onCancel: () => void; onConfirm: () => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal" role="alertdialog" aria-modal={!inspectorEmbedded} aria-labelledby="stop-group-title" aria-describedby="stop-group-description"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GROUP CONTROL</div><h2 id="stop-group-title">Stop group processes?</h2><p>{group.name} · {group.account_count} accounts in scope</p></div><button type="button" className="icon-button" aria-label="Close stop group dialog" onClick={onCancel}><X size={18} /></button></div><div className="modal-body"><div className="delete-warning" id="stop-group-description"><Power size={18} /><div><strong>Close every active browser session in this group</strong><span>Running logins and account operations will be interrupted. Saved profiles and account records remain intact and can be reopened later.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" autoFocus onClick={onCancel}>Keep running</button><button type="button" className="button danger" onClick={onConfirm}>Stop all group processes</button></div></section></div>;
}

function AllStopConfirm({ onCancel, onConfirm }: { onCancel: () => void; onConfirm: () => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal" role="alertdialog" aria-modal={!inspectorEmbedded} aria-labelledby="stop-all-title" aria-describedby="stop-all-description"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> WORKSPACE CONTROL</div><h2 id="stop-all-title">Stop all active sessions?</h2><p>Every accessible group · active processes only</p></div><button type="button" className="icon-button" aria-label="Close stop all sessions dialog" onClick={onCancel}><X size={18} /></button></div><div className="modal-body"><div className="delete-warning" id="stop-all-description"><Power size={18} /><div><strong>Close every active browser session</strong><span>Running logins and account operations will be interrupted. Saved profiles, credentials, and account records remain intact.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" autoFocus onClick={onCancel}>Keep running</button><button type="button" className="button danger" onClick={onConfirm}>Stop all active sessions</button></div></section></div>;
}

function AccountTable({ accounts, profileSizes, loading, canOperate, selectedAccountIds, onSelectionChange, onOpen, onAdd, onContextAction }: { accounts: Account[]; profileSizes: Record<string, number | null>; loading: boolean; canOperate: boolean; selectedAccountIds: string[]; onSelectionChange: (ids: string[]) => void; onOpen: (account: Account) => void; onAdd: () => void; onContextAction: (account: Account, kind: "copy" | "login" | "edit" | "delete" | "subscribe", selectedIds?: string[]) => void }) {
  const [context, setContext] = useState<{ account: Account; selectedIds: string[]; x: number; y: number } | null>(null);
  if (loading) return <div className="table-state loading-state"><span className="spinner" /><strong>Loading account registry</strong><span>Keeping the table stable while the latest view arrives.</span></div>;
  if (!accounts.length) return <div className="table-state empty-state"><div className="empty-icon"><Database size={21} /></div><strong>No accounts in this view</strong><span>Add an account or adjust the group and search filters.</span><button type="button" className="button primary small" onClick={onAdd}><Plus size={15} /> Add account</button></div>;
  const allSelected = accounts.length > 0 && accounts.every((account) => selectedAccountIds.includes(account.id));
  const toggleAll = () => onSelectionChange(allSelected ? selectedAccountIds.filter((id) => !accounts.some((account) => account.id === id)) : [...new Set([...selectedAccountIds, ...accounts.map((account) => account.id)])]);
  const toggleOne = (id: string) => onSelectionChange(selectedAccountIds.includes(id) ? selectedAccountIds.filter((selectedId) => selectedId !== id) : [...selectedAccountIds, id]);
  return <div className="table-scroll"><table><caption className="sr-only">Company Gmail account registry</caption><thead><tr><th scope="col" className="select-column"><input type="checkbox" aria-label="Select all accounts in this view" checked={allSelected} onChange={toggleAll} /></th><th scope="col">Account</th><th scope="col">Group</th><th scope="col">Phase status</th><th scope="col">Login status</th><th scope="col">Secrets</th><th scope="col">Proxy</th><th scope="col">Profile</th><th scope="col">Profile size <span className="profile-size-reference">(100 MB reference)</span></th><th scope="col"><span className="sr-only">Open</span></th></tr></thead><tbody>{accounts.map((account) => <tr key={account.id} className={selectedAccountIds.includes(account.id) ? "selected-row" : ""} onContextMenu={(event) => { event.preventDefault(); const selection = selectedAccountIds.includes(account.id) ? selectedAccountIds : [account.id]; if (!selectedAccountIds.includes(account.id)) onSelectionChange(selection); setContext({ account, selectedIds: selection, x: Math.min(event.clientX, window.innerWidth - 210), y: Math.min(event.clientY, window.innerHeight - 190) }); }}><td className="select-column"><input type="checkbox" aria-label={`Select ${account.email}`} checked={selectedAccountIds.includes(account.id)} onChange={() => toggleOne(account.id)} /></td><td className="account-cell"><button type="button" className="account-link" onClick={() => onOpen(account)}><span className="account-avatar">{account.email.slice(0, 2).toUpperCase()}</span><span><strong>{account.email}</strong><small>Added {formatDate(account.created_at)}</small>{account.tags?.length ? <span className="account-tag-list">{account.tags.map((tag) => <span className="account-tag-chip" key={tag}>{tag}</span>)}</span> : null}</span></button></td><td><span className="group-tag"><span className="tag-dot" />{account.group_name}</span></td><td><StatusBadge status={account.readiness_status} /></td><td><LoginBadge status={account.login_status} /></td><td><span className="security-cell"><LockKeyhole size={14} /> Encrypted</span></td><td><span className="metadata-cell"><span className={`status-dot ${account.proxy.status === "UNASSIGNED" ? "neutral" : "teal"}`} />{account.proxy.status === "UNASSIGNED" ? "Reserved" : labelStatus(account.proxy.status)}</span>{(account.proxy.endpoint || account.proxy.label || account.proxy.id) && <small className="table-subtext proxy-endpoint" title={account.proxy.endpoint || account.proxy.label || account.proxy.id || ""}>{account.proxy.endpoint || account.proxy.label || account.proxy.id}</small>}</td><td><span className="metadata-cell"><span className={`status-dot ${account.profile.status === "SAVED" ? "teal" : "neutral"}`} /> {account.profile.status === "SAVED" ? "Saved · reopens without login" : account.profile.status === "IMPORTED" ? "Imported · sign-in check pending" : account.profile.status === "OPEN" ? "Active session" : account.profile.status === "ARCHIVED" ? "Archived" : "Not saved yet"}</span></td><td className="profile-size-cell">{account.id in profileSizes ? profileSizes[account.id] == null ? <span className="profile-size-unavailable">Unavailable</span> : <ProfileSizeIndicator sizeBytes={profileSizes[account.id]!} /> : account.profile.id || account.profile.status === "SAVED" || account.profile.status === "IMPORTED" ? <span className="profile-size-unavailable">Checking…</span> : <span className="profile-size-unavailable">No profile</span>}</td><td><button type="button" className="row-open" aria-label={`Open ${account.email}`} onClick={() => onOpen(account)}><ArrowRight size={16} /></button></td></tr>)}</tbody></table>{context && <div className="account-context-menu" role="menu" style={{ left: context.x, top: context.y }} onClick={(event) => event.stopPropagation()}><button type="button" role="menuitem" onClick={() => { onContextAction(context.account, "copy", context.selectedIds); setContext(null); }}><ClipboardCopy size={14} /> Copy emails{context.selectedIds.length > 1 ? ` (${context.selectedIds.length})` : ""}</button>{canOperate && <button type="button" role="menuitem" onClick={() => { onContextAction(context.account, "subscribe", context.selectedIds); setContext(null); }}><Mail size={14} /> Subscribe to newsletters{context.selectedIds.length > 1 ? ` (${context.selectedIds.length})` : ""}</button>}{([{ kind: "login", label: "Login", icon: <Play size={14} /> }, { kind: "edit", label: "Edit", icon: <Pencil size={14} /> }, { kind: "delete", label: "Archive", icon: <Trash2 size={14} /> }] as const).map((item) => <button type="button" role="menuitem" key={item.kind} onClick={() => { onContextAction(context.account, item.kind, context.selectedIds); setContext(null); }}>{item.icon}{item.label}</button>)}</div>}</div>;
}

const PROFILE_SIZE_REFERENCE_BYTES = 100 * 1024 * 1024;
const formatProfileSize = (value: number) => value >= 1024 ** 3 ? `${(value / (1024 ** 3)).toFixed(2)} GB` : `${(value / (1024 * 1024)).toFixed(1)} MB`;
function ProfileSizeIndicator({ sizeBytes }: { sizeBytes: number }) {
  const percent = sizeBytes === 0 ? 0 : Math.max(1, Math.round(sizeBytes / PROFILE_SIZE_REFERENCE_BYTES * 100));
  const fill = Math.min(percent, 100);
  const tone = percent >= 100 ? "critical" : percent >= 75 ? "high" : percent >= 50 ? "medium" : "low";
  return <div className="profile-size-indicator" title={`${formatProfileSize(sizeBytes)} · ${percent}% of the 100 MB reference`}>
    <div className="profile-size-meter" role="progressbar" aria-label="Browser profile size compared with 100 MB reference" aria-valuemin={0} aria-valuemax={100} aria-valuenow={fill} aria-valuetext={`${formatProfileSize(sizeBytes)}, ${percent}% of the 100 MB reference`}>
      <span className={`profile-size-meter-fill ${tone}`} style={{ width: `${fill}%` }} />
    </div>
    <span className="profile-size-reading"><strong>{formatProfileSize(sizeBytes)}</strong><small>{percent}%</small></span>
  </div>;
}
function StatusBadge({ status }: { status: string }) { const tone = status === "READY_FOR_LOGIN" ? "ready" : ["SETUP_ERROR", "INVALID"].includes(status) ? "danger" : "setup"; return <span className={`status-badge ${tone}`}><span className="status-mark">{tone === "ready" ? <Check size={11} /> : tone === "danger" ? <XCircle size={11} /> : <Clock3 size={11} />}</span>{labelStatus(status)}</span>; }
const loginTone = (status: string) => status === "NONEXISTENT" ? "danger" : status === "AUTHENTICATED" ? "ok" : ["PROFILE_IMPORTED", "VERIFICATION_REQUIRED", "NEEDS_ATTENTION", "UNKNOWN_PAGE", "ACCOUNT_NOT_FOUND"].includes(status) ? "warning" : "neutral";
function LoginBadge({ status }: { status: string }) { return <span className={`status-badge login ${loginTone(status)}`}><span className="status-mark">{status === "AUTHENTICATED" ? <Check size={11} /> : status === "NONEXISTENT" ? <XCircle size={11} /> : <Clock3 size={11} />}</span>{status === "NOT_STARTED" ? "Never logged in" : labelStatus(status)}</span>; }

function OnboardingModal({ groups, initialGroup, onClose, onSuccess, onError }: { groups: Group[]; initialGroup: string; onClose: () => void; onSuccess: (message: string) => void; onError: (message: string) => void }) {
  const [mode, setMode] = useState<"single" | "bulk">("bulk");
  const [groupId, setGroupId] = useState(initialGroup);
  const [form, setForm] = useState({ email: "", password: "", recovery: "", two_fa: "", proxy: "", proxy_label: "", proxy_region: "" });
  const [rawLines, setRawLines] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [pending, setPending] = useState<"preview" | "import" | "single" | null>(null);
  const [formError, setFormError] = useState("");
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const firstInvalid = useRef<HTMLInputElement | HTMLTextAreaElement | null>(null);
  const update = (key: keyof typeof form, value: string) => { setForm((current) => ({ ...current, [key]: value })); setFormError(""); };
  const toggle = (key: string) => setShown((current) => ({ ...current, [key]: !current[key] }));
  const handlePreview = async () => { if (!rawLines.trim() || !groupId) { setFormError("Choose a group and add at least one import line."); return; } setPending("preview"); setFormError(""); try { setPreview(await api<Preview>("/api/v1/accounts/bulk/preview", { method: "POST", body: JSON.stringify({ raw_lines: rawLines, group_id: groupId }) })); } catch (error) { setFormError((error as Error).message); } finally { setPending(null); } };
  const handleBulkImport = async () => { if (!preview?.valid_rows) return; setPending("import"); setFormError(""); try { const result = await api<{ imported: number; duplicates: number; invalid: number }>("/api/v1/accounts/bulk/import", { method: "POST", body: JSON.stringify({ raw_lines: rawLines, group_id: groupId, idempotency_key: crypto.randomUUID() }) }); setRawLines(""); setPreview(null); onSuccess(`${result.imported} ${result.imported === 1 ? "account" : "accounts"} added and ready for login`); } catch (error) { setFormError((error as Error).message); } finally { setPending(null); } };
  const handleSingle = async (event: FormEvent) => { event.preventDefault(); setFormError(""); const missing = ["email", "password"].find((key) => !form[key as "email" | "password"].trim()) || (!groupId ? "group" : undefined); if (missing) { setFormError(missing === "group" ? "Choose a destination group." : "Enter the Gmail address and password."); firstInvalid.current?.focus(); return; } setPending("single"); try { await api<Account>("/api/v1/accounts/single", { method: "POST", body: JSON.stringify({ ...form, "2fa": form.two_fa, group_id: groupId }) }); onSuccess("Account added and ready for login"); } catch (error) { setFormError((error as Error).message); } finally { setPending(null); } };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal onboarding-modal" role="dialog" aria-modal="true" aria-labelledby="onboarding-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> SECURE INTAKE</div><h2 id="onboarding-title">Add company accounts</h2><p>Credentials are encrypted before they leave the API boundary.</p></div><button type="button" className="icon-button" aria-label="Close add account dialog" onClick={onClose}><X size={18} /></button></div><div className="modal-tabs" role="tablist" aria-label="Account intake method"><button type="button" role="tab" aria-selected={mode === "bulk"} className={mode === "bulk" ? "selected" : ""} onClick={() => { setMode("bulk"); setPreview(null); }}>Bulk import</button><button type="button" role="tab" aria-selected={mode === "single"} className={mode === "single" ? "selected" : ""} onClick={() => { setMode("single"); setPreview(null); }}>Single account</button></div><div className="modal-body">{formError && <div className="form-error" role="alert"><XCircle size={17} /><span>{formError}</span></div>}<div className="field"><label htmlFor="group-select">Destination group <span className="required">Required</span></label><select id="group-select" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="">Select a group</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><small>Groups are capacity-planned around 300 accounts.</small></div>{mode === "bulk" ? <><div className="field"><div className="field-heading"><label htmlFor="raw-lines">Account lines <span className="required">Required</span></label><span className="field-caption">one account per line</span></div><textarea className="resize-none" id="raw-lines" ref={(element) => { firstInvalid.current = element; }} value={rawLines} onChange={(event) => { setRawLines(event.target.value); setPreview(null); setFormError(""); }} placeholder="agent01@company.com:password\nagent02@company.com:password:recovery@example.com\nagent03@company.com:password:recovery@example.com:2fa-secret" aria-describedby="raw-lines-help" spellCheck={false} /></div><div className="security-callout" id="raw-lines-help"><LockKeyhole size={17} /><div><strong>Protected intake</strong><span>Accepted formats: <code>email:password</code>, <code>email:password:recovery</code>, or <code>email:password:recovery:2fa</code>. Lines are encrypted after validation and never shown after import.</span></div></div>{preview && <PreviewPanel preview={preview} />}</> : <form id="single-form" onSubmit={handleSingle} noValidate><div className="form-grid"><Field label="Gmail address" id="email" value={form.email} onChange={(value) => update("email", value)} type="email" autoComplete="off" required inputRef={firstInvalid} /><SecretField label="Password" id="password" value={form.password} onChange={(value) => update("password", value)} visible={!!shown.password} onToggle={() => toggle("password")} autoComplete="new-password" required /><SecretField label="Recovery email or code (optional)" id="recovery" value={form.recovery} onChange={(value) => update("recovery", value)} visible={!!shown.recovery} onToggle={() => toggle("recovery")} autoComplete="off" /><SecretField label="2FA secret (optional)" id="two-fa" value={form.two_fa} onChange={(value) => update("two_fa", value)} visible={!!shown.two_fa} onToggle={() => toggle("two_fa")} autoComplete="off" /></div><div className="form-divider"><span>Environment placeholders</span></div><div className="form-grid"><SecretField label="Proxy connection (optional)" id="proxy" value={form.proxy} onChange={(value) => update("proxy", value)} visible={!!shown.proxy} onToggle={() => toggle("proxy")} autoComplete="off" /><Field label="Proxy label" id="proxy-label" value={form.proxy_label} onChange={(value) => update("proxy_label", value)} placeholder="e.g. US-NY-0142" /><Field label="Proxy region" id="proxy-region" value={form.proxy_region} onChange={(value) => update("proxy_region", value)} placeholder="e.g. New York, US" /></div><small className="field-help">Use <code>ip:port</code> or <code>username:password:ip:port</code>. The connection is encrypted and used only when this account browser opens.</small></form>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button>{mode === "bulk" ? preview ? <button type="button" className="button primary" disabled={pending !== null || preview.valid_rows === 0} aria-busy={pending === "import"} onClick={handleBulkImport}>{pending === "import" && <span className="button-spinner" />}Import {preview.valid_rows} {preview.valid_rows === 1 ? "account" : "accounts"}</button> : <button type="button" className="button primary" disabled={pending !== null} aria-busy={pending === "preview"} onClick={handlePreview}>{pending === "preview" && <span className="button-spinner" />}Review import</button> : <button type="submit" form="single-form" className="button primary" disabled={pending !== null} aria-busy={pending === "single"}>{pending === "single" && <span className="button-spinner" />}Add account</button>}</div></section></div>;
}

function PreviewPanel({ preview }: { preview: Preview }) { return <div className="preview-panel"><div className="preview-summary"><div><strong>Review import</strong><span>{preview.valid_rows} ready · {preview.duplicate_rows} duplicates · {preview.invalid_rows} invalid</span></div><span className={`preview-status ${preview.invalid_rows || preview.duplicate_rows ? "warning" : "ok"}`}>{preview.invalid_rows || preview.duplicate_rows ? "Review needed" : "Ready to import"}</span></div><div className="preview-rows">{preview.rows.map((row) => <div className="preview-row" key={row.row_number}><span className={`row-check ${row.valid ? "ok" : "bad"}`}>{row.valid ? <Check size={12} /> : <X size={12} />}</span><span className="row-number">{String(row.row_number).padStart(2, "0")}</span><strong>{row.email ?? "Unreadable line"}</strong><span className="preview-detail">{row.valid ? `${row.recovery_masked ? `${row.recovery_masked} · ` : ""}${row.two_fa_configured ? "2FA configured" : "No 2FA secret"}` : row.error}</span></div>)}</div></div>; }
function Field({ label, id, value, onChange, type = "text", placeholder, autoComplete, required, inputRef }: { label: string; id: string; value: string; onChange: (value: string) => void; type?: string; placeholder?: string; autoComplete?: string; required?: boolean; inputRef?: React.MutableRefObject<HTMLInputElement | HTMLTextAreaElement | null> }) { return <div className="field"><label htmlFor={id}>{label} {required && <span className="required">Required</span>}</label><input ref={inputRef as React.RefObject<HTMLInputElement>} id={id} type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete={autoComplete} required={required} /></div>; }
function SecretField({ label, id, value, onChange, visible, onToggle, autoComplete, required = false }: { label: string; id: string; value: string; onChange: (value: string) => void; visible: boolean; onToggle: () => void; autoComplete?: string; required?: boolean }) { return <div className="field"><label htmlFor={id}>{label} {required && <span className="required">Required</span>}</label><div className="secret-input"><input id={id} type={visible ? "text" : "password"} value={value} onChange={(event) => onChange(event.target.value)} autoComplete={autoComplete} required={required} /><button type="button" aria-label={visible ? `Hide ${label}` : `Show ${label}`} aria-pressed={visible} onClick={onToggle}>{visible ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></div>; }

function AccountDetail({ account, groups, onClose, onSessionOpened, onUpdated, onPrepared, onError, onResumePlan, onCancelPlan }: { account: Account; groups: Group[]; onClose: () => void; onSessionOpened: (session: Session) => void; onUpdated: () => void; onPrepared: (message: string) => void; onError: (message: string) => void; onResumePlan: (plan: CredentialPlan) => void; onCancelPlan: (plan: CredentialPlan) => void }) {
  const [pending, setPending] = useState(false);
  const [detailAccount, setDetailAccount] = useState(account);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
  const [credentialPlans, setCredentialPlans] = useState<CredentialPlan[]>([]);
  const [detailTab, setDetailTab] = useState<"overview" | "sessions" | "audit">("overview");
  const [detailLoading, setDetailLoading] = useState(true);
  const [detailError, setDetailError] = useState("");
  const [lastRefreshed, setLastRefreshed] = useState<string | null>(null);
  const [existingSession, setExistingSession] = useState<Session | null>(null);
  const [editingCredentials, setEditingCredentials] = useState(false);
  const [operation, setOperation] = useState<OperationKind | null>(null);
  const [adminAction, setAdminAction] = useState<"edit" | "move" | "delete" | null>(null);

  const refreshDetail = useCallback(async () => {
    setDetailLoading(true);
    try {
      const [freshAccount, freshSessions, freshAudit] = await Promise.all([
        api<Account>(`/api/v1/accounts/${account.id}`),
        api<Session[]>(`/api/v1/sessions?account_id=${account.id}&limit=50`),
        api<AuditEvent[]>(`/api/v1/audit-events?account_id=${account.id}&limit=50`),
      ]);
      setDetailAccount(freshAccount);
      setSessions(freshSessions);
      setAuditEvents(freshAudit);
      setCredentialPlans(await api<CredentialPlan[]>(`/api/v1/accounts/${account.id}/credential-change-plans`).catch(() => []));
      setExistingSession(freshSessions.find((session) => isOpenSessionState(session.state)) ?? null);
      setLastRefreshed(new Date().toISOString());
      setDetailError("");
    } catch (error) {
      setDetailError((error as Error).message || "Live account details are temporarily unavailable.");
    } finally {
      setDetailLoading(false);
    }
  }, [account.id]);

  useEffect(() => {
    setDetailAccount(account);
    setDetailTab("overview");
    void refreshDetail();
    const interval = window.setInterval(() => { void refreshDetail(); }, 4000);
    return () => window.clearInterval(interval);
  }, [account, refreshDetail]);

  const openSession = async () => {
    setPending(true);
    try {
      const session = await api<Session>(`/api/v1/accounts/${account.id}/sessions`, { method: "POST", body: JSON.stringify({ purpose: detailAccount.profile.status === "SAVED" ? "reuse" : "login" }) });
      onSessionOpened(session);
      void refreshDetail();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setPending(false);
    }
  };
  const profileValue = detailAccount.profile.id ?? "Reserved for first sign-in";
  const profileNote = detailAccount.profile.status === "SAVED" ? `${detailAccount.profile.storage_reference ?? "Profile storage"} · reopens without login` : detailAccount.profile.status === "IMPORTED" ? "Imported profile is preserved; Gmail sign-in will be checked when opened" : `${labelStatus(detailAccount.profile.status)} · no saved profile yet`;
  const proxyValue = detailAccount.proxy.endpoint ?? detailAccount.proxy.label ?? (detailAccount.proxy.status === "UNASSIGNED" ? "Not assigned" : detailAccount.proxy.id ?? "Assignment unavailable");
  const proxyNote = [detailAccount.proxy.label && detailAccount.proxy.endpoint ? `Label: ${detailAccount.proxy.label}` : null, detailAccount.proxy.region, labelStatus(detailAccount.proxy.status)].filter(Boolean).join(" · ");
  const recentSessions = sessions.slice(0, 3);
  const activeCredentialPlan = credentialPlans.find((plan) => ["DRAFT", "READY_FOR_OPERATOR"].includes(plan.status)) ?? null;
  const latestCredentialPlan = credentialPlans[0] ?? null;
  const detailContent = detailTab === "overview" ? <>
    <div className="detail-overview-grid">
      <div className="detail-overview-card"><span className="detail-label">Record metadata</span><dl className="detail-facts"><div><dt>Email</dt><dd>{detailAccount.email}</dd></div><div><dt>Group</dt><dd>{detailAccount.group_name}</dd></div><div><dt>Lifecycle</dt><dd>{labelStatus(detailAccount.lifecycle_status)}</dd></div><div><dt>Added</dt><dd>{formatDateTime(detailAccount.created_at)}</dd></div><div><dt>Last record update</dt><dd>{formatDateTime(detailAccount.updated_at)}</dd></div></dl></div>
      <div className="detail-overview-card"><div className="detail-section-heading"><span className="detail-label">Latest activity</span><button type="button" className="text-action" onClick={() => setDetailTab("sessions")}>View all</button></div>{recentSessions.length ? <div className="activity-list">{recentSessions.map((session) => <div className="activity-item" key={session.id}><span className={`status-dot ${session.login_state === "AUTHENTICATED" ? "teal" : session.login_state === "NONEXISTENT" ? "danger" : "neutral"}`} /><div><strong>{labelStatus(session.login_state || session.state)}</strong><small>{formatDateTime(session.last_event_at)} · {session.browser_opened ? "Browser open" : "Browser closed"}</small></div></div>)}</div> : <div className="detail-empty">No login sessions have been recorded.</div>}<div className="activity-footer"><span>{auditEvents.length} audit {auditEvents.length === 1 ? "event" : "events"}</span><button type="button" className="text-action" onClick={() => setDetailTab("audit")}>Review audit</button></div></div>
    </div>
    {latestCredentialPlan && <div className={`credential-plan-banner ${activeCredentialPlan ? "active" : ""}`}><LockKeyhole size={17} /><div><strong>Credential change plan v{latestCredentialPlan.version} · {labelStatus(latestCredentialPlan.status)}</strong><span>{latestCredentialPlan.status === "STORED_ENCRYPTED" ? "New credentials stored encrypted. This account now requires a fresh login." : latestCredentialPlan.status === "CANCELLED" ? "This plan was cancelled. No credential material was changed or stored." : latestCredentialPlan.status === "DRAFT" ? "Prepared but not started. Resume it to open the saved Google session." : "Running — " + [latestCredentialPlan.password_requested ? "the password change" : null, latestCredentialPlan.two_fa_requested ? "Two-Step Verification" : null, latestCredentialPlan.recovery_requested ? "recovery rotation" : null].filter(Boolean).join(" + ") + " is automated in the saved Google session; the new material is saved to the profile. Confirm here once it finishes."} Selected: {[latestCredentialPlan.password_requested ? "password" : null, latestCredentialPlan.recovery_requested ? "recovery" : null, latestCredentialPlan.two_fa_requested ? "2FA" : null].filter(Boolean).join(", ")}.</span></div></div>}<div className="detail-note"><ShieldCheck size={17} /><span>Account data is read live from the registry. Credentials remain encrypted and are never returned to this view. Registry actions stay in the action rail so this panel remains a focused record.</span></div>
  </> : detailTab === "sessions" ? <div className="detail-section"><div className="detail-section-heading"><div><span className="detail-label">Session history</span><strong>{sessions.length} recorded {sessions.length === 1 ? "session" : "sessions"}</strong></div><button type="button" className="icon-text-button" onClick={() => void refreshDetail()} disabled={detailLoading}><RefreshCw size={13} className={detailLoading ? "spin" : ""} /> Refresh</button></div>{sessions.length ? <div className="session-history">{sessions.map((session) => <article className="session-history-item" key={session.id}><div className="session-history-main"><div><strong>{labelStatus(session.login_state || session.state)}</strong><span>{session.runtime_type} · {session.browser_opened ? "Browser open" : "Browser closed"}</span></div><LoginBadge status={session.login_state} /></div><div className="session-history-meta"><span>Opened {formatDateTime(session.opened_at)}</span><span>Last event {formatDateTime(session.last_event_at)}</span><span>{session.profile_saved ? "Profile saved" : "Profile not saved"}</span></div>{session.failure_reason && <p className="session-failure">{session.failure_reason}</p>}<code className="session-id">{session.id}</code></article>)}</div> : <div className="detail-empty">No sessions have been recorded for this account.</div>}</div> : <div className="detail-section"><div className="detail-section-heading"><div><span className="detail-label">Audit trail</span><strong>Account and session events</strong></div><button type="button" className="icon-text-button" onClick={() => void refreshDetail()} disabled={detailLoading}><RefreshCw size={13} className={detailLoading ? "spin" : ""} /> Refresh</button></div>{auditEvents.length ? <div className="audit-list">{auditEvents.map((event) => <div className="audit-item" key={event.id}><span className={`audit-result ${event.result.toLowerCase()}`}>{labelStatus(event.result)}</span><div><strong>{labelStatus(event.action)}</strong><small>{labelStatus(event.target_type)} · {formatDateTime(event.created_at)}</small></div><code>{event.target_id}</code></div>)}</div> : <div className="detail-empty">No audit events have been recorded for this account.</div>}</div>;

  return <>
    <div className="modal-backdrop detail-drawer-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="modal detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title">
        <div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT DETAIL</div><h2 id="detail-title">{detailAccount.email}</h2><p>{detailAccount.group_name} · added {formatDate(detailAccount.created_at)}</p></div><div className="detail-header-actions"><span className="live-indicator"><span className={`status-dot ${detailLoading ? "neutral" : "teal"}`} />{detailLoading ? "Refreshing" : `Updated ${lastRefreshed ? formatDateTime(lastRefreshed) : "just now"}`}</span><button type="button" className="icon-button" aria-label="Refresh account detail" onClick={() => void refreshDetail()} disabled={detailLoading}><RefreshCw size={16} className={detailLoading ? "spin" : ""} /></button><button type="button" className="icon-button" aria-label="Close account detail" onClick={onClose}><X size={18} /></button></div></div>
        <div className="detail-body"><div className="detail-status"><div className="detail-status-grid"><div><span className="detail-label">Phase status</span><StatusBadge status={detailAccount.readiness_status} /></div><div><span className="detail-label">Login status</span><LoginBadge status={detailAccount.login_status} /></div><div><span className="detail-label">Gmail status</span><strong className="detail-status-value">{labelStatus(detailAccount.gmail_status)}</strong></div><div><span className="detail-label">Lifecycle</span><strong className="detail-status-value">{labelStatus(detailAccount.lifecycle_status)}</strong></div></div><div className="detail-id"><span>Account ID</span><code>{detailAccount.id}</code><button type="button" className="copy-button" aria-label="Copy account ID" onClick={() => navigator.clipboard?.writeText(detailAccount.id)}><Copy size={14} /></button></div></div>{detailError && <div className="detail-refresh-error" role="status"><XCircle size={15} /> Showing the last known record · {detailError}</div>}<div className="detail-grid"><DetailCard icon={<LockKeyhole size={17} />} label="Credential vault" value={detailAccount.secret_status === "STORED_ENCRYPTED" ? "Encrypted at rest" : labelStatus(detailAccount.secret_status)} note="Raw fields are never returned" /><DetailCard icon={<KeyRound size={17} />} label="Dedicated proxy" value={proxyValue} note={proxyNote} /><DetailCard icon={<Layers3 size={17} />} label="Persistent profile" value={profileValue} note={profileNote} /><DetailCard icon={<Globe2 size={17} />} label="Gmail observation" value={labelStatus(detailAccount.gmail_status)} note="Derived from the latest account session" /></div><div className="secret-summary"><div><span className="detail-label">Recovery</span><strong>{detailAccount.recovery_masked}</strong></div><div><span className="detail-label">2FA</span><strong>{detailAccount.two_fa_configured ? "Configured" : "Not configured"}</strong></div><div><span className="detail-label">Last login signal</span><strong>{labelStatus(detailAccount.login_status)}</strong></div><div><span className="detail-label">Profile state</span><strong>{labelStatus(detailAccount.profile.status)}</strong></div></div><div className="future-tabs" role="tablist" aria-label="Account detail sections"><button type="button" role="tab" aria-selected={detailTab === "overview"} className={detailTab === "overview" ? "selected" : ""} onClick={() => setDetailTab("overview")}>Overview</button><button type="button" role="tab" aria-selected={detailTab === "sessions"} className={detailTab === "sessions" ? "selected" : ""} onClick={() => setDetailTab("sessions")}>Sessions <span>{sessions.length}</span></button><button type="button" role="tab" aria-selected={detailTab === "audit"} className={detailTab === "audit" ? "selected" : ""} onClick={() => setDetailTab("audit")}>Audit <span>{auditEvents.length}</span></button></div><div className="detail-tab-panel" role="tabpanel">{detailContent}</div></div>
        <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close detail</button>{activeCredentialPlan && <button type="button" className="button secondary" onClick={() => onCancelPlan(activeCredentialPlan)}><X size={16} /> Cancel plan</button>}{activeCredentialPlan && <button type="button" className="button primary" onClick={() => onResumePlan(activeCredentialPlan)}><LockKeyhole size={16} /> Resume credential change</button>}{activeCredentialPlan ? null : existingSession ? <button type="button" className="button primary" onClick={() => onSessionOpened(existingSession)}><Globe2 size={16} /> Resume login session</button> : <button type="button" className="button primary" onClick={openSession} disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}<Globe2 size={16} /> Open login session</button>}</div>
      </section>
    </div>
    {editingCredentials && <CredentialEditor account={account} field="password" onClose={() => setEditingCredentials(false)} onSaved={() => { setEditingCredentials(false); onUpdated(); }} onError={onError} />}
    {operation && operation !== "contacts" && <OperationDesigner account={account} kind={operation} onClose={() => setOperation(null)} onSaved={(message) => { setOperation(null); onPrepared(message); }} onError={onError} />}
    {operation === "contacts" && <ContactsImportModal accounts={[account]} onClose={() => setOperation(null)} onPrepared={onPrepared} onError={onError} />}
    {adminAction && <AccountAdminDesigner account={account} groups={groups} kind={adminAction} onClose={() => setAdminAction(null)} onSaved={(message) => { setAdminAction(null); onPrepared(message); onUpdated(); }} onError={onError} />}
  </>;
}

const operationCopy: Record<OperationKind, { title: string; eyebrow: string; description: string; fields: { id: string; label: string; placeholder: string; multiline?: boolean }[] }> = {
  contacts: { title: "Prepare contact upload", eyebrow: "CONTACTS", description: "Define the contact source and import behavior for this account.", fields: [{ id: "source", label: "Source file or list", placeholder: "e.g. partners-q3.csv" }, { id: "mode", label: "Import behavior", placeholder: "Create new · update matches" }] },
  security: { title: "Prepare browser security", eyebrow: "BROWSER SECURITY", description: "Record the approved security settings for a later isolated-profile run.", fields: [{ id: "settings", label: "Settings to review", placeholder: "Enhanced Safe Browsing, recovery review", multiline: true }] },
  labels: { title: "Prepare markers & labels", eyebrow: "MARKERS & LABELS", description: "Describe the label structure the future worker should create and apply.", fields: [{ id: "labels", label: "Label names", placeholder: "Partners\nPriority\nFollow-up", multiline: true }] },
  apps: { title: "Prepare Google app setup", eyebrow: "GOOGLE APPS", description: "Capture the app configuration and option choices for a later profile run.", fields: [{ id: "app", label: "App or art configuration", placeholder: "e.g. Google Art · selected options", multiline: true }] },
  mail: { title: "Prepare send & reply workflow", eyebrow: "MAIL WORKFLOW", description: "Draft the mail operation without sending anything from this frontend surface.", fields: [{ id: "recipients", label: "Recipients or segment", placeholder: "e.g. partner follow-up list" }, { id: "instructions", label: "Send/reply instructions", placeholder: "Subject, body template, reply rules", multiline: true }] },
  addons: { title: "Prepare add-ons", eyebrow: "ADD-ONS", description: "List the approved add-ons to review in the isolated account profile.", fields: [{ id: "addons", label: "Approved add-ons", placeholder: "Add-on names or internal catalog IDs", multiline: true }] },
  alerts: { title: "Prepare Google Alerts", eyebrow: "GOOGLE ALERTS", description: "Choose the topics and delivery preferences for a later account operation.", fields: [{ id: "topics", label: "Topics", placeholder: "Topic one\nTopic two", multiline: true }, { id: "delivery", label: "Delivery preference", placeholder: "Daily digest · as-it-happens" }] },
  preferences: { title: "Prepare account preferences", eyebrow: "ACCOUNT PREFERENCES", description: "Account appearance is executed from the Accounts action bar through Google's own profile-image and Gmail-theme pickers.", fields: [{ id: "selection", label: "Selection", placeholder: "Random Google illustration + random Gmail theme" }] },
};

function OperationCard({ icon, title, note, onClick }: { icon: React.ReactNode; title: string; note: string; onClick: () => void }) { return <button type="button" className="operation-card" onClick={onClick}><span className="operation-icon">{icon}</span><span><strong>{title}</strong><small>{note}</small></span><ArrowRight size={14} /></button>; }

type MailAction = "spam_to_inbox" | "promotions_to_spam" | "reply" | "confirmation_review";
const MAIL_WORKFLOW_ACTIONS = [
  { value: "spam_to_inbox", title: "Recover from Spam", description: "Find messages by subject and move them to Inbox.", icon: MoveRight, singleAccountOnly: false },
  { value: "promotions_to_spam", title: "Report Promotions as spam", description: "Find matching Promotions messages and report them as spam.", icon: ShieldCheck, singleAccountOnly: false },
  { value: "reply", title: "Reply to a message", description: "Open one matching thread and send an approved reply.", icon: Mail, singleAccountOnly: false },
  { value: "confirmation_review", title: "Confirm subscription emails", description: "Open one email for handoff, or confirm a bounded queue by activating each message's confirmation control.", icon: ClipboardCheck, singleAccountOnly: false },
] as const;
const MAIL_REPLY_TEMPLATES = [
  { id: "accept", label: "Accepting a proposal", body: "Thank you for reaching out. I’m happy to confirm that we accept your proposal. Please share the next steps and any information you need from us." },
  { id: "greeting", label: "Professional greeting", body: "Hello, thank you for your message. I hope you’re doing well. I’m pleased to connect and will be happy to help." },
  { id: "salut", label: "Warm salut", body: "Salut, merci pour ton message. J’espère que tu vas bien. Je reviens vers toi rapidement avec les prochaines informations." },
  { id: "refuse", label: "Polite refusal", body: "Thank you for considering us. After reviewing the request, we’re unable to proceed at this time. We appreciate your understanding and wish you every success." },
  { id: "thanks", label: "Thank you", body: "Thank you very much for your message and for the information provided. We appreciate your time and consideration." },
  { id: "received", label: "Confirm receipt", body: "Thank you for sending this through. I confirm that we received your message and will review it carefully before coming back to you." },
  { id: "follow_up", label: "Follow-up needed", body: "Thank you for your email. We’re reviewing the details and will follow up with you as soon as we have a complete update." },
  { id: "clarification", label: "Request clarification", body: "Thank you for your message. Could you please provide a little more detail about the request and the outcome you’re looking for?" },
  { id: "approval", label: "Approval confirmed", body: "Thank you for the update. I confirm that this has been reviewed and approved. Please proceed with the next step when ready." },
  { id: "schedule", label: "Schedule a meeting", body: "Thank you for reaching out. I’d be glad to discuss this further. Please send a few suitable times, and I’ll confirm a meeting slot." },
  { id: "apology", label: "Professional apology", body: "Thank you for your patience. I’m sorry for the delay and any inconvenience caused. We’re addressing this now and will keep you updated." },
  { id: "closing", label: "Professional closing", body: "Thank you again for getting in touch. Please let me know if you need anything else. I look forward to hearing from you." },
];

function MailWorkflowBulkDialog({ accounts, onClose, onFinished, onInterrupted, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onInterrupted: (message: string) => void; onError: (message: string) => void }) {
  type MailRunState = "QUEUED" | "RUNNING" | "COMPLETED" | "REVIEW_QUEUED" | "FAILED" | "ATTENTION";
  const [subject, setSubject] = useState("");
  const [action, setAction] = useState<MailAction>("spam_to_inbox");
  const [matchMode, setMatchMode] = useState<"contains" | "exact">("contains");
  const [maxMessages, setMaxMessages] = useState("100");
  const [concurrency, setConcurrency] = useState("4");
  const [replySelection, setReplySelection] = useState<"specific" | "random">("specific");
  const [replyTemplate, setReplyTemplate] = useState("");
  const [replyBody, setReplyBody] = useState("");
  const [confirmationMode, setConfirmationMode] = useState<"handoff" | "queue">("handoff");
  const [confirmationEmailState, setConfirmationEmailState] = useState<"unread" | "read">("unread");
  const [states, setStates] = useState<Record<string, MailRunState>>(() => Object.fromEntries(accounts.map((account) => [account.id, "QUEUED"] as const)));
  const [confirmationCounts, setConfirmationCounts] = useState<Record<string, { confirmed: number; reviewed: number }>>({});
  const [pending, setPending] = useState(false);
  const [finished, setFinished] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [error, setError] = useState("");
  const jobIdRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const interruptedRef = useRef(false);
  const completionNotified = useRef(false);
  const replyMode = action === "reply";
  const reviewMode = action === "confirmation_review";
  const reviewQueueMode = reviewMode && confirmationMode === "queue";
  const reviewUnavailable = reviewMode && !reviewQueueMode && accounts.length !== 1;
  const confirmationEmailStateLabel = confirmationEmailState === "unread" ? "unread" : "read";
  const workflowHeading = reviewMode ? reviewQueueMode ? "Confirm subscription emails" : "Confirm one email or open it for review" : replyMode ? "Find and reply to messages" : "Find and move messages";
  const actionDescription = reviewMode
    ? reviewQueueMode
      ? `Each saved profile confirms up to the selected number of ${confirmationEmailStateLabel} matching emails by activating the confirmation control rendered in the message, then drives the confirmation page that control opens until it reports a result.`
      : `One ${confirmationEmailStateLabel} matching email is opened in the saved profile and its confirmation control is activated. If it cannot be confirmed, the email stays open for employee review.`
    : replyMode
      ? replySelection === "random"
        ? "The first matching thread in each account receives one independently selected approved reply."
        : "The first matching thread in each account is opened, replied to, and sent through its saved profile."
      : "Each account searches its own Gmail folders, selects up to the per-account limit, and applies the visible toolbar action.";
  const applyProgress = (progress: JobProgress) => {
    setStates(Object.fromEntries(progress.items.map((item) => [item.account_id, item.state === "SUCCEEDED" ? reviewQueueMode ? "REVIEW_QUEUED" : "COMPLETED" : item.state === "ATTENTION" || item.state === "CANCELLED" ? "ATTENTION" : item.state === "FAILED" ? "FAILED" : item.state === "RUNNING" ? "RUNNING" : "QUEUED"])) as Record<string, MailRunState>);
    if (reviewQueueMode) setConfirmationCounts(Object.fromEntries(progress.items.filter((item) => item.reviewed_message_count != null).map((item) => [item.account_id, { confirmed: item.confirmed_messages ?? 0, reviewed: item.reviewed_message_count ?? 0 }])));
  };
  const chooseReplyTemplate = (templateId: string) => { setReplyTemplate(templateId); const template = MAIL_REPLY_TEMPLATES.find((item) => item.id === templateId); if (template) setReplyBody(template.body); };
  const selectAction = (next: MailAction) => { setAction(next); setError(""); if (next === "reply") setMaxMessages("1"); if (next === "confirmation_review") { const queue = accounts.length !== 1; setConfirmationMode(queue ? "queue" : "handoff"); setConfirmationEmailState("unread"); setMaxMessages(queue ? "10" : "1"); } };
  const selectConfirmationMode = (next: "handoff" | "queue") => { setConfirmationMode(next); setError(""); setMaxMessages(next === "handoff" ? "1" : "10"); };
  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (pending || finished) return;
    if (!subject.trim() && !reviewQueueMode) { setError("Enter a subject line to search for."); return; }
    if (reviewUnavailable) { setError("Confirmation review is available for exactly one selected account."); return; }
    if (replyMode && replySelection === "specific" && !replyBody.trim()) { setError("Choose a ready answer or enter a reply message."); return; }
    setPending(true); setError(""); interruptedRef.current = false; jobIdRef.current = null; sessionIdRef.current = null;
    try {
      if (reviewMode && !reviewQueueMode) {
        const account = accounts[0];
        const started = await api<Session>(`/api/v1/accounts/${account.id}/mail-workflow`, { method: "POST", body: JSON.stringify({ subject: subject.trim(), action, match_mode: matchMode, max_messages: 1, review_email_state: confirmationEmailState }) });
        sessionIdRef.current = started.id;
        setStates({ [account.id]: "RUNNING" });
        let session = started;
        for (let attempt = 0; attempt < 120 && !interruptedRef.current; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1000));
          session = await api<Session>(`/api/v1/sessions/${started.id}`);
          if (session.login_state === "MAIL_WORKFLOW_REVIEW_READY") {
            setStates({ [account.id]: "ATTENTION" });
            setPending(false); setFinished(true);
            return;
          }
          if (session.login_state === "MAIL_WORKFLOW_COMPLETED") {
            setStates({ [account.id]: "COMPLETED" });
            setPending(false); setFinished(true);
            return;
          }
          if (session.state === "CLOSED" || session.login_state === "MAIL_WORKFLOW_FAILED") break;
        }
        if (!interruptedRef.current) {
          setStates({ [account.id]: "FAILED" });
          setError("The matching confirmation email could not be confirmed or opened for review. Check the saved session and try again.");
          setPending(false); setFinished(true);
        }
        return;
      }
      const created = await api<LoginJob>("/api/v1/mail-workflow/jobs", { method: "POST", body: JSON.stringify({ account_ids: accounts.map((account) => account.id), concurrency_limit: Math.max(1, Math.min(reviewQueueMode ? 10 : 50, Number(concurrency) || 1)), subject: subject.trim(), action, match_mode: matchMode, max_messages: replyMode ? 1 : Math.max(1, Math.min(100, Number(maxMessages) || 1)), ...(reviewMode ? { review_mode: reviewQueueMode ? "queue" : "handoff", review_email_state: confirmationEmailState } : {}), ...(replyMode ? { reply_mode: replySelection, ...(replySelection === "specific" ? { reply_body: replyBody.trim() } : {}) } : {}) }) });
      jobIdRef.current = created.id;
      const started = await api<LoginJob>(`/api/v1/mail-workflow/jobs/${created.id}/start`, { method: "POST" });
      applyProgress({ ...started, cancelled_count: 0, progress_percent: 0, states: {}, items: started.items });
      let progress = await api<JobProgress>(`/api/v1/mail-workflow/jobs/${created.id}/progress`);
      applyProgress(progress);
      for (let attempt = 0; attempt < 900 && !["COMPLETED", "CANCELLED"].includes(progress.status); attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        if (interruptedRef.current) return;
        progress = await api<JobProgress>(`/api/v1/mail-workflow/jobs/${created.id}/progress`);
        applyProgress(progress);
      }
      setPending(false); setFinished(true);
    } catch (requestError) {
      setPending(false); setError((requestError as Error).message || "Mail workflow could not be started."); onError((requestError as Error).message || "Mail workflow could not be started.");
    }
  };
  const interrupt = async () => {
    if (!pending || interrupting) return;
    interruptedRef.current = true; setInterrupting(true);
    try {
      if (jobIdRef.current) {
        const cancelled = await api<LoginJob>(`/api/v1/mail-workflow/jobs/${jobIdRef.current}/cancel`, { method: "POST" });
        setStates(Object.fromEntries(cancelled.items.map((item) => [item.account_id, item.state === "SUCCEEDED" ? reviewQueueMode ? "REVIEW_QUEUED" : "COMPLETED" : item.state === "FAILED" ? "FAILED" : "ATTENTION"])) as Record<string, MailRunState>);
      } else if (sessionIdRef.current) {
        await api<Session>(`/api/v1/sessions/${sessionIdRef.current}/close`, { method: "POST" });
        setStates({ [accounts[0].id]: "ATTENTION" });
      }
      setInterrupting(false); setPending(false); setFinished(true);
    } catch (requestError) { setInterrupting(false); setError((requestError as Error).message || "The mail workflow could not be interrupted safely."); onError((requestError as Error).message || "The mail workflow could not be interrupted safely."); }
  };
  const doneCount = Object.values(states).filter((state) => ["COMPLETED", "REVIEW_QUEUED", "FAILED", "ATTENTION"].includes(state)).length;
  const completedCount = Object.values(states).filter((state) => state === "COMPLETED" || state === "REVIEW_QUEUED").length;
  const failedCount = Object.values(states).filter((state) => ["FAILED", "ATTENTION"].includes(state)).length;
  const totalConfirmedMessages = Object.values(confirmationCounts).reduce((sum, count) => sum + count.confirmed, 0);
  useEffect(() => {
    if (!finished || completionNotified.current) return;
    completionNotified.current = true;
    if (interruptedRef.current) { onInterrupted("Mail workflow interrupted. Active sessions were closed and unfinished accounts were marked for review."); return; }
    if (reviewMode && !reviewQueueMode) {
      if (states[accounts[0]?.id] === "ATTENTION") onFinished("Confirmation email opened for employee review. The saved browser session remains open on the matched message.");
      else if (states[accounts[0]?.id] === "COMPLETED") onFinished("Confirmation confirmed. The confirmation control in the matched email was activated and the confirmation page reported success.");
      else onError("The matching confirmation email could not be confirmed or opened. Review the account session and retry.");
      return;
    }
    if (reviewQueueMode) {
      if (failedCount) onError(`${totalConfirmedMessages} confirmation email${totalConfirmedMessages === 1 ? "" : "s"} confirmed · ${failedCount} account${failedCount === 1 ? "" : "s"} need review.`);
      else onFinished(`${totalConfirmedMessages} confirmation email${totalConfirmedMessages === 1 ? "" : "s"} confirmed across ${completedCount} account${completedCount === 1 ? "" : "s"}.`);
      return;
    }
    if (failedCount) onError(`${completedCount} mail workflow${completedCount === 1 ? "" : "s"} completed · ${failedCount} account${failedCount === 1 ? "" : "s"} need review.`);
    onFinished(completedCount ? `Mail workflow completed for ${completedCount} account${completedCount === 1 ? "" : "s"}.` : "No mail workflows completed. Review the account sessions and retry.");
  }, [finished, reviewMode, reviewQueueMode, states, accounts, failedCount, completedCount, totalConfirmedMessages, onFinished, onInterrupted, onError]);

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}>
    <section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal="true" aria-labelledby="mail-workflow-bulk-title">
      <div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> MAIL WORKFLOW</div><h2 id="mail-workflow-bulk-title">{workflowHeading}</h2><p>{reviewMode && !reviewQueueMode ? "One saved account session · employee review" : `${accounts.length} account${accounts.length === 1 ? "" : "s"} · ${concurrency} concurrent account sessions`}</p></div><button type="button" className="icon-button" aria-label={pending ? "Interrupt mail workflow" : "Close mail workflow"} onClick={() => { if (pending) void interrupt(); else onClose(); }} disabled={interrupting}><X size={18} /></button></div>
      <form id="mail-workflow-bulk-form" className="modal-body" onSubmit={run} noValidate>
        {error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}
        <div className="security-callout"><Mail size={17} /><div><strong>{reviewMode ? reviewQueueMode ? "Confirmation review queue" : "Employee review required" : "Controlled Gmail action"}</strong><span>{actionDescription}</span></div></div>
        <div className="operation-fields">
          <fieldset className="mail-action-fieldset"><legend>Choose the action</legend><div className="mail-action-grid">{MAIL_WORKFLOW_ACTIONS.map((choice) => { const Icon = choice.icon; return <label className={`mail-action-choice ${action === choice.value ? "is-selected" : ""}`} key={choice.value}><input type="radio" name="mail-workflow-action" value={choice.value} checked={action === choice.value} onChange={() => selectAction(choice.value)} disabled={pending || finished} /><span className="mail-action-choice-icon"><Icon size={16} /></span><span><strong>{choice.title}</strong><small>{choice.description}</small></span></label>; })}</div></fieldset>
          <div className="field"><label htmlFor="mail-workflow-bulk-subject">{reviewMode ? "Confirmation subject" : "Subject line"} {!reviewQueueMode && <span className="required">Required</span>}</label><input id="mail-workflow-bulk-subject" value={subject} onChange={(event) => { setSubject(event.target.value); setError(""); }} placeholder={reviewQueueMode ? "Leave blank to detect subscription confirmations" : reviewMode ? "e.g. Confirm your subscription" : "e.g. Invoice March 2026"} disabled={pending || finished} autoFocus />{reviewQueueMode && <small>Leave blank to find subscription confirmation subjects across publishers; enter text to search a specific subject.</small>}</div>
          <div className="form-grid"><div className="field"><label htmlFor="mail-workflow-bulk-match">Subject match</label><select id="mail-workflow-bulk-match" value={matchMode} onChange={(event) => setMatchMode(event.target.value as "contains" | "exact")} disabled={pending || finished}><option value="contains">Contains the subject text</option><option value="exact">Exact subject line</option></select></div>{reviewMode ? <><div className="field"><label htmlFor="mail-workflow-review-email-state">Email status</label><select id="mail-workflow-review-email-state" value={confirmationEmailState} onChange={(event) => { setConfirmationEmailState(event.target.value as "unread" | "read"); setError(""); }} disabled={pending || finished}><option value="unread">Unread emails only</option><option value="read">Read emails only</option></select><small>Only emails in this Gmail state are opened and confirmed.</small></div><div className="field"><label htmlFor="mail-workflow-review-mode">Review mode</label><select id="mail-workflow-review-mode" value={confirmationMode} onChange={(event) => selectConfirmationMode(event.target.value as "handoff" | "queue")} disabled={pending || finished}><option value="handoff" disabled={accounts.length !== 1}>Open one email for employee review</option><option value="queue">Confirm a queue of matching emails</option></select><small>{reviewQueueMode ? "Each matched email is confirmed one at a time from its own Gmail route; threads already confirmed during this run are not repeated." : "Leaves the matched email open for the employee when it could not be confirmed."}</small></div>{reviewQueueMode ? <><div className="field"><label htmlFor="mail-workflow-bulk-limit">Maximum emails per account</label><input id="mail-workflow-bulk-limit" type="number" min="1" max="100" value={maxMessages} onChange={(event) => setMaxMessages(event.target.value)} disabled={pending || finished} /><small>Stops after this many matched emails are confirmed or reported for review, per account.</small></div><div className="field"><label htmlFor="mail-workflow-bulk-concurrency">Concurrent accounts</label><input id="mail-workflow-bulk-concurrency" type="number" min="1" max={reviewQueueMode ? "10" : "50"} value={concurrency} onChange={(event) => setConcurrency(event.target.value)} disabled={pending || finished} /><small>Maximum browser sessions confirming at once.</small></div></> : <div className="field"><label>Review target</label><div className="field-static">One {confirmationEmailStateLabel} matched email. If it cannot be confirmed, its browser stays open for employee review.</div></div>}</> : <><div className="field"><label htmlFor="mail-workflow-bulk-limit">{replyMode ? "Reply target" : "Maximum messages per account"}</label><input id="mail-workflow-bulk-limit" type="number" min="1" max="100" value={replyMode ? "1" : maxMessages} onChange={(event) => setMaxMessages(event.target.value)} disabled={pending || finished || replyMode} /><small>{replyMode ? "One matching thread per account." : "Applied independently to every account."}</small></div><div className="field"><label htmlFor="mail-workflow-bulk-concurrency">Concurrent accounts</label><input id="mail-workflow-bulk-concurrency" type="number" min="1" max="50" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} disabled={pending || finished} /><small>Maximum sessions open at once; the rest stay queued.</small></div></>}</div>
          {replyMode && <><div className="field"><label htmlFor="mail-workflow-bulk-reply-selection">Reply selection</label><select id="mail-workflow-bulk-reply-selection" value={replySelection} onChange={(event) => setReplySelection(event.target.value as "specific" | "random")} disabled={pending || finished}><option value="specific">Use one specific answer</option><option value="random">Random rotation · one answer per account</option></select></div>{replySelection === "specific" && <div className="field mail-reply-composer"><label htmlFor="mail-workflow-bulk-template">Ready answer</label><select id="mail-workflow-bulk-template" value={replyTemplate} onChange={(event) => chooseReplyTemplate(event.target.value)} disabled={pending || finished}><option value="">Choose a professional starting point</option>{MAIL_REPLY_TEMPLATES.map((template) => <option value={template.id} key={template.id}>{template.label}</option>)}</select><label htmlFor="mail-workflow-bulk-reply">Reply message <span className="required">Required</span></label><textarea className="resize-none" id="mail-workflow-bulk-reply" value={replyBody} onChange={(event) => { setReplyBody(event.target.value); setReplyTemplate(""); setError(""); }} placeholder="Write the reply that should be sent" disabled={pending || finished} rows={6} /></div>}</>}
        </div>
        {pending || finished ? <div className="operation-progress" role="status"><div><span className="detail-label">Progress</span><strong>{doneCount} of {reviewMode && !reviewQueueMode ? 1 : accounts.length} finished</strong></div><div className="dispatch-meter"><span style={{ width: `${(reviewMode && !reviewQueueMode ? 1 : accounts.length) ? (doneCount / (reviewMode && !reviewQueueMode ? 1 : accounts.length)) * 100 : 0}%` }} /></div></div> : <div className="security-callout"><ShieldCheck size={17} /><div><strong>{reviewMode ? "Confirmed only from the message itself" : "Per-account limits"}</strong><span>{reviewMode ? reviewQueueMode ? "Only the confirmation control rendered inside a matched email is activated, and only the confirmation page it opens is followed. A confirmation is reported as confirmed only when that page says so." : "The employee decides whether to act after the matching message is opened." : "Every account runs through its own profile lock. The queue never exceeds the concurrency value."}</span></div></div>}
        {(pending || finished) && <div className="operation-run-list">{accounts.map((account) => <div className="operation-run-item" key={account.id}><span className={`operation-run-status ${states[account.id]?.toLowerCase()}`}>{states[account.id] === "COMPLETED" || states[account.id] === "REVIEW_QUEUED" ? <Check size={13} /> : states[account.id] === "FAILED" || states[account.id] === "ATTENTION" ? <XCircle size={13} /> : states[account.id] === "RUNNING" ? <span className="button-spinner" /> : <Clock3 size={13} />}</span><strong>{account.email}</strong><span>{reviewQueueMode && confirmationCounts[account.id] ? `${confirmationCounts[account.id].confirmed} of ${confirmationCounts[account.id].reviewed} confirmed` : states[account.id] === "REVIEW_QUEUED" ? "Confirmed" : labelStatus(states[account.id] ?? "QUEUED")}</span></div>)}</div>}
      </form>
      <div className="modal-footer">{pending ? <button type="button" className="button danger" onClick={() => void interrupt()} disabled={interrupting}>{interrupting ? "Interrupting…" : "Interrupt workflow"}</button> : <button type="button" className="button secondary" onClick={onClose}>{finished ? "Close" : "Cancel"}</button>}{!finished && !pending && <button type="submit" form="mail-workflow-bulk-form" className="button primary" disabled={(!subject.trim() && !reviewQueueMode) || reviewUnavailable || (replyMode && replySelection === "specific" && !replyBody.trim())}>{reviewMode ? reviewQueueMode ? `Scan ${accounts.length} account${accounts.length === 1 ? "" : "s"}` : "Open email for review" : replyMode ? `Reply for ${accounts.length} account${accounts.length === 1 ? "" : "s"}` : `Run for ${accounts.length} account${accounts.length === 1 ? "" : "s"}`}</button>}</div>
    </section>
  </div>;
}

function MailWorkflowModal({ accounts, onClose, onFinished, onInterrupted, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onInterrupted: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  return <MailWorkflowBulkDialog accounts={accounts} onClose={onClose} onFinished={onFinished} onInterrupted={onInterrupted} onError={onError} />;
}

function LegacyMailWorkflowModal({ accounts, onClose, onFinished, onInterrupted, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onInterrupted: (message: string) => void; onError: (message: string) => void }) {
  type MailRunState = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "ATTENTION";
  const [subject, setSubject] = useState("");
  const [action, setAction] = useState<MailAction>("spam_to_inbox");
  const [matchMode, setMatchMode] = useState<"contains" | "exact">("contains");
  const [maxMessages, setMaxMessages] = useState("100");
  const [concurrency, setConcurrency] = useState("4");
  const [replySelection, setReplySelection] = useState<"specific" | "random">("specific");
  const [replyTemplate, setReplyTemplate] = useState("");
  const [replyBody, setReplyBody] = useState("");
  const [states, setStates] = useState<Record<string, MailRunState>>(() => Object.fromEntries(accounts.map((account) => [account.id, "QUEUED"] as const)));
  const [pending, setPending] = useState(false);
  const [finished, setFinished] = useState(false);
  const [interrupting, setInterrupting] = useState(false);
  const [error, setError] = useState("");
  const completionNotified = useRef(false);
  const abortRef = useRef(false);
  const interruptedRef = useRef(false);
  const jobIdRef = useRef<string | null>(null);

  const run = async (event: FormEvent) => {
    event.preventDefault();
    if (pending || finished) return;
    if (!subject.trim()) { setError("Enter a subject line to search for."); return; }
    if (action === "reply" && replySelection === "specific" && !replyBody.trim()) { setError("Choose a ready answer or enter a reply message."); return; }
    abortRef.current = false;
    interruptedRef.current = false;
    jobIdRef.current = null;
    setPending(true); setError("");
    const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    const applyProgress = (progress: JobProgress) => {
      setStates(Object.fromEntries(progress.items.map((item) => [item.account_id, item.state === "SUCCEEDED" ? "COMPLETED" : item.state === "ATTENTION" || item.state === "CANCELLED" ? "ATTENTION" : item.state === "FAILED" ? "FAILED" : item.state === "RUNNING" ? "RUNNING" : "QUEUED"])) as Record<string, MailRunState>);
    };
    try {
      const created = await api<LoginJob>("/api/v1/mail-workflow/jobs", { method: "POST", body: JSON.stringify({ account_ids: accounts.map((account) => account.id), concurrency_limit: Math.max(1, Math.min(50, Number(concurrency) || 1)), subject: subject.trim(), action, match_mode: matchMode, max_messages: Number(maxMessages) || 100, ...(action === "reply" ? { reply_mode: replySelection, ...(replySelection === "specific" ? { reply_body: replyBody.trim() } : {}) } : {}) }) });
      jobIdRef.current = created.id;
      applyProgress({ ...created, cancelled_count: 0, progress_percent: 0, states: {}, items: created.items });
      const started = await api<LoginJob>(`/api/v1/mail-workflow/jobs/${created.id}/start`, { method: "POST" });
      applyProgress({ ...started, cancelled_count: 0, progress_percent: 0, states: {}, items: started.items });
      let progress = await api<JobProgress>(`/api/v1/mail-workflow/jobs/${created.id}/progress`);
      applyProgress(progress);
      for (let attempt = 0; attempt < 900 && !["COMPLETED", "CANCELLED"].includes(progress.status); attempt += 1) {
        if (abortRef.current) return;
        await wait(1000);
        if (abortRef.current) return;
        progress = await api<JobProgress>(`/api/v1/mail-workflow/jobs/${created.id}/progress`);
        applyProgress(progress);
      }
      setPending(false);
      setFinished(true);
    } catch (requestError) {
      setPending(false);
      setError((requestError as Error).message || "Mail workflow could not be queued.");
      onError((requestError as Error).message || "Mail workflow could not be queued.");
    }
  };

  const interrupt = async () => {
    if (!pending || interrupting) return;
    abortRef.current = true;
    interruptedRef.current = true;
    setInterrupting(true);
    try {
      if (jobIdRef.current) {
        const cancelled = await api<LoginJob>(`/api/v1/mail-workflow/jobs/${jobIdRef.current}/cancel`, { method: "POST" });
        setStates(Object.fromEntries(cancelled.items.map((item) => [item.account_id, item.state === "SUCCEEDED" ? "COMPLETED" : item.state === "CANCELLED" ? "ATTENTION" : item.state === "FAILED" ? "FAILED" : "ATTENTION"])) as Record<string, MailRunState>);
      }
      setInterrupting(false);
      setPending(false);
      setFinished(true);
    } catch (requestError) {
      setInterrupting(false);
      setError((requestError as Error).message || "The mail workflow could not be interrupted safely.");
      onError((requestError as Error).message || "The mail workflow could not be interrupted safely.");
    }
  };

  const doneCount = Object.values(states).filter((state) => ["COMPLETED", "FAILED", "ATTENTION"].includes(state)).length;
  const completedCount = Object.values(states).filter((state) => state === "COMPLETED").length;
  const failedCount = Object.values(states).filter((state) => ["FAILED", "ATTENTION"].includes(state)).length;
  useEffect(() => {
    if (!finished || completionNotified.current) return;
    completionNotified.current = true;
    if (interruptedRef.current) {
      onInterrupted("Mail workflow interrupted. Active sessions were closed and unfinished accounts were marked for review.");
      return;
    }
    if (failedCount) onError(`${completedCount} mail workflow${completedCount === 1 ? "" : "s"} completed · ${failedCount} account${failedCount === 1 ? "" : "s"} need review.`);
    onFinished(completedCount ? `Mail workflow completed for ${completedCount} account${completedCount === 1 ? "" : "s"}.` : "No mail workflows completed. Review the account sessions and retry.");
  }, [finished, failedCount, completedCount, onFinished, onInterrupted, onError]);

  const replyMode = action === "reply";
  const actionDescription = replyMode
    ? replySelection === "random"
      ? "The workflow searches Gmail by subject, opens the first matching thread, and sends one randomly selected approved reply per account."
      : "The workflow searches Gmail by subject, opens the first matching thread, selects Reply, enters your prepared message, and sends it through the saved profile."
    : "The workflow searches Gmail using its subject filter, selects only matching messages in the chosen source, and applies one visible toolbar action. It never reads or copies message bodies.";
  const chooseReplyTemplate = (templateId: string) => {
    setReplyTemplate(templateId);
    const template = MAIL_REPLY_TEMPLATES.find((item) => item.id === templateId);
    if (template) setReplyBody(template.body);
  };

  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal="true" aria-labelledby="mail-workflow-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> MAIL WORKFLOW</div><h2 id="mail-workflow-title">{replyMode ? "Find and reply to messages" : "Find and move messages"}</h2><p>{accounts.length} account{accounts.length === 1 ? "" : "s"} · each action runs in its saved profile</p></div><button type="button" className="icon-button" aria-label={pending ? "Interrupt mail workflow" : "Close mail workflow"} onClick={() => { if (pending) void interrupt(); else onClose(); }} disabled={interrupting}><X size={18} /></button></div><form id="mail-workflow-form" className="modal-body" onSubmit={run} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><Mail size={17} /><div><strong>Subject-scoped Gmail action</strong><span>{actionDescription}</span></div></div><div className="operation-fields"><div className="field"><label htmlFor="mail-workflow-subject">Subject line <span className="required">Required</span></label><input id="mail-workflow-subject" value={subject} onChange={(event) => { setSubject(event.target.value); setError(""); }} placeholder="e.g. Invoice March 2026" disabled={pending || finished} autoFocus /></div><div className="form-grid"><div className="field"><label htmlFor="mail-workflow-action">Action</label><select id="mail-workflow-action" value={action} onChange={(event) => { const next = event.target.value as MailAction; setAction(next); if (next === "reply") setMaxMessages("1"); }} disabled={pending || finished}><option value="spam_to_inbox">Move from Spam to Inbox</option><option value="promotions_to_spam">Move from Promotions to Spam</option><option value="reply">Reply to matching message</option></select></div><div className="field"><label htmlFor="mail-workflow-match">Subject match</label><select id="mail-workflow-match" value={matchMode} onChange={(event) => setMatchMode(event.target.value as typeof matchMode)} disabled={pending || finished}><option value="contains">Contains the subject text</option><option value="exact">Exact subject line</option></select></div><div className="field"><label htmlFor="mail-workflow-limit">{replyMode ? "Reply target" : "Maximum messages"}</label><input id="mail-workflow-limit" type="number" min="1" max="100" value={replyMode ? "1" : maxMessages} onChange={(event) => setMaxMessages(event.target.value)} disabled={pending || finished || replyMode} /><small>{replyMode ? "One reply is sent to the first matching thread." : "Limit the number of matching messages."}</small></div></div>{replyMode && <><div className="field"><label htmlFor="mail-workflow-reply-selection">Reply selection</label><select id="mail-workflow-reply-selection" value={replySelection} onChange={(event) => setReplySelection(event.target.value as "specific" | "random")} disabled={pending || finished}><option value="specific">Use one specific answer</option><option value="random">Random rotation · one answer per account</option></select><small>Choose a fixed message for every account, or let the app select independently from the approved answer pool.</small></div>{replySelection === "specific" ? <div className="field mail-reply-composer"><label htmlFor="mail-workflow-template">Ready answer</label><select id="mail-workflow-template" value={replyTemplate} onChange={(event) => chooseReplyTemplate(event.target.value)} disabled={pending || finished}><option value="">Choose a professional starting point</option>{MAIL_REPLY_TEMPLATES.map((template) => <option value={template.id} key={template.id}>{template.label}</option>)}</select><label htmlFor="mail-workflow-reply">Reply message <span className="required">Required</span></label><textarea className="resize-none" id="mail-workflow-reply" value={replyBody} onChange={(event) => { setReplyBody(event.target.value); setReplyTemplate(""); setError(""); }} placeholder="Write the reply that should be sent to the matching thread" disabled={pending || finished} rows={6} /><small>Review the message before running. The app sends it only after the subject match opens in the saved account profile.</small></div> : <div className="mail-reply-rotation-note" role="status"><RefreshCw size={17} /><div><strong>Random rotation enabled</strong><span>Each account receives one independently selected answer from the {MAIL_REPLY_TEMPLATES.length}-message professional pool.</span></div></div>}</>}</div>{pending || finished ? <div className="operation-progress" role="status"><div><span className="detail-label">Progress</span><strong>{doneCount} of {accounts.length} finished</strong></div><div className="dispatch-meter"><span style={{ width: `${accounts.length ? (doneCount / accounts.length) * 100 : 0}%` }} /></div></div> : <div className="security-callout"><ShieldCheck size={17} /><div><strong>Safe execution boundary</strong><span>Google challenge pages, missing profiles, and unavailable actions stop that account and remain visible in Sessions for review.</span></div></div>}{(pending || finished) && <div className="operation-run-list">{accounts.map((account) => <div className="operation-run-item" key={account.id}><span className={`operation-run-status ${states[account.id]?.toLowerCase()}`}>{states[account.id] === "COMPLETED" ? <Check size={13} /> : states[account.id] === "FAILED" || states[account.id] === "ATTENTION" ? <XCircle size={13} /> : states[account.id] === "RUNNING" ? <span className="button-spinner" /> : <Clock3 size={13} />}</span><strong>{account.email}</strong><span>{labelStatus(states[account.id] ?? "QUEUED")}</span></div>)}</div>}</form><div className="modal-footer">{pending ? <button type="button" className="button danger" onClick={() => void interrupt()} disabled={interrupting}>{interrupting ? "Interrupting…" : "Interrupt workflow"}</button> : <button type="button" className="button secondary" onClick={onClose}>{finished ? "Close" : "Cancel"}</button>}{!finished && !pending && <button type="submit" form="mail-workflow-form" className="button primary" disabled={!subject.trim() || (replyMode && replySelection === "specific" && !replyBody.trim())}>{replyMode ? `Reply for ${accounts.length} account${accounts.length === 1 ? "" : "s"}` : `Run for ${accounts.length} account${accounts.length === 1 ? "" : "s"}`}</button>}</div></section></div>;
}

function ContactsImportModal({ accounts, onClose, onPrepared, onError, onStarted }: { accounts: Account[]; onClose: () => void; onPrepared: (message: string) => void; onError: (message: string) => void; onStarted?: (session: Session) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [files, setFiles] = useState<ContactFile[]>([]);
  const [selectedFileId, setSelectedFileId] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState("");
  const libraryOnly = accounts.length === 0;

  const loadFiles = async () => {
    try { setFiles(await api<ContactFile[]>("/api/v1/contact-files?limit=100")); setError(""); }
    catch (err) { setError((err as Error).message || "Could not load the contact file library."); }
  };

  useEffect(() => { void loadFiles(); }, []);

  const handleUpload = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setUploading(true); setError("");
    try {
      const formData = new FormData();
      formData.append("file", file);
      const created = await api<ContactFile>("/api/v1/contact-files", { method: "POST", body: formData });
      setFiles((current) => [created, ...current.filter((item) => item.id !== created.id)]);
      setSelectedFileId(created.id);
      onPrepared(`${created.original_filename} is ready with ${created.contact_count} contacts.`);
    } catch (err) { setError((err as Error).message || "The contact file could not be added."); }
    finally { setUploading(false); }
  };

  const handleDelete = async (file: ContactFile) => {
    if (!window.confirm(`Remove ${file.original_filename} from the contact file library?`)) return;
    try { await api<void>(`/api/v1/contact-files/${file.id}`, { method: "DELETE" }); setFiles((current) => current.filter((item) => item.id !== file.id)); if (selectedFileId === file.id) setSelectedFileId(""); onPrepared(`${file.original_filename} removed from the library.`); }
    catch (err) { setError((err as Error).message || "The contact file could not be removed."); }
  };

  const handleImport = async () => {
    if (!selectedFileId) { setError("Choose a ready contact file first."); return; }
    setPending(true);
    setError(""); setProgress("");
    let started = 0;
    const failures: string[] = [];
    let firstSession: Session | null = null;
    for (const account of accounts) {
      setProgress(`Starting ${started + failures.length + 1} of ${accounts.length}…`);
      try {
        const result = await api<{ session_id: string; email_count: number; contact_file_id: string; status: string }>(`/api/v1/accounts/${account.id}/contacts-import`, { method: "POST", body: JSON.stringify({ file_id: selectedFileId }) });
        started += 1;
        if (accounts.length === 1) firstSession = await api<Session>(`/api/v1/sessions/${result.session_id}`);
      } catch { failures.push(account.email); }
    }
    setPending(false); setProgress("");
    if (firstSession && onStarted) onStarted(firstSession);
    if (started) onPrepared(`Contact import started for ${started} ${started === 1 ? "account" : "accounts"}${failures.length ? ` · ${failures.length} could not start` : ""}.`);
    if (failures.length && !started) setError(`No imports started. ${failures[0]} could not open an authenticated profile.`);
    if (started || libraryOnly) onClose();
  };

  const title = libraryOnly ? "Contact file library" : accounts.length === 1 ? "Choose a contact file" : `Choose a contact file · ${accounts.length} accounts`;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="modal contact-library-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="contacts-title">
        <div className="modal-header">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" /> CONTACTS · GOOGLE CONTACTS</div>
            <h2 id="contacts-title">{title}</h2>
            <p>{libraryOnly ? "Keep reusable CSV or vCard files ready for a later account import." : "Select one prepared file. The isolated profile will open contacts.google.com and upload it."}</p>
          </div>
          <button type="button" className="icon-button" aria-label="Close contact file library" onClick={onClose} disabled={pending || uploading}><X size={18} /></button>
        </div>
        <div className="modal-body">
          <div className="security-callout">
            <AddressBook size={17} />
            <div>
              <strong>{libraryOnly ? "Reusable import assets" : "Browser-automated import"}</strong>
              <span>{libraryOnly ? "Upload a contact file once, then select it from the Contacts action for one account, selected accounts, or a whole group." : "The selected file is uploaded through the account’s isolated saved profile. No file contents are copied into account metadata or audit events."}</span>
            </div>
          </div>
          {error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}
          <div className="contact-library-head"><div><span className="detail-label">Ready files</span><strong>{files.length} reusable {files.length === 1 ? "file" : "files"}</strong></div><label className="button secondary small upload-file-button"><Plus size={15} /> {uploading ? "Uploading…" : "Add file"}<input type="file" accept=".csv,.vcf,.txt,text/csv,text/vcard,text/plain" onChange={handleUpload} disabled={pending || uploading} /></label></div>
          {files.length ? <div className="contact-file-list">{files.map((file) => <div className={`contact-file-row ${selectedFileId === file.id ? "selected" : ""}`} key={file.id}><label className="contact-file-select"><input type="radio" name="contact-file" value={file.id} checked={selectedFileId === file.id} onChange={() => setSelectedFileId(file.id)} disabled={pending} /><span className="file-type">{file.original_filename.split(".").pop()?.toUpperCase() ?? "FILE"}</span><span className="contact-file-copy"><strong>{file.original_filename}</strong><small>{file.contact_count} contacts · {formatBytes(file.size_bytes)} · added {formatDate(file.created_at)}{file.last_used_at ? ` · used ${formatDate(file.last_used_at)}` : ""}</small></span></label><button type="button" className="icon-button file-delete" aria-label={`Remove ${file.original_filename}`} onClick={() => void handleDelete(file)} disabled={pending || uploading}><Trash2 size={15} /></button></div>)}</div> : <div className="detail-empty">No ready contact files yet. Add a CSV, VCF, or TXT file to make it available here.</div>}
          {progress && <div className="contact-progress" role="status"><span className="spinner" />{progress}</div>}
          {!libraryOnly && <small className="contact-selection-note">{selectedFileId ? "The selected file will be pushed to each account in this scope, one account at a time." : "Choose a ready file to enable the import."}</small>}
        </div>
        <div className="modal-footer">
          <button type="button" className="button secondary" onClick={onClose} disabled={pending || uploading}>Close</button>
          {!libraryOnly && <button type="button" className="button primary" onClick={() => void handleImport()} disabled={pending || uploading || !selectedFileId}>{pending ? "Starting import…" : `Import to ${accounts.length === 1 ? "account" : `${accounts.length} accounts`}`}</button>}
        </div>
      </section>
    </div>
  );
}

function LabelsMarkersBatchModal({ accounts, onClose, onFinished, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  type RunState = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  const [states, setStates] = useState<Record<string, RunState>>(() => Object.fromEntries(accounts.map((account) => [account.id, "QUEUED"] as const)));
  const [failureEmails, setFailureEmails] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const operationStarted = useRef(false);
  const completionNotified = useRef(false);
  const completionNoticeSent = useRef(false);

  useEffect(() => {
    if (!running || operationStarted.current) return;
    operationStarted.current = true;
    let cursor = 0;
    const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    const runAccount = async () => {
      while (true) {
        const account = accounts[cursor++];
        if (!account) return;
        setStates((current) => ({ ...current, [account.id]: "RUNNING" }));
        try {
          const started = await api<Session>(`/api/v1/accounts/${account.id}/labels-markers`, { method: "POST" });
          let current = started;
          for (let attempt = 0; attempt < 120 && current.state !== "CLOSED"; attempt += 1) {
            await wait(1000);
            current = await api<Session>(`/api/v1/sessions/${started.id}`);
          }
          if (current.login_state === "LABELS_MARKERS_COMPLETED") {
            setStates((state) => ({ ...state, [account.id]: "COMPLETED" }));
          } else {
            setStates((state) => ({ ...state, [account.id]: "FAILED" }));
            setFailureEmails((emails) => [...emails, account.email]);
          }
        } catch {
          setStates((state) => ({ ...state, [account.id]: "FAILED" }));
          setFailureEmails((emails) => [...emails, account.email]);
        }
      }
    };
    const run = async () => {
      await Promise.all(Array.from({ length: Math.min(4, accounts.length) }, () => runAccount()));
      if (completionNotified.current) return;
      completionNotified.current = true;
      setFinished(true);
    };
    void run();
    // This is an explicit background operation. Do not cancel or re-dispatch
    // it when React remounts effects in development Strict Mode.
  }, [accounts, running]);

  const completedCount = Object.values(states).filter((state) => state === "COMPLETED").length;
  const failedCount = Object.values(states).filter((state) => state === "FAILED").length;
  useEffect(() => {
    if (!finished || completionNoticeSent.current) return;
    completionNoticeSent.current = true;
    const message = failedCount ? `Labels & Markers completed for ${completedCount} accounts · ${failedCount} require review.` : `Labels & Markers completed for ${completedCount} accounts.`;
    onFinished(message);
    if (failedCount === accounts.length) onError("No Labels & Markers operations completed. Review the account statuses and retry.");
  }, [finished, failedCount, completedCount, accounts.length, onFinished, onError]);

  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="labels-markers-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> LABELS & MARKERS</div><h2 id="labels-markers-title">Enable Gmail labels & markers</h2><p>{accounts.length} accounts · up to 4 isolated profiles at a time</p></div><button type="button" className="icon-button" aria-label="Close Labels & Markers" onClick={onClose}><X size={18} /></button></div><div className="modal-body"><div className="security-callout"><Tags size={17} /><div><strong>{finished ? "Operation finished" : running ? "Running in saved profiles" : "Ready to start"}</strong><span>Each authenticated profile opens Gmail settings, enables Importance markers, shows Spam under System labels, verifies both changes, and closes its browser session.</span></div></div>{running || finished ? <><div className="operation-progress" role="status"><div><span className="detail-label">Progress</span><strong>{completedCount + failedCount} of {accounts.length} finished</strong></div><div className="dispatch-meter"><span style={{ width: `${accounts.length ? ((completedCount + failedCount) / accounts.length) * 100 : 0}%` }} /></div></div><div className="operation-run-list">{accounts.map((account) => <div className="operation-run-item" key={account.id}><span className={`operation-run-status ${states[account.id]?.toLowerCase()}`}>{states[account.id] === "COMPLETED" ? <Check size={13} /> : states[account.id] === "FAILED" ? <XCircle size={13} /> : states[account.id] === "RUNNING" ? <span className="button-spinner" /> : <Clock3 size={13} />}</span><strong>{account.email}</strong><span>{labelStatus(states[account.id] ?? "QUEUED")}</span></div>)}</div>{failureEmails.length > 0 && <div className="form-error" role="alert"><XCircle size={16} /><span>{failureEmails.length} account{failureEmails.length === 1 ? "" : "s"} could not complete. Their sessions were closed and remain auditable.</span></div>}</> : <div className="dispatch-preview"><strong>{accounts.length} accounts in scope</strong><small>No browser work starts until you press the button below.</small></div>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>{running && !finished ? "Run in background" : finished ? "Close" : "Cancel"}</button>{!running && !finished && <button type="button" className="button primary" onClick={() => setRunning(true)}>Start for {accounts.length} {accounts.length === 1 ? "account" : "accounts"}</button>}</div></section></div>;
}

function SecurityBatchModal({ accounts, onClose, onFinished, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  type RunState = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED";
  const [states, setStates] = useState<Record<string, RunState>>(() => Object.fromEntries(accounts.map((account) => [account.id, "QUEUED"] as const)));
  const [failureEmails, setFailureEmails] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const operationStarted = useRef(false);
  const completionNotified = useRef(false);
  const completionNoticeSent = useRef(false);

  useEffect(() => {
    if (!running || operationStarted.current) return;
    operationStarted.current = true;
    let cursor = 0;
    const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    const runAccount = async () => {
      while (true) {
        const account = accounts[cursor++];
        if (!account) return;
        setStates((current) => ({ ...current, [account.id]: "RUNNING" }));
        try {
          const started = await api<Session>(`/api/v1/accounts/${account.id}/security`, { method: "POST" });
          let current = started;
          for (let attempt = 0; attempt < 120 && current.state !== "CLOSED"; attempt += 1) {
            await wait(1000);
            current = await api<Session>(`/api/v1/sessions/${started.id}`);
          }
          if (current.login_state === "SECURITY_COMPLETED") setStates((state) => ({ ...state, [account.id]: "COMPLETED" }));
          else { setStates((state) => ({ ...state, [account.id]: "FAILED" })); setFailureEmails((emails) => [...emails, account.email]); }
        } catch {
          setStates((state) => ({ ...state, [account.id]: "FAILED" }));
          setFailureEmails((emails) => [...emails, account.email]);
        }
      }
    };
    const run = async () => {
      await Promise.all(Array.from({ length: Math.min(4, accounts.length) }, () => runAccount()));
      if (completionNotified.current) return;
      completionNotified.current = true;
      setFinished(true);
    };
    void run();
    // This explicit operation continues in the background if the dialog is
    // dismissed. The guard also prevents Strict Mode from dispatching twice.
  }, [accounts, running]);

  const completedCount = Object.values(states).filter((state) => state === "COMPLETED").length;
  const failedCount = Object.values(states).filter((state) => state === "FAILED").length;
  useEffect(() => {
    if (!finished || completionNoticeSent.current) return;
    completionNoticeSent.current = true;
    const message = failedCount ? `Browser security completed for ${completedCount} accounts · ${failedCount} require review.` : `Browser security completed for ${completedCount} accounts.`;
    onFinished(message);
    if (failedCount === accounts.length) onError("No browser security operations completed. Review the account statuses and retry.");
  }, [finished, failedCount, completedCount, accounts.length, onFinished, onError]);

  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="security-batch-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> BROWSER SECURITY</div><h2 id="security-batch-title">Enable Enhanced Safe Browsing</h2><p>{accounts.length} accounts · up to 4 isolated profiles at a time</p></div><button type="button" className="icon-button" aria-label="Close browser security" onClick={onClose}><X size={18} /></button></div><div className="modal-body"><div className="security-callout"><ShieldCheck size={17} /><div><strong>{finished ? "Operation finished" : running ? "Running in saved profiles" : "Ready to start"}</strong><span>Each authenticated profile opens Google Account security, enables Enhanced Safe Browsing in the current page language, verifies the setting, and closes its browser session.</span></div></div>{running || finished ? <><div className="operation-progress" role="status"><div><span className="detail-label">Progress</span><strong>{completedCount + failedCount} of {accounts.length} finished</strong></div><div className="dispatch-meter"><span style={{ width: `${accounts.length ? ((completedCount + failedCount) / accounts.length) * 100 : 0}%` }} /></div></div><div className="operation-run-list">{accounts.map((account) => <div className="operation-run-item" key={account.id}><span className={`operation-run-status ${states[account.id]?.toLowerCase()}`}>{states[account.id] === "COMPLETED" ? <Check size={13} /> : states[account.id] === "FAILED" ? <XCircle size={13} /> : states[account.id] === "RUNNING" ? <span className="button-spinner" /> : <Clock3 size={13} />}</span><strong>{account.email}</strong><span>{labelStatus(states[account.id] ?? "QUEUED")}</span></div>)}</div>{failureEmails.length > 0 && <div className="form-error" role="alert"><XCircle size={16} /><span>{failureEmails.length} account{failureEmails.length === 1 ? "" : "s"} could not complete. Their sessions were closed and remain auditable.</span></div>}</> : <div className="dispatch-preview"><strong>{accounts.length} accounts in scope</strong><small>No browser work starts until you press the button below.</small></div>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>{running && !finished ? "Run in background" : finished ? "Close" : "Cancel"}</button>{!running && !finished && <button type="button" className="button primary" onClick={() => setRunning(true)}>Start for {accounts.length} {accounts.length === 1 ? "account" : "accounts"}</button>}</div></section></div>;
}

function AddonsBatchModal({ accounts, onClose, onFinished, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  type RunState = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "ATTENTION";
  const [states, setStates] = useState<Record<string, RunState>>(() => Object.fromEntries(accounts.map((account) => [account.id, "QUEUED"] as const)));
  const [failureEmails, setFailureEmails] = useState<string[]>([]);
  const [attentionEmails, setAttentionEmails] = useState<string[]>([]);
  const [concurrency, setConcurrency] = useState("4");
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const operationStarted = useRef(false);
  const completionNotified = useRef(false);
  const completionNoticeSent = useRef(false);

  useEffect(() => {
    if (!running || operationStarted.current) return;
    operationStarted.current = true;
    let cursor = 0;
    const concurrencyLimit = Math.min(Math.max(1, Number(concurrency) || 1), 10, accounts.length);
    const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    const runAccount = async () => {
      while (true) {
        const account = accounts[cursor++];
        if (!account) return;
        setStates((current) => ({ ...current, [account.id]: "RUNNING" }));
        try {
          const started = await api<Session>(`/api/v1/accounts/${account.id}/addons`, { method: "POST" });
          let current = started;
          for (let attempt = 0; attempt < 240 && !["CLOSED", "NEEDS_ATTENTION"].includes(current.state); attempt += 1) {
            await wait(1000);
            current = await api<Session>(`/api/v1/sessions/${started.id}`);
          }
          if (current.login_state === "ADDONS_COMPLETED") setStates((state) => ({ ...state, [account.id]: "COMPLETED" }));
          else if (current.state === "NEEDS_ATTENTION") { setStates((state) => ({ ...state, [account.id]: "ATTENTION" })); setAttentionEmails((emails) => [...emails, account.email]); }
          else { setStates((state) => ({ ...state, [account.id]: "FAILED" })); setFailureEmails((emails) => [...emails, account.email]); }
        } catch {
          setStates((state) => ({ ...state, [account.id]: "FAILED" }));
          setFailureEmails((emails) => [...emails, account.email]);
        }
      }
    };
    const run = async () => {
      await Promise.all(Array.from({ length: concurrencyLimit }, () => runAccount()));
      if (completionNotified.current) return;
      completionNotified.current = true;
      setFinished(true);
    };
    void run();
    // Starting is explicit; once started, the selected work continues even if
    // the operator closes the progress surface.
  }, [accounts, running]);

  const completedCount = Object.values(states).filter((state) => state === "COMPLETED").length;
  const failedCount = Object.values(states).filter((state) => state === "FAILED").length;
  const attentionCount = Object.values(states).filter((state) => state === "ATTENTION").length;
  const doneCount = completedCount + failedCount + attentionCount;
  useEffect(() => {
    if (!finished || completionNoticeSent.current) return;
    completionNoticeSent.current = true;
    const suffix = attentionCount ? ` · ${attentionCount} require Marketplace approval` : failedCount ? ` · ${failedCount} require review` : "";
    onFinished(`Gmail add-ons completed for ${completedCount} ${completedCount === 1 ? "account" : "accounts"}${suffix}.`);
    if (!completedCount) onError("No Gmail add-on installations completed. Review the account sessions and Marketplace approval requirements.");
  }, [finished, completedCount, failedCount, attentionCount, onFinished, onError]);

  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="addons-batch-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GMAIL ADD-ONS</div><h2 id="addons-batch-title">Install Gmail add-ons</h2><p>{accounts.length} accounts · {concurrency} isolated profiles at a time</p></div><button type="button" className="icon-button" aria-label="Close Gmail add-ons" onClick={onClose}><X size={18} /></button></div><div className="modal-body"><div className="security-callout"><Boxes size={17} /><div><strong>{finished ? "Operation finished" : running ? "Running in saved profiles" : "Ready to start"}</strong><span>Each account opens Gmail, opens Get add-ons, and installs up to three randomly selected Marketplace listings. Third-party permission or billing prompts pause that account for review.</span></div></div>{!running && !finished ? <><div className="operation-fields"><div className="field"><label htmlFor="addons-concurrency">Concurrent profiles</label><select id="addons-concurrency" value={concurrency} onChange={(event) => setConcurrency(event.target.value)}><option value="1">1 · cautious</option><option value="2">2</option><option value="4">4 · recommended</option><option value="6">6</option><option value="10">10 · maximum</option></select></div></div><div className="dispatch-preview"><strong>{accounts.length} accounts in scope</strong><small>No browser work starts until you press the button below.</small></div></> : <><div className="operation-progress" role="status"><div><span className="detail-label">Progress</span><strong>{doneCount} of {accounts.length} finished</strong></div><div className="dispatch-meter"><span style={{ width: `${accounts.length ? (doneCount / accounts.length) * 100 : 0}%` }} /></div></div><div className="operation-run-list">{accounts.map((account) => <div className="operation-run-item" key={account.id}><span className={`operation-run-status ${states[account.id]?.toLowerCase()}`}>{states[account.id] === "COMPLETED" ? <Check size={13} /> : states[account.id] === "FAILED" ? <XCircle size={13} /> : states[account.id] === "ATTENTION" ? <ShieldCheck size={13} /> : states[account.id] === "RUNNING" ? <span className="button-spinner" /> : <Clock3 size={13} />}</span><strong>{account.email}</strong><span>{states[account.id] === "ATTENTION" ? "Needs approval" : labelStatus(states[account.id] ?? "QUEUED")}</span></div>)}</div>{(failureEmails.length > 0 || attentionEmails.length > 0) && <div className="form-error" role="alert"><XCircle size={16} /><span>{failureEmails.length + attentionEmails.length} account{failureEmails.length + attentionEmails.length === 1 ? "" : "s"} need review. Permission prompts were not accepted automatically.</span></div>}</>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>{running && !finished ? "Run in background" : finished ? "Close" : "Cancel"}</button>{!running && !finished && <button type="button" className="button primary" onClick={() => setRunning(true)}>Start for {accounts.length} {accounts.length === 1 ? "account" : "accounts"}</button>}</div></section></div>;
}

function AlertsBatchModal({ accounts, onClose, onFinished, onError }: { accounts: Account[]; onClose: () => void; onFinished: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  type RunState = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "ATTENTION";
  const [topicCount, setTopicCount] = useState("3");
  const [concurrency, setConcurrency] = useState("4");
  const [catalog, setCatalog] = useState<AlertTopic[]>([]);
  const [catalogError, setCatalogError] = useState("");
  const [states, setStates] = useState<Record<string, RunState>>(() => Object.fromEntries(accounts.map((account) => [account.id, "QUEUED"] as const)));
  const [failureEmails, setFailureEmails] = useState<string[]>([]);
  const [attentionEmails, setAttentionEmails] = useState<string[]>([]);
  const [running, setRunning] = useState(false);
  const [finished, setFinished] = useState(false);
  const completionNoticeSent = useRef(false);

  useEffect(() => { api<AlertTopic[]>("/api/v1/alerts/topics").then(setCatalog).catch((error) => setCatalogError((error as Error).message || "The topic catalog could not be loaded.")); }, []);
  useEffect(() => {
    if (!running) return;
    let cancelled = false;
    let cursor = 0;
    const topicPool = catalog.map((item) => item.topic).filter(Boolean).sort(() => Math.random() - 0.5);
    const topicAssignments: Record<string, string[]> = {};
    const usedTopicSets = new Set<string>();
    for (let accountIndex = 0; accountIndex < accounts.length; accountIndex += 1) {
      let topics: string[] = [];
      let signature = "";
      for (let attempt = 0; attempt < 100; attempt += 1) {
        topics = topicPool.slice().sort(() => Math.random() - 0.5).slice(0, Number(topicCount));
        signature = topics.slice().sort().join("\u0001");
        if (!usedTopicSets.has(signature) || attempt === 99) break;
      }
      usedTopicSets.add(signature);
      topicAssignments[accounts[accountIndex].id] = topics;
    }
    const wait = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));
    const runAccount = async () => {
      while (!cancelled) {
        const account = accounts[cursor++];
        if (!account) return;
        setStates((current) => ({ ...current, [account.id]: "RUNNING" }));
        try {
          const started = await api<Session>(`/api/v1/accounts/${account.id}/alerts`, { method: "POST", body: JSON.stringify({ topic_count: Number(topicCount), topics: topicAssignments[account.id] }) });
          let current = started;
          for (let attempt = 0; attempt < 300 && !cancelled && !["CLOSED", "NEEDS_ATTENTION"].includes(current.state); attempt += 1) {
            await wait(1000);
            current = await api<Session>(`/api/v1/sessions/${started.id}`);
          }
          if (current.login_state === "ALERTS_COMPLETED") setStates((state) => ({ ...state, [account.id]: "COMPLETED" }));
          else if (current.state === "NEEDS_ATTENTION") { setStates((state) => ({ ...state, [account.id]: "ATTENTION" })); setAttentionEmails((emails) => [...emails, account.email]); }
          else { setStates((state) => ({ ...state, [account.id]: "FAILED" })); setFailureEmails((emails) => [...emails, account.email]); }
        } catch { setStates((state) => ({ ...state, [account.id]: "FAILED" })); setFailureEmails((emails) => [...emails, account.email]); }
      }
    };
    const run = async () => {
      const workerCount = Math.min(Math.max(1, Number(concurrency) || 1), accounts.length);
      await Promise.all(Array.from({ length: workerCount }, () => runAccount()));
      if (!cancelled) setFinished(true);
    };
    void run();
    return () => { cancelled = true; };
  }, [accounts, catalog, concurrency, running, topicCount]);

  const completedCount = Object.values(states).filter((state) => state === "COMPLETED").length;
  const failedCount = Object.values(states).filter((state) => state === "FAILED").length;
  const attentionCount = Object.values(states).filter((state) => state === "ATTENTION").length;
  const doneCount = completedCount + failedCount + attentionCount;
  useEffect(() => {
    if (!finished || completionNoticeSent.current) return;
    completionNoticeSent.current = true;
    const problems = failedCount + attentionCount;
    onFinished(`Google Alerts completed for ${completedCount} ${completedCount === 1 ? "account" : "accounts"}${problems ? ` · ${problems} require review` : ""}.`);
    if (!completedCount) onError("No Google Alerts were created. Review the affected account sessions and retry.");
  }, [finished, completedCount, failedCount, attentionCount, onFinished, onError]);

  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="alerts-batch-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> GOOGLE ALERTS</div><h2 id="alerts-batch-title">Create Google Alerts</h2><p>{accounts.length} accounts · {concurrency} isolated profiles at a time</p></div><button type="button" className="icon-button" aria-label="Close Google Alerts" onClick={onClose}><X size={18} /></button></div><div className="modal-body">{!running && !finished ? <><div className="security-callout"><Bell size={17} /><div><strong>Randomized from the workspace catalog</strong><span>Each account receives its own random topic selection and adds topics one by one at google.com/alerts. The catalog contains {catalog.length || "…"} active topics.</span></div></div><div className="operation-fields"><div className="field"><label htmlFor="alerts-topic-count">Topics per account</label><select id="alerts-topic-count" value={topicCount} onChange={(event) => setTopicCount(event.target.value)}><option value="1">1 topic</option><option value="2">2 topics</option><option value="3">3 topics</option><option value="5">5 topics</option><option value="10">10 topics · maximum</option></select><small>Each account receives its own random topic set.</small></div><div className="field"><label htmlFor="alerts-concurrency">Concurrent accounts</label><select id="alerts-concurrency" value={concurrency} onChange={(event) => setConcurrency(event.target.value)}><option value="1">1 · cautious</option><option value="2">2</option><option value="4">4 · recommended</option><option value="6">6</option><option value="10">10 · maximum</option></select><small>Maximum number of saved profiles running at once.</small></div></div><div className="alerts-topic-preview"><div className="detail-section-heading"><span className="detail-label">Catalog preview</span><span>{catalog.length} available</span></div><div className="topic-chip-list">{catalog.slice(0, 8).map((item) => <span key={item.id}>{item.topic}</span>)}</div></div>{catalogError && <div className="form-error" role="alert"><XCircle size={16} /><span>{catalogError}</span></div>}</> : <><div className="security-callout"><ShieldCheck size={17} /><div><strong>{finished ? "Operation finished" : "Running in saved profiles"}</strong><span>{concurrency} saved profiles can run concurrently. Google Alerts is opened and each selected topic is submitted and verified separately.</span></div></div><div className="operation-progress" role="status"><div><span className="detail-label">Progress</span><strong>{doneCount} of {accounts.length} finished</strong></div><div className="dispatch-meter"><span style={{ width: `${accounts.length ? (doneCount / accounts.length) * 100 : 0}%` }} /></div></div><div className="operation-run-list">{accounts.map((account) => <div className="operation-run-item" key={account.id}><span className={`operation-run-status ${states[account.id]?.toLowerCase()}`}>{states[account.id] === "COMPLETED" ? <Check size={13} /> : states[account.id] === "FAILED" ? <XCircle size={13} /> : states[account.id] === "ATTENTION" ? <ShieldCheck size={13} /> : states[account.id] === "RUNNING" ? <span className="button-spinner" /> : <Clock3 size={13} />}</span><strong>{account.email}</strong><span>{states[account.id] === "ATTENTION" ? "Needs review" : labelStatus(states[account.id] ?? "QUEUED")}</span></div>)}</div>{(failureEmails.length > 0 || attentionEmails.length > 0) && <div className="form-error" role="alert"><XCircle size={16} /><span>{failureEmails.length + attentionEmails.length} account{failureEmails.length + attentionEmails.length === 1 ? "" : "s"} need review.</span></div>}</>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>{running && !finished ? "Run in background" : "Close"}</button>{!running && !finished && <button type="button" className="button primary" onClick={() => setRunning(true)} disabled={!catalog.length || !!catalogError}>Create alerts</button>}</div></section></div>;
}

function AccountPreferencesBatchModal({ accounts, onClose, onStarted, onError }: { accounts: Account[]; onClose: () => void; onStarted: (session: Session, count: number) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [changeAvatar, setChangeAvatar] = useState(true);
  const [changeTheme, setChangeTheme] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");

  const start = async () => {
    if (!changeAvatar && !changeTheme) { setError("Enable profile image or Gmail theme first."); return; }
    setRunning(true);
    setError("");
    const started: Session[] = [];
    const failures: string[] = [];
    for (const account of accounts) {
      try {
        started.push(await api<Session>(`/api/v1/accounts/${account.id}/preferences/apply`, { method: "POST", body: JSON.stringify({ change_avatar: changeAvatar, change_theme: changeTheme, random_theme: true }) }));
      } catch {
        failures.push(account.email);
      }
    }
    setRunning(false);
    if (failures.length) onError(`${failures.length} account${failures.length === 1 ? "" : "s"} could not start. Review active sessions and retry.`);
    if (started.length) onStarted(started[0], started.length);
    else setError("No account preference process could be started.");
  };

  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="preferences-batch-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT PREFERENCES</div><h2 id="preferences-batch-title">Change account appearance</h2><p>{accounts.length} {accounts.length === 1 ? "account" : "accounts"} · start requests are sent sequentially</p></div><button type="button" className="icon-button" aria-label="Close account preferences" onClick={onClose} disabled={running}><X size={18} /></button></div><div className="modal-body">{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><Palette size={17} /><div><strong>Use Google's own pickers</strong><span>The browser opens Personal info and Gmail Settings, selects a random illustration already offered by Google, then opens Theme → View all, selects a random theme, and saves it. No local image upload is used.</span></div></div><div className="operation-fields"><label className="check-row"><input type="checkbox" checked={changeAvatar} onChange={(event) => setChangeAvatar(event.target.checked)} disabled={running} /><span><strong>Change profile image</strong><small>Choose from Google's Browse Illustrations collection.</small></span></label><label className="check-row"><input type="checkbox" checked={changeTheme} onChange={(event) => setChangeTheme(event.target.checked)} disabled={running} /><span><strong>Change Gmail theme</strong><small>Open Settings → Theme → View all and choose randomly.</small></span></label></div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Safe execution</strong><span>Each selected account keeps its own saved Chrome profile. Requests are sent one by one and each session remains auditable.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={running}>Cancel</button><button type="button" className="button primary" onClick={() => void start()} disabled={running || (!changeAvatar && !changeTheme)}>{running && <span className="button-spinner" />}{running ? "Starting…" : `Start for ${accounts.length} ${accounts.length === 1 ? "account" : "accounts"}`}</button></div></section></div>;
}

function OperationDesigner({ account, kind, onClose, onSaved, onError }: { account: Account; kind: OperationKind; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const copy = operationCopy[kind];
  const [values, setValues] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const submit = async (event: FormEvent) => { event.preventDefault(); if (pending) return; setPending(true); try { await api(`/api/v1/accounts/${account.id}/operation-drafts`, { method: "POST", body: JSON.stringify({ operation_type: kind, configuration: values }) }); onSaved(`${copy.title} saved for ${account.email}`); } catch (error) { onError((error as Error).message); setPending(false); } };
  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal" role="dialog" aria-modal="true" aria-labelledby="operation-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> {copy.eyebrow}</div><h2 id="operation-title">{copy.title}</h2><p>{account.email} · saved workflow draft</p></div><button type="button" className="icon-button" aria-label={`Close ${copy.title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="operation-form" className="modal-body" onSubmit={submit} noValidate><div className="security-callout"><ShieldCheck size={17} /><div><strong>Preparation only</strong><span>{copy.description} Saving stores a typed plan only; no external action is sent or changed.</span></div></div><div className="operation-fields">{copy.fields.map((field) => <div className="field" key={field.id}><label htmlFor={`operation-${field.id}`}>{field.label}</label>{field.multiline ? <textarea className="resize-none" id={`operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} /> : <input id={`operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} />}</div>)}</div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="operation-form" className="button primary" disabled={pending}>{pending ? "Saving…" : "Save workflow draft"}</button></div></section></div>;
}

function BulkOperationDesigner({ accounts, kind, onClose, onSaved, onError }: { accounts: Account[]; kind: OperationKind; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const copy = operationCopy[kind];
  const [values, setValues] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!Object.values(values).some((value) => value.trim())) { setError("Add at least one workflow value before saving."); return; }
    setPending(true); setError("");
    let saved = 0;
    const failures: string[] = [];
    for (const account of accounts) {
      try { await api(`/api/v1/accounts/${account.id}/operation-drafts`, { method: "POST", body: JSON.stringify({ operation_type: kind, configuration: values }) }); saved += 1; }
      catch { failures.push(account.email); }
    }
    setPending(false);
    if (failures.length) { onError(`${saved} saved; ${failures.length} could not be saved. Review the registry and retry the failed accounts.`); }
    if (saved) onSaved(`${copy.title} saved for ${saved} ${saved === 1 ? "account" : "accounts"}`);
    else setError("No workflow drafts were saved. Retry the operation.");
  };
  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="bulk-operation-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> {copy.eyebrow}</div><h2 id="bulk-operation-title">{copy.title}</h2><p>{accounts.length} accounts · one audited plan per account</p></div><button type="button" className="icon-button" aria-label={`Close ${copy.title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="bulk-operation-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><ShieldCheck size={17} /><div><strong>Preparation only</strong><span>{copy.description} This saves the same typed plan as a separate draft for each account. It does not execute an external Google action.</span></div></div><div className="operation-fields">{copy.fields.map((field) => <div className="field" key={field.id}><label htmlFor={`bulk-operation-${field.id}`}>{field.label}</label>{field.multiline ? <textarea className="resize-none" id={`bulk-operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} /> : <input id={`bulk-operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} />}</div>)}</div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="bulk-operation-form" className="button primary" disabled={pending}>{pending ? "Saving drafts…" : `Save for ${accounts.length} accounts`}</button></div></section></div>;
}

function AccountTagsModal({ accounts, onClose, onSaved, onError }: { accounts: Account[]; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [tags, setTags] = useState([...new Set(accounts.flatMap((account) => account.tags ?? []))].join(", "));
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const save = async (event: FormEvent) => {
    event.preventDefault(); setPending(true); setError("");
    const values = [...new Set(tags.split(",").map((tag) => tag.trim()).filter(Boolean))];
    if (values.length > 20 || values.some((tag) => tag.length > 40)) { setError("Use up to 20 tags, with 40 characters or fewer each."); setPending(false); return; }
    let complete = 0;
    for (const account of accounts) { try { await api(`/api/v1/accounts/${account.id}/tags`, { method: "PATCH", body: JSON.stringify({ tags: values }) }); complete += 1; } catch (e) { setError((e as Error).message); break; } }
    setPending(false);
    if (complete === accounts.length) onSaved(`Tags updated for ${complete} ${complete === 1 ? "account" : "accounts"}`);
    else if (complete) onError(`Tags updated for ${complete} of ${accounts.length} accounts; retry the remaining accounts.`);
  };
  return <div className="modal-backdrop" role="presentation"><section className="modal admin-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="account-tags-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT ORGANIZATION</div><h2 id="account-tags-title">Manage account tags</h2><p>{accounts.length} selected account{accounts.length === 1 ? "" : "s"} · use commas between tags</p></div><button type="button" className="icon-button" aria-label="Close tags dialog" onClick={onClose}><X size={18} /></button></div><form id="account-tags-form" className="modal-body" onSubmit={save} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="field"><label htmlFor="account-tags-input">Tags</label><input id="account-tags-input" value={tags} onChange={(event) => setTags(event.target.value)} placeholder="e.g. client-a, priority, review" disabled={pending} /><small>Saving replaces the tags on each selected account. Leave empty to clear them.</small></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="account-tags-form" className="button primary" disabled={pending}>{pending ? "Saving…" : "Save tags"}</button></div></section></div>;
}

function BulkAdminDesigner({ accounts, groups, kind, onClose, onSaved, onPartial }: { accounts: Account[]; groups: Group[]; kind: "edit" | "move" | "delete"; onClose: () => void; onSaved: (message: string) => void; onPartial: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [proxyLabel, setProxyLabel] = useState("");
  const [proxyRegion, setProxyRegion] = useState("");
  const [proxy, setProxy] = useState("");
  const [groupId, setGroupId] = useState(groups[0]?.id ?? "");
  const [confirmation, setConfirmation] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const title = kind === "edit" ? "Edit account metadata" : kind === "move" ? "Move accounts to another group" : "Archive accounts";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (kind === "move" && !groupId) { setError("Choose a destination group."); return; }
    if (kind === "delete" && confirmation.trim().toUpperCase() !== "ARCHIVE") { setError("Type ARCHIVE to confirm this bulk action."); return; }
    setPending(true); setError("");
    let completed = 0;
    const failures: { email: string; reason: string }[] = [];
    for (const account of accounts) {
      try {
        if (kind === "edit") await api(`/api/v1/accounts/${account.id}`, { method: "PATCH", body: JSON.stringify({ ...(proxy.trim() ? { proxy: proxy.trim() } : {}), proxy_label: proxyLabel || null, proxy_region: proxyRegion || null }) });
        if (kind === "move") await api(`/api/v1/accounts/${account.id}/group-move`, { method: "POST", body: JSON.stringify({ destination_group_id: groupId }) });
        if (kind === "delete") await api(`/api/v1/accounts/${account.id}`, { method: "DELETE", body: JSON.stringify({ confirmation_email: account.email }) });
        completed += 1;
      } catch (requestError) { failures.push({ email: account.email, reason: (requestError as Error).message }); }
    }
    setPending(false);
    if (failures.length) {
      const detail = failures.map(({ email, reason }) => `${email}: ${reason}`).join(" · ").slice(0, 900);
      const summary = `${completed} of ${accounts.length} completed; ${failures.length} failed. ${detail}`;
      if (completed) onPartial(summary);
      else setError(summary);
      return;
    }
    onSaved(`${title} completed for ${completed} ${completed === 1 ? "account" : "accounts"}`);
  };
  return <div className="modal-backdrop" role="presentation"><section className={`modal admin-modal bulk-admin-modal ${kind === "delete" ? "delete-modal" : ""}`} role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="bulk-admin-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> REGISTRY ADMINISTRATION</div><h2 id="bulk-admin-title">{title}</h2><p>{accounts.length} accounts · each change is validated and audited individually</p></div><button type="button" className="icon-button" aria-label={`Close ${title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="bulk-admin-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{kind === "edit" && <><div className="form-grid"><div className="field"><label htmlFor="bulk-proxy">Proxy connection</label><input id="bulk-proxy" type="password" value={proxy} onChange={(event) => setProxy(event.target.value)} placeholder="ip:port or username:password:ip:port" disabled={pending} autoComplete="off" /></div><div className="field"><label htmlFor="bulk-proxy-label">Proxy label</label><input id="bulk-proxy-label" value={proxyLabel} onChange={(event) => setProxyLabel(event.target.value)} placeholder="e.g. US-NY-0142" disabled={pending} /></div><div className="field"><label htmlFor="bulk-proxy-region">Proxy region</label><input id="bulk-proxy-region" value={proxyRegion} onChange={(event) => setProxyRegion(event.target.value)} placeholder="e.g. New York, US" disabled={pending} /></div></div><small className="field-help">The connection is encrypted and applied only when each account browser opens.</small></>}{kind === "move" && <div className="field"><label htmlFor="bulk-move-group">Destination group</label><select id="bulk-move-group" value={groupId} onChange={(event) => setGroupId(event.target.value)} disabled={pending}>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><small>The server checks capacity and records each move in audit history.</small></div>}{kind === "delete" && <><div className="delete-warning"><Trash2 size={18} /><div><strong>Archive {accounts.length} accounts</strong><span>Active sessions are closed and the accounts are archived. This cannot be undone from the registry without re-adding credentials.</span></div></div><div className="field"><label htmlFor="bulk-delete-confirm">Type ARCHIVE to confirm</label><input id="bulk-delete-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="ARCHIVE" autoComplete="off" disabled={pending} /></div></>}{kind !== "delete" && <div className="security-callout"><ShieldCheck size={17} /><div><strong>Server-confirmed change</strong><span>The operation runs one account at a time so partial failures remain visible and recoverable.</span></div></div>}</form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="bulk-admin-form" className={`button ${kind === "delete" ? "danger" : "primary"}`} disabled={pending}>{pending ? "Applying changes…" : kind === "delete" ? `Archive ${accounts.length} accounts` : title}</button></div></section></div>;
}

function AccountAdminDesigner({ account, groups, kind, onClose, onSaved, onError }: { account: Account; groups: Group[]; kind: "edit" | "move" | "delete"; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const [groupId, setGroupId] = useState(account.group_id);
  const [confirmation, setConfirmation] = useState("");
  const [proxyLabel, setProxyLabel] = useState(account.proxy.label ?? "");
  const [proxyRegion, setProxyRegion] = useState(account.proxy.region ?? "");
  const [proxy, setProxy] = useState("");
  const [pending, setPending] = useState(false);
  const title = kind === "edit" ? "Edit account metadata" : kind === "move" ? "Move account to another group" : "Delete account";
  const submit = async (event: FormEvent) => { event.preventDefault(); if (pending || (kind === "delete" && confirmation.trim().toLowerCase() !== account.email.toLowerCase())) return; setPending(true); try { if (kind === "edit") await api<Account>(`/api/v1/accounts/${account.id}`, { method: "PATCH", body: JSON.stringify({ ...(proxy.trim() ? { proxy: proxy.trim() } : {}), proxy_label: proxyLabel || null, proxy_region: proxyRegion || null }) }); if (kind === "move") await api<Account>(`/api/v1/accounts/${account.id}/group-move`, { method: "POST", body: JSON.stringify({ destination_group_id: groupId }) }); if (kind === "delete") await api<Account>(`/api/v1/accounts/${account.id}`, { method: "DELETE", body: JSON.stringify({ confirmation_email: confirmation }) }); onSaved(`${title} completed for ${account.email}`); } catch (error) { onError((error as Error).message); setPending(false); } };
  return <div className="modal-backdrop" role="presentation"><section className={`modal admin-modal ${kind === "delete" ? "delete-modal" : ""}`} role="dialog" aria-modal="true" aria-labelledby="admin-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT ADMINISTRATION</div><h2 id="admin-title">{title}</h2><p>{account.email} · server-backed admin action</p></div><button type="button" className="icon-button" aria-label={`Close ${title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="admin-form" className="modal-body" onSubmit={submit} noValidate>{kind === "edit" && <><div className="form-grid"><div className="field"><label htmlFor="edit-email">Gmail address</label><input id="edit-email" value={account.email} readOnly /></div><div className="field"><label htmlFor="edit-proxy">Proxy connection</label><input id="edit-proxy" type="password" value={proxy} onChange={(event) => setProxy(event.target.value)} placeholder="ip:port or username:password:ip:port" disabled={pending} autoComplete="off" /></div><div className="field"><label htmlFor="edit-proxy-label">Proxy label</label><input id="edit-proxy-label" value={proxyLabel} onChange={(event) => setProxyLabel(event.target.value)} placeholder="e.g. US-NY-0142" disabled={pending} /></div><div className="field"><label htmlFor="edit-proxy-region">Proxy region</label><input id="edit-proxy-region" value={proxyRegion} onChange={(event) => setProxyRegion(event.target.value)} placeholder="e.g. New York, US" disabled={pending} /></div></div><small className="field-help">Use <code>ip:port</code> or <code>username:password:ip:port</code>. The connection is encrypted and used only when this profile opens.</small></>}{kind === "move" && <div className="field"><label htmlFor="move-group">Destination group</label><select id="move-group" value={groupId} onChange={(event) => setGroupId(event.target.value)} disabled={pending}>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><small>The server checks destination capacity and records the group change in audit history.</small></div>}{kind === "delete" && <><div className="delete-warning"><Trash2 size={18} /><div><strong>This is a destructive action</strong><span>The account is archived, active sessions are closed, and its profile/secrets remain subject to retention policy.</span></div></div><div className="field"><label htmlFor="delete-confirm">Type the account email to confirm</label><input id="delete-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={account.email} autoComplete="off" disabled={pending} /></div></>}{kind !== "delete" && <div className="security-callout"><ShieldCheck size={17} /><div><strong>Server-confirmed change</strong><span>The API validates the request, writes an audit event, and reloads the account registry after success.</span></div></div>}</form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="admin-form" className={`button ${kind === "delete" ? "danger" : "primary"}`} disabled={pending || (kind === "delete" && confirmation.trim().toLowerCase() !== account.email.toLowerCase())}>{pending ? "Saving…" : kind === "delete" ? "Archive account" : kind === "move" ? "Move account" : "Save metadata"}</button></div></section></div>;
}

const credentialChoices: { field: CredentialField; title: string; description: string; icon: React.ReactNode }[] = [
  { field: "password", title: "Change password", description: "Replace the saved sign-in password.", icon: <KeyRound size={17} /> },
  { field: "recovery", title: "Change recovery", description: "Update the recovery email or recovery code.", icon: <Mail size={17} /> },
  { field: "two_fa", title: "Change 2FA", description: "Replace the stored authenticator secret.", icon: <ShieldCheck size={17} /> },
];

function CredentialChoiceModal({ account, onClose, onPlan }: { account: Account; onClose: () => void; onPlan: (fields: CredentialField[], recoveryEmails: string[]) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [selectedFields, setSelectedFields] = useState<CredentialField[]>([]);
  const [recoveryPool, setRecoveryPool] = useState("");
  const recoveryEmails = recoveryPool.split(/[\n,]+/).map((email) => email.trim()).filter(Boolean);
  const invalidRecovery = recoveryEmails.some((email) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email));
  const recoverySelected = selectedFields.includes("recovery");
  const toggleField = (field: CredentialField) => setSelectedFields((current) => current.includes(field) ? current.filter((item) => item !== field) : [...current, field]);
  const ready = selectedFields.length > 0 && (!recoverySelected || (recoveryEmails.length > 0 && !invalidRecovery));
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal credential-modal credential-choice-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="credential-choice-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CREDENTIAL PLAN</div><h2 id="credential-choice-title">Choose credential changes</h2><p>{account.email} · select one or more fields for this account</p></div><button type="button" className="icon-button" aria-label="Close credential options" onClick={onClose}><X size={18} /></button></div><div className="modal-body"><div className="security-callout"><LockKeyhole size={17} /><div><strong>Prepare the account-specific plan</strong><span>These choices are kept in memory until you submit the plan. Google changes and secret generation require the backend security worker.</span></div></div><div className="credential-checkbox-list" role="group" aria-label="Credential change options">{credentialChoices.map((choice) => <label className={`credential-checkbox ${selectedFields.includes(choice.field) ? "selected" : ""}`} key={choice.field}><input type="checkbox" checked={selectedFields.includes(choice.field)} onChange={() => toggleField(choice.field)} /><span className="credential-choice-icon">{choice.icon}</span><span><strong>{choice.title}</strong><small>{choice.description}</small></span></label>)}</div>{recoverySelected && <div className="field credential-pool"><label htmlFor="recovery-pool">Recovery email rotation pool <span className="required">Required</span></label><textarea className="resize-none" id="recovery-pool" value={recoveryPool} onChange={(event) => setRecoveryPool(event.target.value)} placeholder="recovery-one@example.com\nrecovery-two@example.com" aria-describedby="recovery-pool-help" spellCheck={false} /><small id="recovery-pool-help">One real recovery email per line, or separate them with commas. The backend must assign and audit rotation; this UI does not store them persistently.</small>{invalidRecovery && <span className="field-error" role="alert">Every recovery entry must be a valid email address.</span>}</div>}<p className="credential-reauth-note">Any applied change must invalidate the current profile and require a fresh login. Bulk execution remains disabled until the backend job and authorization policy are connected.</p></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="button" className="button primary" disabled={!ready} onClick={() => onPlan(selectedFields, recoveryEmails)}>Prepare secure plan</button></div></section></div>;
}

function CredentialEditor({ account, field, onClose, onSaved, onError }: { account: Account; field: CredentialField; onClose: () => void; onSaved: () => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [value, setValue] = useState("");
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const choice = credentialChoices.find((item) => item.field === field) ?? credentialChoices[0];
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (!value.trim()) { setError(`Enter a new ${field === "two_fa" ? "2FA secret" : field === "recovery" ? "recovery value" : "password"}.`); return; }
    setPending(true);
    try { await api<Account>(`/api/v1/accounts/${account.id}/credentials`, { method: "PUT", body: JSON.stringify({ [field === "two_fa" ? "2fa" : field]: value.trim() }) }); onSaved(); }
    catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); }
    finally { setPending(false); }
  };
  const inputType = field === "recovery" ? "text" : shown[field] ? "text" : "password";
  const [googleConfirmed, setGoogleConfirmed] = useState(false);
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) onClose(); }}><section className="modal credential-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="credential-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ENCRYPTED REVISION</div><h2 id="credential-title">{choice.title}</h2><p>{account.email} · the previous revision remains audit-visible, never readable</p></div><button type="button" className="icon-button" aria-label="Close credential editor" onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="credential-editor" className="modal-body" onSubmit={save} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><LockKeyhole size={17} /><div><strong>Manual Google handoff</strong><span>Complete this change inside the account’s saved Google session first. This screen records the new value only after your confirmation.</span></div></div><div className="credential-handoff"><span className="detail-label">Required before saving</span><strong>Update {field === "two_fa" ? "2FA" : field === "recovery" ? "the recovery value" : "the password"} in Google</strong><small>Open or resume this account’s saved session from the Login action, finish Google’s security checks, then return here.</small><label className="credential-confirm"><input type="checkbox" checked={googleConfirmed} onChange={(event) => setGoogleConfirmed(event.target.checked)} disabled={pending} /><span>I completed and confirmed the change in Google</span></label></div><div className="credential-stack"><div className="field"><label htmlFor="revision-value">New {field === "two_fa" ? "2FA secret" : field === "recovery" ? "recovery value" : "password"} <span className="required">Required</span></label><div className="secret-input"><input id="revision-value" type={inputType} value={value} onChange={(event) => { setValue(event.target.value); setError(""); }} autoComplete={field === "password" ? "new-password" : "off"} disabled={pending} autoFocus /><button type="button" aria-label={shown[field] ? `Hide ${choice.title}` : `Show ${choice.title}`} aria-pressed={!!shown[field]} onClick={() => setShown((current) => ({ ...current, [field]: !current[field] }))} disabled={pending}>{shown[field] ? <EyeOff size={16} /> : <Eye size={16} />}</button></div>{field === "recovery" && <small>Use the new recovery email or recovery value accepted by the account.</small>}</div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="credential-editor" className="button primary" disabled={pending || !googleConfirmed} aria-busy={pending}>{pending && <span className="button-spinner" />}Save encrypted revision</button></div></section></div>;
}

function LoginBatchModal({ groups, initialGroup, accountIds = [], onClose, onStarted, onError }: { groups: Group[]; initialGroup: string; accountIds?: string[]; onClose: () => void; onStarted: (job: LoginJob) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [groupId, setGroupId] = useState(initialGroup);
  const [concurrency, setConcurrency] = useState("4");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!groupId) { setError("Choose a group to queue."); return; }
    setPending(true); setError("");
    try {
      const created = await api<LoginJob>("/api/v1/login-jobs", { method: "POST", body: JSON.stringify({ group_id: accountIds.length ? null : groupId, account_ids: accountIds, concurrency_limit: Number(concurrency) }) });
      const started = await api<LoginJob>(`/api/v1/login-jobs/${created.id}/start`, { method: "POST" });
      onStarted(started);
    } catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); }
    finally { setPending(false); }
  };
  const group = groups.find((candidate) => candidate.id === groupId);
  const targetCount = accountIds.length || group?.ready_count || 0;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal batch-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="batch-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CONTROLLED DISPATCH</div><h2 id="batch-title">Queue a login batch</h2><p>Open isolated saved profiles in waves. Each account keeps its own session lock.</p></div><button type="button" className="icon-button" aria-label="Close login batch dialog" onClick={onClose}><X size={18} /></button></div><form id="batch-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{!accountIds.length && <div className="field"><label htmlFor="batch-group">Account group <span className="required">Required</span></label><select id="batch-group" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="">Select a group</option>{groups.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name} · {candidate.ready_count}/{candidate.capacity} ready</option>)}</select></div>}<div className="batch-control"><div><label htmlFor="concurrency">Concurrent profiles</label><small>How many account sessions can be open at the same time.</small></div><input id="concurrency" type="number" min="1" max="50" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} /></div><div className="dispatch-preview"><div><span className="detail-label">Dispatch preview</span><strong>{targetCount} accounts in scope</strong></div><div className="dispatch-meter" aria-hidden="true"><span style={{ width: `${Math.min(100, (targetCount / Math.max(1, group?.capacity ?? 300)) * 100)}%` }} /></div><small>Remaining accounts stay queued until a running profile finishes or the batch is dispatched again.</small></div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Challenge-safe behavior</strong><span>Normal sign-in work can continue in isolated profiles. CAPTCHA, MFA, and unknown pages pause the affected account for operator attention.</span></div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" form="batch-form" className="button primary" disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}Open controlled batch</button></div></section></div>;
}

function LoginJobWorkspace({ job, onUpdate, onClose, onError }: { job: LoginJob; onUpdate: (job: LoginJob) => void; onClose: () => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    if (!["RUNNING", "QUEUED"].includes(job.status) && !job.items.some((item) => item.state === "ATTENTION" && item.session_id)) return;
    const timer = window.setInterval(() => { api<LoginJob>(`/api/v1/login-jobs/${job.id}/progress`).then(onUpdate).catch(() => undefined); }, 4000);
    return () => window.clearInterval(timer);
  }, [job.id, job.status, job.attention_count, onUpdate]);
  const action = async (name: "dispatch-next" | "pause" | "resume" | "cancel") => {
    setPending(name);
    try { onUpdate(await api<LoginJob>(`/api/v1/login-jobs/${job.id}/${name}`, { method: "POST" })); }
    catch (requestError) { const message = (requestError as Error).message; onError(message); }
    finally { setPending(null); }
  };
  const completedCount = job.succeeded_count + job.attention_count + job.failed_count;
  const progress = job.total_count ? Math.round((completedCount / job.total_count) * 100) : 0;
  return <div className="modal-backdrop" role="presentation"><section className="modal job-modal" role="dialog" aria-modal="true" aria-labelledby="job-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> BATCH CONTROL</div><h2 id="job-title">Login batch progress</h2><p>{job.total_count} accounts · concurrency {job.concurrency_limit} · {labelStatus(job.status)}</p></div><button type="button" className="icon-button" aria-label="Close batch workspace" onClick={onClose}><X size={18} /></button></div><div className="job-body"><div className="job-progress-head"><div><span className="detail-label">Completion</span><strong>{completedCount} of {job.total_count}</strong></div><span className="job-percent">{progress}%</span></div><div className="job-progress" aria-label={`${progress}% complete`}><span style={{ width: `${progress}%` }} /></div><div className="job-stats"><JobStat label="Running" value={job.running_count} tone="running" /><JobStat label="Queued" value={job.queued_count} tone="queued" /><JobStat label="Completed" value={job.succeeded_count} tone="done" /><JobStat label="Attention" value={job.attention_count} tone="attention" /></div><div className="job-list" aria-label="Account batch progress"><div className="job-list-head"><span>ACCOUNT</span><span>STATE</span></div>{job.items.map((item) => <div className="job-item" key={item.id}><div><strong>{item.account_email}</strong><small>{item.paused_reason ?? item.last_error ?? (item.session_id ? `Session ${item.session_id}` : "Waiting for an isolated profile")}</small></div><span className={`job-item-state ${item.state.toLowerCase()}`}>{item.state === "RUNNING" && item.paused_reason ? "Waiting for verification" : labelStatus(item.state)}</span></div>)}</div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Audit-safe control</strong><span>Each profile is isolated and locked to one account. Google security challenges pause only the affected item.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Keep workspace open</button>{job.status === "RUNNING" && <button type="button" className="button secondary" onClick={() => action("pause")} disabled={pending !== null} aria-busy={pending === "pause"}>Pause batch</button>}{job.status === "PAUSED" && <button type="button" className="button secondary" onClick={() => action("resume")} disabled={pending !== null} aria-busy={pending === "resume"}><Play size={16} /> Resume batch</button>}{job.status === "RUNNING" && job.queued_count > 0 && <button type="button" className="button secondary" onClick={() => action("dispatch-next")} disabled={pending !== null} aria-busy={pending === "dispatch-next"}><Play size={16} /> Dispatch next wave</button>}{["QUEUED", "RUNNING", "PAUSED"].includes(job.status) && <button type="button" className="button danger" onClick={() => action("cancel")} disabled={pending !== null} aria-busy={pending === "cancel"}>Cancel batch</button>}</div></section></div>;
}

function JobStat({ label, value, tone }: { label: string; value: number; tone: string }) { return <div className={`job-stat ${tone}`}><span>{label}</span><strong>{value}</strong></div>; }

function SessionWorkspace({ session, credentialPlan = null, onConfirmPlan, onUpdate, onClose, onError }: { session: Session; credentialPlan?: CredentialPlan | null; onConfirmPlan: () => void; onUpdate: (session: Session) => void; onClose: () => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [pending, setPending] = useState<"complete" | "verify" | "close" | null>(null);
  useEffect(() => {
    let mounted = true;
    const pollSession = async () => {
      try {
        const current = await api<Session>(`/api/v1/sessions/${session.id}`);
        if (!mounted) return;
        onUpdate(current);
        if (current.state === "CLOSED") onClose();
      } catch {
        // Keep the current workspace visible during a transient refresh error.
        // The explicit close control remains available to the operator.
      }
    };
    const timer = window.setInterval(() => void pollSession(), 1500);
    return () => { mounted = false; window.clearInterval(timer); };
  }, [session.id]);
  const updateEvent = async (eventType: "LOGIN_COMPLETED" | "VERIFICATION_REQUIRED") => {
    setPending(eventType === "LOGIN_COMPLETED" ? "complete" : "verify");
    try { onUpdate(await api<Session>(`/api/v1/sessions/${session.id}/events`, { method: "POST", body: JSON.stringify({ event_type: eventType }) })); } catch (error) { onError((error as Error).message); } finally { setPending(null); }
  };
  const close = async () => {
    setPending("close");
    try { onUpdate(await api<Session>(`/api/v1/sessions/${session.id}/close`, { method: "POST" })); onClose(); } catch (error) { onError((error as Error).message); setPending(null); }
  };
  const isOpen = isOpenSessionState(session.state);
  const isAddonOperation = session.purpose === "addons";
  const isPreferencesOperation = session.purpose === "account_preferences";
  const isCredentialChange = Boolean(credentialPlan);
  const sessionTitle = isCredentialChange ? `Credential change — ${session.account_email}` : isAddonOperation ? `Install Gmail add-ons for ${session.account_email}` : isPreferencesOperation ? `Apply account preferences for ${session.account_email}` : `Sign in to ${session.account_email}`;
  const firstStepTitle = isCredentialChange ? "The credential change runs automatically" : isAddonOperation || isPreferencesOperation ? "Use the saved authenticated profile" : session.login_state === "PROFILE_CHECK" ? "Check imported browser profile" : "Manual sign-in in the separate browser";
  const secondStepTitle = isCredentialChange ? "Confirm here to store the new credentials" : isAddonOperation ? "Verify the Marketplace result" : isPreferencesOperation ? "Verify the Gmail preference result" : "Record the browser result";
  return <div className="modal-backdrop" role="presentation"><section className="modal session-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="session-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROFILE-BACKED SESSION</div><h2 id="session-title">{sessionTitle}</h2><p>Session {session.id} · profile lock {session.profile_id}</p></div><button type="button" className="icon-button" aria-label="Close session workspace" onClick={onClose}><X size={18} /></button></div><div className="session-body"><div className="session-hero"><div className="session-orbit"><Globe2 size={24} /></div><div><span className="detail-label">Current session state</span><strong className={`session-state ${session.state.toLowerCase()}`}>{labelStatus(session.state)}</strong><p>{session.login_state === "VERIFICATION_REQUIRED" ? `${session.failure_reason ?? "Verification is required."} Complete it in the separate Chrome window; this session will continue when Google clears the challenge.` : isAddonOperation && session.state === "NEEDS_ATTENTION" ? (session.failure_reason ?? "Marketplace approval is required in the separate browser window.") : session.browser_opened ? (session.automation_hint ?? "A separate standard Chrome session is open with the saved browser profile.") : session.login_state === "REQUIRED" ? "The isolated standard Chrome session is launching for manual sign-in." : session.login_state === "ACCOUNT_NOT_FOUND" ? "Google reports that no account exists for this email address." : session.login_state === "AUTHENTICATED" ? "The browser reported a completed sign-in. Keep the profile open only while needed." : session.failure_reason ?? "Review the account in the controlled browser session."}</p></div></div><div className="session-grid"><DetailCard icon={<Layers3 size={17} />} label="Persistent profile" value={session.profile_status} note={session.profile_saved ? "Saved · reopens without login" : session.login_state === "PROFILE_CHECK" ? "Imported · checking saved sign-in in Gmail" : "Created once the sign-in completes"} /><DetailCard icon={<LockKeyhole size={17} />} label="Profile lock" value={isOpen ? "Locked to this session" : "Released"} note={session.profile_storage_reference} /><DetailCard icon={<ShieldCheck size={17} />} label="Runtime boundary" value={session.browser_opened ? "Separate Chrome session" : "Human hand-off"} note="Separate from the SaaS console" /></div><div className="session-steps"><div className={`session-step ${session.login_state === "REQUIRED" ? "current" : "done"}`}><span>01</span><div><strong>{firstStepTitle}</strong><small>{isAddonOperation ? "The operation opens Gmail and the Workspace Marketplace in this account's saved profile." : isPreferencesOperation ? "The operation opens Google Account and Gmail settings in this account's saved profile." : isCredentialChange ? "Google's security page for this account is open in the separate Chrome window. Complete the password, recovery, and 2FA changes there." : session.browser_opened ? (session.automation_hint ?? "Enter the saved account credentials in the separate standard Chrome window.") : "The isolated standard Chrome window is launching for manual sign-in."}</small></div></div><div className={`session-step ${session.login_state === "AUTHENTICATED" ? "done" : "current"}`}><span>02</span><div><strong>{secondStepTitle}</strong><small>{isAddonOperation ? "The observer closes the session only after up to three installations are verified." : isPreferencesOperation ? "The observer selects the requested localized controls and closes after the result is settled." : isCredentialChange ? "Once the Google-side changes are done, confirm back here to store the new credentials encrypted and provision the authenticator secret." : "Only record completion after Google visibly confirms the sign-in."}</small></div></div></div><div className="session-warning"><ShieldCheck size={17} /><span>This browser window is separate from the SaaS console. Passwords, recovery values, and 2FA secrets remain in the encrypted vault and are never injected into this interface.</span></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Keep session open</button>{isOpen && <>{session.browser_opened ? <span className="button secondary session-opened"><Globe2 size={16} /> Separate Chrome window open</span> : <span className="button secondary session-opened"><Globe2 size={16} /> Isolated browser launching</span>}{isCredentialChange ? <button type="button" className="button primary" onClick={onConfirmPlan}><LockKeyhole size={16} /> Confirm credential change</button> : !isAddonOperation && !isPreferencesOperation && <><button type="button" className="button secondary" onClick={() => updateEvent("VERIFICATION_REQUIRED")} disabled={pending !== null || session.login_state === "VERIFICATION_REQUIRED"} aria-busy={pending === "verify"}>Needs verification</button><button type="button" className="button primary" onClick={() => updateEvent("LOGIN_COMPLETED")} disabled={pending !== null || (session.login_state === "VERIFICATION_REQUIRED" && session.observer_attached)} aria-busy={pending === "complete"}>{pending === "complete" && <span className="button-spinner" />}<Check size={16} /> Record sign-in complete</button></>}<button type="button" className="button danger" onClick={close} disabled={pending !== null} aria-busy={pending === "close"}><Power size={16} /> Close session</button></>}</div></section></div>;
}
function CredentialPlanConfirmModal({ plan, accountEmail, onClose, onConfirmed, onError }: { plan: CredentialPlan; accountEmail: string; onClose: () => void; onConfirmed: (provisioned: string | null) => void; onError: (message: string) => void }) {
  const inspectorEmbedded = useDesktopInspectorPresentation();
  const [acknowledged, setAcknowledged] = useState(false);
  const [recovery, setRecovery] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [doneResult, setDoneResult] = useState<CredentialPlan | null>(null);
  const ready = acknowledged;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!acknowledged) { setError("Confirm that the credential change was completed."); return; }
    setPending(true); setError("");
    try {
      const result = await api<CredentialPlan>(`/api/v1/credential-change-plans/${plan.id}/confirm`, { method: "POST", body: JSON.stringify({ acknowledged, ...(plan.recovery_requested ? { recovery: recovery.trim() } : {}) }) });
      setDoneResult(result);
    } catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); } finally { setPending(false); }
  };
  if (doneResult) {
    const secret = doneResult.provisioned_two_fa_secret;
    return <div className="modal-backdrop credential-backdrop" role="presentation"><section className="modal credential-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="confirm-done-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CREDENTIAL CHANGE STORED</div><h2 id="confirm-done-title">{secret ? "Authenticator secret provisioned" : "Credentials stored encrypted"}</h2><p>{accountEmail} · revision v{doneResult.version} · {secret ? "a one-time secret was generated" : "new values encrypted at rest"}</p></div></div><div className="modal-body">{secret ? <div className="security-callout provisioned-secret"><LockKeyhole size={17} /><div><strong>Save this TOTP secret now</strong><small>Copy it into your authenticator app before closing this screen. It was also written to twofa-secret.txt in the account's profile folder.</small></div></div> : <div className="security-callout"><ShieldCheck size={17} /><div><strong>New credentials are encrypted in the vault</strong><small>The account now requires a fresh login for the saved browser profile.</small></div></div>}{secret && <div className="provisioned-secret-display"><code>{secret}</code><button type="button" className="text-button copy-button" onClick={() => navigator.clipboard?.writeText(secret)}>Copy secret</button></div>}</div><div className="modal-footer"><button type="button" className="button primary" onClick={() => onConfirmed(secret ?? null)}>Done</button></div></section></div>;
  }
  return <div className="modal-backdrop credential-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal credential-modal" role="dialog" aria-modal={!inspectorEmbedded} aria-labelledby="confirm-plan-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CONFIRM CREDENTIAL CHANGE</div><h2 id="confirm-plan-title">Confirm the automated change</h2><p>{accountEmail} · plan v{plan.version}</p></div><button type="button" className="icon-button" aria-label="Close confirm dialog" onClick={onClose}><X size={18} /></button></div><form id="confirm-plan-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><ShieldCheck size={17} /><div><strong>Automated Google change</strong><span>{plan.password_requested ? "A new password was generated, entered into Google's password-change page, and saved to new-password.txt inside the account's profile folder." : "The account's Google credentials were changed in the saved session."}{plan.two_fa_requested ? " A new authenticator secret was generated and saved to twofa-secret.txt inside the profile folder." : ""}</span></div></div>{plan.recovery_requested && <div className="field"><label htmlFor="confirm-recovery">New recovery address <span className="field-caption">optional</span></label><small className="field-caption">The app already entered the new address into Google and stores that exact value automatically{plan.recovery_hint ? ` (hint: ${plan.recovery_hint})` : ""}. Only type one if you changed it yourself in Google.</small><input id="confirm-recovery" type="text" value={recovery} onChange={(event) => setRecovery(event.target.value)} placeholder="Leave blank to use the address the app entered" disabled={pending} /></div>}<label className="credential-confirm"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} disabled={pending} /><span>Confirm — store the new credentials encrypted in the vault</span></label></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="button" className="button primary" onClick={() => void submit({ preventDefault: () => {} } as FormEvent)} disabled={!ready || pending} aria-busy={pending}>{pending && <span className="button-spinner" />}Confirm and store</button></div></section></div>;
}

function DetailCard({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) { return <div className="detail-card"><span className="detail-card-icon">{icon}</span><span className="detail-label">{label}</span><strong>{value}</strong><small>{note}</small></div>; }
function ToastRegion({ toasts }: { toasts: Toast[] }) { return <div className="toast-region" aria-live="polite" aria-atomic="true">{toasts.map((toast) => <div className={`toast ${toast.tone}`} key={toast.id}><span>{toast.tone === "success" ? <Check size={15} /> : toast.tone === "error" ? <XCircle size={15} /> : <ShieldCheck size={15} />}</span>{toast.message}</div>)}</div>; }

export default App;
