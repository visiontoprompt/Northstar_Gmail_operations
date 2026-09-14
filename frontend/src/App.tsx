import { FormEvent, useEffect, useRef, useState } from "react";
import {
  Activity,
  ArrowRight,
  Check,
  ChevronDown,
  CircleHelp,
  ClipboardCheck,
  Clock3,
  Copy,
  Database,
  Eye,
  EyeOff,
  Filter,
  Globe2,
  KeyRound,
  Layers3,
  LockKeyhole,
  Menu,
  Plus,
  Power,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
  UsersRound,
  X,
  XCircle,
} from "lucide-react";
import { isOpenSessionState } from "./session-contract";

type Group = { id: string; name: string; description: string | null; capacity: number; account_count: number; ready_count: number; issue_count: number; status: string };
type Account = { id: string; email: string; group_id: string; group_name: string; lifecycle_status: string; readiness_status: string; login_status: string; gmail_status: string; secret_status: string; recovery_masked: string; two_fa_configured: boolean; proxy: { id: string | null; label: string | null; region: string | null; status: string }; profile: { id: string | null; storage_reference: string | null; status: string }; created_at: string; updated_at: string };
type Session = { id: string; account_id: string; account_email: string; profile_id: string; profile_storage_reference: string; profile_status: string; state: string; login_state: string; runtime_type: string; session_url: string; opened_at: string; last_event_at: string; closed_at: string | null; failure_reason: string | null };
type Preview = { total_rows: number; valid_rows: number; duplicate_rows: number; invalid_rows: number; rows: { row_number: number; email?: string | null; recovery_masked?: string | null; two_fa_configured: boolean; valid: boolean; duplicate: boolean; error?: string | null }[] };
type Toast = { id: number; tone: "success" | "error" | "info"; message: string };

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, { headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) }, ...options });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload.detail === "string" ? payload.detail : "The request could not be completed.");
  }
  return response.json() as Promise<T>;
};

const formatDate = (value: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
const labelStatus = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());

function App() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [selectedGroup, setSelectedGroup] = useState("all");
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("ALL");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [isOnboardingOpen, setOnboardingOpen] = useState(false);
  const [activeAccount, setActiveAccount] = useState<Account | null>(null);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [mobileNav, setMobileNav] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => { document.title = activeSession ? "Login session — Gmail Operations" : activeAccount ? "Account detail — Gmail Operations" : "Account registry — Gmail Operations"; }, [activeAccount, activeSession]);

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
      const params = new URLSearchParams({ page: "1", page_size: "50" });
      if (query.trim()) params.set("q", query.trim());
      if (selectedGroup !== "all") params.set("group_id", selectedGroup);
      if (statusFilter !== "ALL") params.set("status", statusFilter);
      try {
        const data = await api<{ items: Account[]; total: number }>(`/api/v1/accounts?${params.toString()}`, { signal: controller.signal });
        if (!controller.signal.aborted) { setAccounts(data.items); setTotal(data.total); setLoading(false); }
      } catch (requestError) {
        if ((requestError as Error).name !== "AbortError" && !controller.signal.aborted) { setLoading(false); setError("Accounts are unavailable. Retry the connection."); }
      }
    }, 300);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [query, selectedGroup, statusFilter, refreshKey]);

  const notify = (tone: Toast["tone"], message: string) => {
    const id = Date.now();
    setToasts((current) => [...current.filter((toast) => toast.message !== message), { id, tone, message }]);
    window.setTimeout(() => setToasts((current) => current.filter((toast) => toast.id !== id)), 4500);
  };

  const reload = () => setRefreshKey((current) => current + 1);
  const visibleCount = selectedGroup === "all" ? groups.reduce((sum, group) => sum + group.account_count, 0) : groups.find((group) => group.id === selectedGroup)?.account_count ?? 0;
  const readyCount = selectedGroup === "all" ? groups.reduce((sum, group) => sum + group.ready_count, 0) : groups.find((group) => group.id === selectedGroup)?.ready_count ?? 0;

  return (
    <div className="app-shell">
      <aside className={`sidebar ${mobileNav ? "sidebar-open" : ""}`} aria-label="Primary navigation">
        <div className="brand-lockup"><div className="brand-mark"><Layers3 size={18} /></div><div><strong>Northstar</strong><span>Gmail operations</span></div></div>
        <div className="workspace-switcher"><span className="workspace-label">WORKSPACE</span><div className="workspace-button" role="status"><span className="workspace-avatar">NO</span><span>Northstar Ops</span><ChevronDown size={15} /></div></div>
        <nav className="nav-groups">
          <NavLabel>Workspace</NavLabel>
          <NavItem icon={<Activity size={17} />} label="Overview" />
          <NavItem active icon={<Database size={17} />} label="Accounts" badge={total || undefined} />
          <NavItem icon={<UsersRound size={17} />} label="Account groups" />
          <NavItem icon={<UserRound size={17} />} label="Employees" />
          <NavLabel>Operations</NavLabel>
          <NavItem icon={<ShieldCheck size={17} />} label="Issues" badge="0" mutedBadge />
          <NavItem icon={<ClipboardCheck size={17} />} label="Account factory" badge="—" mutedBadge />
          <NavItem icon={<Clock3 size={17} />} label="Sessions" />
          <NavLabel>Infrastructure</NavLabel>
          <NavItem icon={<KeyRound size={17} />} label="Proxies" />
          <NavItem icon={<Layers3 size={17} />} label="Browser profiles" />
          <NavItem icon={<SlidersHorizontal size={17} />} label="Settings" />
        </nav>
        <div className="sidebar-footer"><div className="security-note"><LockKeyhole size={15} /><span>Credentials encrypted at rest</span></div><div className="user-card"><div className="user-avatar">AM</div><div><strong>Alex Morgan</strong><span>Administrator</span></div><CircleHelp size={16} className="help-icon" /></div></div>
      </aside>
      {mobileNav && <button type="button" className="sidebar-scrim" aria-label="Close navigation" onClick={() => setMobileNav(false)} />}

      <main className="main-content">
        <header className="topbar"><button type="button" className="icon-button mobile-menu" aria-label="Open navigation" onClick={() => setMobileNav(true)}><Menu size={20} /></button><div className="breadcrumbs"><span>Workspace</span><ArrowRight size={14} /><strong>Accounts</strong></div><div className="topbar-actions"><div className="connection-state"><span className="pulse-dot" /> API connected</div><div className="avatar-button" role="img" aria-label="Signed in as Alex Morgan">AM</div></div></header>

        <div className="page-content">
          <section className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> PHASE 1 · ACCOUNT REGISTRY</div><h1>Account onboarding</h1><p>Store company accounts safely, then hand them off to the login phase.</p></div><div className="heading-actions"><button type="button" className="button secondary" onClick={() => setOnboardingOpen(true)}><Plus size={17} /> Add account</button><button type="button" className="button primary" onClick={() => setOnboardingOpen(true)}><ClipboardCheck size={17} /> Import accounts</button></div></section>
          <section className="handoff-banner" aria-label="Phase 1 boundary"><div className="handoff-rail" /><div className="handoff-icon"><ShieldCheck size={19} /></div><div className="handoff-copy"><strong>Safe hand-off to Phase 2</strong><span>New records are encrypted, deduplicated, and ready for a profile-backed human login session.</span></div><span className="handoff-state">BOUNDARY LOCKED</span></section>

          <section className="summary-grid" aria-label="Account registry summary"><SummaryCard label="Accounts in view" value={visibleCount.toString()} note="Current group scope" icon={<Database size={17} />} /><SummaryCard label="Ready for login" value={readyCount.toString()} note="Stored and validated" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Groups" value={groups.length.toString()} note="300 account capacity" icon={<Layers3 size={17} />} /><SummaryCard label="Credential health" value="100%" note="Encrypted at rest" icon={<LockKeyhole size={17} />} tone="amber" /></section>

          <section className="registry-panel"><div className="panel-header"><div><h2>Account registry</h2><p>Choose a group to inspect its account boundary.</p></div><div className="group-context"><span>GROUP CONTEXT</span><label className="select-wrap"><select aria-label="Select account group" value={selectedGroup} onChange={(event) => setSelectedGroup(event.target.value)}><option value="all">All groups · {groups.reduce((sum, group) => sum + group.account_count, 0)} accounts</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><ChevronDown size={15} /></label></div></div>
            <div className="toolbar"><label className="search-field"><Search size={17} /><span className="sr-only">Search accounts</span><input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) event.preventDefault(); }} placeholder="Search by email or group" />{query && <button type="button" className="clear-search" aria-label="Clear search" onClick={() => { setQuery(""); searchRef.current?.focus(); }}><X size={15} /></button>}</label><div className="toolbar-right"><label className="filter-select"><Filter size={15} /><span className="sr-only">Filter account status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter account status"><option value="ALL">All statuses</option><option value="READY_FOR_LOGIN">Ready for login</option><option value="SETUP_REQUIRED">Setup required</option></select></label><button type="button" className="icon-button" aria-label="Refresh accounts" onClick={reload}><RefreshCw size={16} /></button></div></div>
            {error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={reload}>Retry</button></div>}
            <div className="result-meta" role="status"><span>{loading ? "Refreshing account registry…" : `${total} ${total === 1 ? "account" : "accounts"} in this view`}</span>{query && <span className="search-context">for “{query}”</span>}</div>
            <AccountTable accounts={accounts} loading={loading} onOpen={setActiveAccount} onAdd={() => setOnboardingOpen(true)} />
            <div className="table-footer"><span>Showing {accounts.length ? 1 : 0}–{accounts.length} of {total}</span><span className="pagination-note">Server pagination ready · view capped at 50</span></div>
          </section>
          <footer className="page-footer"><span>Northstar Operations</span><span>Phase 1 registry · v0.1</span><a href="/docs" onClick={(event) => event.preventDefault()}>API docs <ArrowRight size={13} /></a></footer>
        </div>
      </main>
      {isOnboardingOpen && <OnboardingModal groups={groups} initialGroup={selectedGroup !== "all" ? selectedGroup : groups[0]?.id ?? ""} onClose={() => setOnboardingOpen(false)} onSuccess={(message) => { setOnboardingOpen(false); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
      {activeAccount && <AccountDetail account={activeAccount} onClose={() => setActiveAccount(null)} onSessionOpened={setActiveSession} onError={(message) => notify("error", message)} />}
      {activeSession && <SessionWorkspace session={activeSession} onUpdate={(session) => { setActiveSession(session); reload(); }} onClose={() => { setActiveSession(null); reload(); }} onError={(message) => notify("error", message)} />}
      <ToastRegion toasts={toasts} />
    </div>
  );
}

function NavLabel({ children }: { children: string }) { return <div className="nav-label">{children}</div>; }
function NavItem({ icon, label, active, badge, mutedBadge }: { icon: React.ReactNode; label: string; active?: boolean; badge?: string | number; mutedBadge?: boolean }) { return <button type="button" className={`nav-item ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} disabled={!active}>{icon}<span>{label}</span>{badge !== undefined && <span className={`nav-badge ${mutedBadge ? "muted" : ""}`}>{badge}</span>}</button>; }
function SummaryCard({ label, value, note, icon, tone = "default" }: { label: string; value: string; note: string; icon: React.ReactNode; tone?: string }) { return <div className={`summary-card ${tone}`}><div className="summary-top"><span>{label}</span><span className="summary-icon">{icon}</span></div><strong>{value}</strong><small>{note}</small></div>; }

function AccountTable({ accounts, loading, onOpen, onAdd }: { accounts: Account[]; loading: boolean; onOpen: (account: Account) => void; onAdd: () => void }) {
  if (loading) return <div className="table-state loading-state"><span className="spinner" /><strong>Loading account registry</strong><span>Keeping the table stable while the latest view arrives.</span></div>;
  if (!accounts.length) return <div className="table-state empty-state"><div className="empty-icon"><Database size={21} /></div><strong>No accounts in this view</strong><span>Add an account or adjust the group and search filters.</span><button type="button" className="button primary small" onClick={onAdd}><Plus size={15} /> Add account</button></div>;
  return <div className="table-scroll"><table><caption className="sr-only">Company Gmail account registry</caption><thead><tr><th scope="col">Account</th><th scope="col">Group</th><th scope="col">Phase status</th><th scope="col">Secrets</th><th scope="col">Proxy</th><th scope="col">Profile</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead><tbody>{accounts.map((account) => <tr key={account.id}><td className="account-cell"><button type="button" className="account-link" onClick={() => onOpen(account)}><span className="account-avatar">{account.email.slice(0, 2).toUpperCase()}</span><span><strong>{account.email}</strong><small>Added {formatDate(account.created_at)}</small></span></button></td><td><span className="group-tag"><span className="tag-dot" />{account.group_name}</span></td><td><StatusBadge status={account.readiness_status} /></td><td><span className="security-cell"><LockKeyhole size={14} /> Encrypted</span></td><td><span className="metadata-cell"><span className="status-dot neutral" /> {account.proxy.status === "UNASSIGNED" ? "Reserved" : account.proxy.status}</span></td><td><span className="metadata-cell"><span className="status-dot teal" /> {account.profile.status === "RESERVED" ? "Placeholder ready" : account.profile.status}</span></td><td><button type="button" className="row-open" aria-label={`Open ${account.email}`} onClick={() => onOpen(account)}><ArrowRight size={16} /></button></td></tr>)}</tbody></table></div>;
}
function StatusBadge({ status }: { status: string }) { return <span className={`status-badge ${status === "READY_FOR_LOGIN" ? "ready" : "setup"}`}><span className="status-mark">{status === "READY_FOR_LOGIN" ? <Check size={11} /> : <Clock3 size={11} />}</span>{labelStatus(status)}</span>; }

function OnboardingModal({ groups, initialGroup, onClose, onSuccess, onError }: { groups: Group[]; initialGroup: string; onClose: () => void; onSuccess: (message: string) => void; onError: (message: string) => void }) {
  const [mode, setMode] = useState<"single" | "bulk">("bulk");
  const [groupId, setGroupId] = useState(initialGroup);
  const [form, setForm] = useState({ email: "", password: "", recovery: "", two_fa: "", proxy_label: "", proxy_region: "" });
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
  const handleSingle = async (event: FormEvent) => { event.preventDefault(); setFormError(""); const required: (keyof typeof form)[] = ["email", "password", "recovery", "two_fa"]; const missing = required.find((key) => !form[key].trim()) || (!groupId ? "group" : undefined); if (missing) { setFormError(missing === "group" ? "Choose a destination group." : "Complete all required credential fields."); firstInvalid.current?.focus(); return; } setPending("single"); try { await api<Account>("/api/v1/accounts/single", { method: "POST", body: JSON.stringify({ ...form, "2fa": form.two_fa, group_id: groupId }) }); onSuccess("Account added and ready for login"); } catch (error) { setFormError((error as Error).message); } finally { setPending(null); } };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal onboarding-modal" role="dialog" aria-modal="true" aria-labelledby="onboarding-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> SECURE INTAKE</div><h2 id="onboarding-title">Add company accounts</h2><p>Credentials are encrypted before they leave the API boundary.</p></div><button type="button" className="icon-button" aria-label="Close add account dialog" onClick={onClose}><X size={18} /></button></div><div className="modal-tabs" role="tablist" aria-label="Account intake method"><button type="button" role="tab" aria-selected={mode === "bulk"} className={mode === "bulk" ? "selected" : ""} onClick={() => { setMode("bulk"); setPreview(null); }}>Bulk import</button><button type="button" role="tab" aria-selected={mode === "single"} className={mode === "single" ? "selected" : ""} onClick={() => { setMode("single"); setPreview(null); }}>Single account</button></div><div className="modal-body">{formError && <div className="form-error" role="alert"><XCircle size={17} /><span>{formError}</span></div>}<div className="field"><label htmlFor="group-select">Destination group <span className="required">Required</span></label><select id="group-select" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="">Select a group</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><small>Groups are capacity-planned around 300 accounts.</small></div>{mode === "bulk" ? <><div className="field"><div className="field-heading"><label htmlFor="raw-lines">Account lines <span className="required">Required</span></label><span className="field-caption">one account per line</span></div><textarea className="resize-none" id="raw-lines" ref={(element) => { firstInvalid.current = element; }} value={rawLines} onChange={(event) => { setRawLines(event.target.value); setPreview(null); setFormError(""); }} placeholder="agent01@company.com:password:recovery@company.com:2fa-secret\nagent02@company.com:password:recovery@company.com:2fa-secret" aria-describedby="raw-lines-help" spellCheck={false} /></div><div className="security-callout" id="raw-lines-help"><LockKeyhole size={17} /><div><strong>Protected intake</strong><span>Lines are used in memory for validation, then encrypted. They are not stored in browser history, logs, toasts, or audit metadata.</span></div></div>{preview && <PreviewPanel preview={preview} />}</> : <form id="single-form" onSubmit={handleSingle} noValidate><div className="form-grid"><Field label="Gmail address" id="email" value={form.email} onChange={(value) => update("email", value)} type="email" autoComplete="off" required inputRef={firstInvalid} /><SecretField label="Password" id="password" value={form.password} onChange={(value) => update("password", value)} visible={!!shown.password} onToggle={() => toggle("password")} autoComplete="new-password" /><SecretField label="Recovery email or code" id="recovery" value={form.recovery} onChange={(value) => update("recovery", value)} visible={!!shown.recovery} onToggle={() => toggle("recovery")} autoComplete="off" /><SecretField label="2FA secret" id="two-fa" value={form.two_fa} onChange={(value) => update("two_fa", value)} visible={!!shown.two_fa} onToggle={() => toggle("two_fa")} autoComplete="off" /></div><div className="form-divider"><span>Environment placeholders</span></div><div className="form-grid"><Field label="Dedicated proxy label" id="proxy-label" value={form.proxy_label} onChange={(value) => update("proxy_label", value)} placeholder="e.g. US-NY-0142" /><Field label="Proxy region" id="proxy-region" value={form.proxy_region} onChange={(value) => update("proxy_region", value)} placeholder="e.g. New York, US" /></div></form>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button>{mode === "bulk" ? preview ? <button type="button" className="button primary" disabled={pending !== null || preview.valid_rows === 0} aria-busy={pending === "import"} onClick={handleBulkImport}>{pending === "import" && <span className="button-spinner" />}Import {preview.valid_rows} {preview.valid_rows === 1 ? "account" : "accounts"}</button> : <button type="button" className="button primary" disabled={pending !== null} aria-busy={pending === "preview"} onClick={handlePreview}>{pending === "preview" && <span className="button-spinner" />}Review import</button> : <button type="submit" form="single-form" className="button primary" disabled={pending !== null} aria-busy={pending === "single"}>{pending === "single" && <span className="button-spinner" />}Add account</button>}</div></section></div>;
}

function PreviewPanel({ preview }: { preview: Preview }) { return <div className="preview-panel"><div className="preview-summary"><div><strong>Review import</strong><span>{preview.valid_rows} ready · {preview.duplicate_rows} duplicates · {preview.invalid_rows} invalid</span></div><span className={`preview-status ${preview.invalid_rows || preview.duplicate_rows ? "warning" : "ok"}`}>{preview.invalid_rows || preview.duplicate_rows ? "Review needed" : "Ready to import"}</span></div><div className="preview-rows">{preview.rows.map((row) => <div className="preview-row" key={row.row_number}><span className={`row-check ${row.valid ? "ok" : "bad"}`}>{row.valid ? <Check size={12} /> : <X size={12} />}</span><span className="row-number">{String(row.row_number).padStart(2, "0")}</span><strong>{row.email ?? "Unreadable line"}</strong><span className="preview-detail">{row.valid ? `${row.recovery_masked} · 2FA configured` : row.error}</span></div>)}</div></div>; }
function Field({ label, id, value, onChange, type = "text", placeholder, autoComplete, required, inputRef }: { label: string; id: string; value: string; onChange: (value: string) => void; type?: string; placeholder?: string; autoComplete?: string; required?: boolean; inputRef?: React.MutableRefObject<HTMLInputElement | HTMLTextAreaElement | null> }) { return <div className="field"><label htmlFor={id}>{label} {required && <span className="required">Required</span>}</label><input ref={inputRef as React.RefObject<HTMLInputElement>} id={id} type={type} value={value} onChange={(event) => onChange(event.target.value)} placeholder={placeholder} autoComplete={autoComplete} required={required} /></div>; }
function SecretField({ label, id, value, onChange, visible, onToggle, autoComplete }: { label: string; id: string; value: string; onChange: (value: string) => void; visible: boolean; onToggle: () => void; autoComplete?: string }) { return <div className="field"><label htmlFor={id}>{label} <span className="required">Required</span></label><div className="secret-input"><input id={id} type={visible ? "text" : "password"} value={value} onChange={(event) => onChange(event.target.value)} autoComplete={autoComplete} /><button type="button" aria-label={visible ? `Hide ${label}` : `Show ${label}`} aria-pressed={visible} onClick={onToggle}>{visible ? <EyeOff size={16} /> : <Eye size={16} />}</button></div></div>; }

function AccountDetail({ account, onClose, onSessionOpened, onError }: { account: Account; onClose: () => void; onSessionOpened: (session: Session) => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState(false);
  const openSession = async () => {
    setPending(true);
    try {
      const session = await api<Session>(`/api/v1/accounts/${account.id}/sessions`, { method: "POST" });
      onSessionOpened(session);
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setPending(false);
    }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT DETAIL</div><h2 id="detail-title">{account.email}</h2><p>{account.group_name} · added {formatDate(account.created_at)}</p></div><button type="button" className="icon-button" aria-label="Close account detail" onClick={onClose}><X size={18} /></button></div><div className="detail-body"><div className="detail-status"><div><span className="detail-label">Current hand-off state</span><StatusBadge status={account.readiness_status} /></div><div className="detail-id"><span>Account ID</span><code>{account.id}</code><button type="button" className="copy-button" aria-label="Copy account ID" onClick={() => navigator.clipboard?.writeText(account.id)}><Copy size={14} /></button></div></div><div className="detail-grid"><DetailCard icon={<LockKeyhole size={17} />} label="Credential vault" value="Encrypted at rest" note="Raw fields are never returned" /><DetailCard icon={<KeyRound size={17} />} label="Dedicated proxy" value={account.proxy.label ?? "Not assigned"} note={account.proxy.region ?? "Assignment metadata reserved"} /><DetailCard icon={<Layers3 size={17} />} label="Persistent profile" value={account.profile.id ?? "Reserved"} note="Saved for the next session" /></div><div className="secret-summary"><div><span className="detail-label">Recovery</span><strong>{account.recovery_masked}</strong></div><div><span className="detail-label">2FA</span><strong>{account.two_fa_configured ? "Configured" : "Not configured"}</strong></div><div><span className="detail-label">Login state</span><strong>{labelStatus(account.login_status)}</strong></div></div><div className="future-tabs" role="tablist" aria-label="Account detail sections"><span className="selected" role="tab" aria-selected="true">Overview</span><span role="tab" aria-selected="false">Sessions <small>Next</small></span><span role="tab" aria-selected="false">Audit <small>Next</small></span></div><div className="detail-note"><ShieldCheck size={17} /><span>Opening a session locks this account’s saved profile. Google sign-in stays human-controlled; no credentials are entered by this app.</span></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close detail</button><button type="button" className="button primary" onClick={openSession} disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}<Globe2 size={16} /> Open login session</button></div></section></div>;
}

function SessionWorkspace({ session, onUpdate, onClose, onError }: { session: Session; onUpdate: (session: Session) => void; onClose: () => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState<"complete" | "verify" | "close" | null>(null);
  const updateEvent = async (eventType: "LOGIN_COMPLETED" | "VERIFICATION_REQUIRED") => {
    setPending(eventType === "LOGIN_COMPLETED" ? "complete" : "verify");
    try { onUpdate(await api<Session>(`/api/v1/sessions/${session.id}/events`, { method: "POST", body: JSON.stringify({ event_type: eventType }) })); } catch (error) { onError((error as Error).message); } finally { setPending(null); }
  };
  const close = async () => {
    setPending("close");
    try { onUpdate(await api<Session>(`/api/v1/sessions/${session.id}/close`, { method: "POST" })); onClose(); } catch (error) { onError((error as Error).message); setPending(null); }
  };
  const isOpen = isOpenSessionState(session.state);
  return <div className="modal-backdrop" role="presentation"><section className="modal session-modal" role="dialog" aria-modal="true" aria-labelledby="session-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROFILE-BACKED SESSION</div><h2 id="session-title">Sign in to {session.account_email}</h2><p>Session {session.id} · profile lock {session.profile_id}</p></div><button type="button" className="icon-button" aria-label="Close session workspace" onClick={onClose}><X size={18} /></button></div><div className="session-body"><div className="session-hero"><div className="session-orbit"><Globe2 size={24} /></div><div><span className="detail-label">Current session state</span><strong className={`session-state ${session.state.toLowerCase()}`}>{labelStatus(session.state)}</strong><p>{session.login_state === "REQUIRED" ? "The saved browser profile is ready for a human Google sign-in." : session.login_state === "AUTHENTICATED" ? "The browser reported a completed sign-in. Keep the profile open only while needed." : session.failure_reason ?? "Review the account in the controlled browser session."}</p></div></div><div className="session-grid"><DetailCard icon={<Layers3 size={17} />} label="Persistent profile" value={session.profile_status} note="Profile is saved in the SaaS" /><DetailCard icon={<LockKeyhole size={17} />} label="Profile lock" value={isOpen ? "Locked to this session" : "Released"} note={session.profile_storage_reference} /><DetailCard icon={<ShieldCheck size={17} />} label="Runtime boundary" value="Human hand-off" note="No secret values in session UI" /></div><div className="session-steps"><div className={`session-step ${session.login_state === "REQUIRED" ? "current" : "done"}`}><span>01</span><div><strong>Open Google sign-in</strong><small>Use the saved profile and complete authentication yourself.</small></div></div><div className={`session-step ${session.login_state === "AUTHENTICATED" ? "done" : "current"}`}><span>02</span><div><strong>Record the browser result</strong><small>Only record completion after Google visibly confirms the sign-in.</small></div></div></div><div className="session-warning"><ShieldCheck size={17} /><span>Passwords, recovery values, and 2FA secrets remain in the encrypted vault and are never injected into this interface.</span></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Keep workspace open</button>{isOpen && <><a className="button secondary" href="https://accounts.google.com" target="_blank" rel="noreferrer"><Globe2 size={16} /> Open Google sign-in</a><button type="button" className="button secondary" onClick={() => updateEvent("VERIFICATION_REQUIRED")} disabled={pending !== null} aria-busy={pending === "verify"}>Needs verification</button><button type="button" className="button primary" onClick={() => updateEvent("LOGIN_COMPLETED")} disabled={pending !== null} aria-busy={pending === "complete"}>{pending === "complete" && <span className="button-spinner" />}<Check size={16} /> Record sign-in complete</button><button type="button" className="button danger" onClick={close} disabled={pending !== null} aria-busy={pending === "close"}><Power size={16} /> Close session</button></>}</div></section></div>;
}
function DetailCard({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) { return <div className="detail-card"><span className="detail-card-icon">{icon}</span><span className="detail-label">{label}</span><strong>{value}</strong><small>{note}</small></div>; }
function ToastRegion({ toasts }: { toasts: Toast[] }) { return <div className="toast-region" aria-live="polite" aria-atomic="true">{toasts.map((toast) => <div className={`toast ${toast.tone}`} key={toast.id}><span>{toast.tone === "success" ? <Check size={15} /> : toast.tone === "error" ? <XCircle size={15} /> : <ShieldCheck size={15} />}</span>{toast.message}</div>)}</div>; }

export default App;
