import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  Contact as AddressBook,
  ArrowRight,
  Bell,
  Boxes,
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
  Mail,
  MoveRight,
  Pencil,
  Play,
  Plus,
  Power,
  RefreshCw,
  Search,
  ShieldCheck,
  SlidersHorizontal,
  Tags,
  Trash2,
  UserRound,
  UsersRound,
  X,
  XCircle,
} from "lucide-react";
import { isOpenSessionState } from "./session-contract";

type Group = { id: string; name: string; description: string | null; capacity: number; account_count: number; ready_count: number; issue_count: number; status: string };
type Account = { id: string; email: string; group_id: string; group_name: string; lifecycle_status: string; readiness_status: string; login_status: string; gmail_status: string; secret_status: string; recovery_masked: string; two_fa_configured: boolean; proxy: { id: string | null; label: string | null; region: string | null; status: string }; profile: { id: string | null; storage_reference: string | null; status: string }; created_at: string; updated_at: string };
type Session = { id: string; account_id: string; account_email: string; profile_id: string; profile_storage_reference: string; profile_status: string; profile_saved?: boolean; profile_authenticated_at?: string | null; state: string; login_state: string; runtime_type: string; browser_opened: boolean; observer_attached: boolean; automation_hint: string | null; session_url: string; opened_at: string; last_event_at: string; closed_at: string | null; failure_reason: string | null };
type AuditEvent = { id: string; action: string; target_type: string; target_id: string; result: string; metadata: Record<string, unknown>; created_at: string };
type ContactFile = { id: string; original_filename: string; media_type: string; size_bytes: number; contact_count: number; status: string; last_used_at: string | null; created_at: string; updated_at: string };
type Preview = { total_rows: number; valid_rows: number; duplicate_rows: number; invalid_rows: number; rows: { row_number: number; email?: string | null; recovery_masked?: string | null; two_fa_configured: boolean; valid: boolean; duplicate: boolean; error?: string | null }[] };
type Toast = { id: number; tone: "success" | "error" | "info"; message: string };
type LoginJob = { id: string; group_id: string | null; concurrency_limit: number; status: string; total_count: number; queued_count: number; running_count: number; succeeded_count: number; attention_count: number; failed_count: number; created_at: string; started_at: string | null; completed_at: string | null; updated_at: string; items: { id: string; account_id: string; account_email: string; session_id: string | null; state: string; attempts: number; last_error: string | null; updated_at: string }[] };
type OperationKind = "contacts" | "security" | "labels" | "apps" | "mail" | "addons" | "alerts";
type RegistryAction = OperationKind | "login" | "credentials" | "edit" | "move" | "delete";
type ActionScope = "selected" | "group";

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const isMultipart = options?.body instanceof FormData;
  const response = await fetch(path, { headers: { ...(isMultipart ? {} : { "Content-Type": "application/json" }), ...(options?.headers ?? {}) }, ...options });
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload.detail === "string" ? payload.detail : "The request could not be completed.");
  }
  return response.json() as Promise<T>;
};

const formatDate = (value: string) => new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value));
const formatDateTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)) : "—";
const formatBytes = (value: number) => value < 1024 * 1024 ? `${Math.max(1, Math.round(value / 1024))} KB` : `${(value / (1024 * 1024)).toFixed(1)} MB`;
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
  const [isContactLibraryOpen, setContactLibraryOpen] = useState(false);
  const [isBatchOpen, setBatchOpen] = useState(false);
  const [activeAccount, setActiveAccount] = useState<Account | null>(null);
  const [activeSession, setActiveSession] = useState<Session | null>(null);
  const [activeJob, setActiveJob] = useState<LoginJob | null>(null);
  const [selectedAccountIds, setSelectedAccountIds] = useState<string[]>([]);
  const [actionScope, setActionScope] = useState<ActionScope>("selected");
  const [activeOperation, setActiveOperation] = useState<{ kind: OperationKind; accounts: Account[] } | null>(null);
  const [contactTargets, setContactTargets] = useState<Account[] | null>(null);
  const [activeAdminAction, setActiveAdminAction] = useState<{ kind: "edit" | "move" | "delete"; accounts: Account[] } | null>(null);
  const [credentialAccount, setCredentialAccount] = useState<Account | null>(null);
  const [batchAccountIds, setBatchAccountIds] = useState<string[]>([]);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [mobileNav, setMobileNav] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => { document.title = activeJob ? "Login batch — Gmail Operations" : activeSession ? "Login session — Gmail Operations" : activeAccount ? "Account detail — Gmail Operations" : "Account registry — Gmail Operations"; }, [activeAccount, activeSession, activeJob]);

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
  useEffect(() => { setSelectedAccountIds([]); }, [selectedGroup, query, statusFilter, refreshKey]);
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
  const openLogin = async (account: Account) => {
    try { setActiveSession(await api<Session>(`/api/v1/accounts/${account.id}/sessions`, { method: "POST" })); reload(); }
    catch (requestError) { notify("error", (requestError as Error).message); }
  };
  const runRegistryAction = async (kind: RegistryAction) => {
    try {
      const targets = await resolveActionAccounts();
      if (!targets.length) { notify("info", actionScope === "group" ? "Choose a group with accounts first." : "Select at least one account first."); return; }
      if (kind === "login") {
        if (targets.length === 1) { await openLogin(targets[0]); return; }
        setBatchAccountIds(targets.map((account) => account.id));
        setBatchOpen(true);
        return;
      }
      if (kind === "credentials") {
        if (targets.length !== 1) { notify("info", "Credential revisions are account-specific. Select exactly one account."); return; }
        setCredentialAccount(targets[0]);
        return;
      }
      if (kind === "contacts") {
        setContactTargets(targets);
        return;
      }
      if (["edit", "move", "delete"].includes(kind)) { setActiveAdminAction({ kind: kind as "edit" | "move" | "delete", accounts: targets }); return; }
      setActiveOperation({ kind: kind as OperationKind, accounts: targets });
    } catch (requestError) { notify("error", (requestError as Error).message); }
  };

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
          <section className="page-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT OPERATIONS</div><h1>Account registry</h1><p>Operate the fleet from one control surface. Select accounts, choose a scope, and dispatch the next approved action.</p></div><div className="heading-actions"><button type="button" className="button secondary" onClick={() => setContactLibraryOpen(true)}><AddressBook size={17} /> Contact files</button><button type="button" className="button secondary" onClick={() => setOnboardingOpen(true)}><Plus size={17} /> Add account</button><button type="button" className="button primary" onClick={() => setOnboardingOpen(true)}><ClipboardCheck size={17} /> Import accounts</button></div></section>
          <section className="handoff-banner" aria-label="Phase 1 boundary"><div className="handoff-rail" /><div className="handoff-icon"><ShieldCheck size={19} /></div><div className="handoff-copy"><strong>Safe hand-off to Phase 2</strong><span>New records are encrypted, deduplicated, and ready for a profile-backed automatic login session.</span></div><span className="handoff-state">BOUNDARY LOCKED</span></section>

          <AttentionStrip accounts={attentionAccounts} onOpen={setActiveAccount} />

          <section className="summary-grid" aria-label="Account registry summary"><SummaryCard label="Accounts in view" value={visibleCount.toString()} note="Current group scope" icon={<Database size={17} />} /><SummaryCard label="Ready for login" value={readyCount.toString()} note="Stored and validated" icon={<Check size={17} />} tone="teal" /><SummaryCard label="Groups" value={groups.length.toString()} note="300 account capacity" icon={<Layers3 size={17} />} /><SummaryCard label="Credential health" value="100%" note="Encrypted at rest" icon={<LockKeyhole size={17} />} tone="amber" /></section>

          <section className="registry-panel"><div className="panel-header"><div><h2>Account registry</h2><p>Choose a group to inspect its account boundary.</p></div><div className="group-context"><span>GROUP CONTEXT</span><label className="select-wrap"><select aria-label="Select account group" value={selectedGroup} onChange={(event) => setSelectedGroup(event.target.value)}><option value="all">All groups · {groups.reduce((sum, group) => sum + group.account_count, 0)} accounts</option>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><ChevronDown size={15} /></label></div></div>
            <div className="toolbar"><label className="search-field"><Search size={17} /><span className="sr-only">Search accounts</span><input ref={searchRef} value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.nativeEvent.isComposing) event.preventDefault(); }} placeholder="Search by email or group" />{query && <button type="button" className="clear-search" aria-label="Clear search" onClick={() => { setQuery(""); searchRef.current?.focus(); }}><X size={15} /></button>}</label><div className="toolbar-right"><label className="filter-select"><Filter size={15} /><span className="sr-only">Filter account status</span><select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)} aria-label="Filter account status"><option value="ALL">All statuses</option><option value="READY_FOR_LOGIN">Ready for login</option><option value="SETUP_REQUIRED">Setup required</option><option value="AUTHENTICATED">Authenticated</option><option value="NEEDS_ATTENTION">Needs attention</option><option value="ACCOUNT_NOT_FOUND">Account not found</option></select></label><button type="button" className="icon-button" aria-label="Refresh accounts" onClick={reload}><RefreshCw size={16} /></button></div></div>
            <RegistryActionBar accounts={accounts} selectedAccountIds={selectedAccountIds} actionScope={actionScope} group={currentGroup} onScopeChange={setActionScope} onAction={runRegistryAction} />
            {error && <div className="inline-error" role="alert"><XCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={reload}>Retry</button></div>}
            <div className="result-meta" role="status"><span>{loading ? "Refreshing account registry…" : `${total} ${total === 1 ? "account" : "accounts"} in this view`}</span>{query && <span className="search-context">for “{query}”</span>}</div>
            <AccountTable accounts={accounts} loading={loading} selectedAccountIds={selectedAccountIds} onSelectionChange={setSelectedAccountIds} onOpen={setActiveAccount} onAdd={() => setOnboardingOpen(true)} />
            <div className="table-footer"><span>Showing {accounts.length ? 1 : 0}–{accounts.length} of {total}</span><span className="pagination-note">Server pagination ready · view capped at 50</span></div>
          </section>
          <footer className="page-footer"><span>Northstar Operations</span><span>Phase 1 registry · v0.1</span><a href="/docs" onClick={(event) => event.preventDefault()}>API docs <ArrowRight size={13} /></a></footer>
        </div>
      </main>
      {isOnboardingOpen && <OnboardingModal groups={groups} initialGroup={selectedGroup !== "all" ? selectedGroup : groups[0]?.id ?? ""} onClose={() => setOnboardingOpen(false)} onSuccess={(message) => { setOnboardingOpen(false); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
      {isContactLibraryOpen && <ContactsImportModal accounts={[]} onClose={() => setContactLibraryOpen(false)} onPrepared={(message) => notify("success", message)} onError={(message) => notify("error", message)} />}
      {activeAccount && <AccountDetail account={activeAccount} groups={groups} onClose={() => setActiveAccount(null)} onSessionOpened={setActiveSession} onUpdated={() => { setActiveAccount(null); reload(); }} onPrepared={(message) => notify("info", message)} onError={(message) => notify("error", message)} />}
      {credentialAccount && <CredentialEditor account={credentialAccount} onClose={() => setCredentialAccount(null)} onSaved={() => { setCredentialAccount(null); notify("success", "Credentials updated and the account is ready for a fresh login."); reload(); }} onError={(message) => notify("error", message)} />}
      {activeSession && <SessionWorkspace session={activeSession} onUpdate={(session) => { setActiveSession(session); reload(); }} onClose={() => { setActiveSession(null); reload(); }} onError={(message) => notify("error", message)} />}
      {isBatchOpen && <LoginBatchModal groups={groups} initialGroup={selectedGroup !== "all" ? selectedGroup : groups[0]?.id ?? ""} accountIds={batchAccountIds} onClose={() => { setBatchOpen(false); setBatchAccountIds([]); }} onStarted={(job) => { setBatchOpen(false); setBatchAccountIds([]); setActiveJob(job); notify("success", `${job.running_count} profile sessions opened`); reload(); }} onError={(message) => notify("error", message)} />}
      {activeOperation && <BulkOperationDesigner accounts={activeOperation.accounts} kind={activeOperation.kind} onClose={() => setActiveOperation(null)} onSaved={(message) => { setActiveOperation(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
      {contactTargets && <ContactsImportModal accounts={contactTargets} onClose={() => setContactTargets(null)} onStarted={(session) => setActiveSession(session)} onPrepared={(message) => { setContactTargets(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
      {activeAdminAction && <BulkAdminDesigner accounts={activeAdminAction.accounts} groups={groups} kind={activeAdminAction.kind} onClose={() => setActiveAdminAction(null)} onSaved={(message) => { setActiveAdminAction(null); notify("success", message); reload(); }} onError={(message) => notify("error", message)} />}
      {activeJob && <LoginJobWorkspace job={activeJob} onUpdate={(job) => { setActiveJob(job); reload(); }} onClose={() => { setActiveJob(null); reload(); }} onError={(message) => notify("error", message)} />}
      <ToastRegion toasts={toasts} />
    </div>
  );
}

function NavLabel({ children }: { children: string }) { return <div className="nav-label">{children}</div>; }
function NavItem({ icon, label, active, badge, mutedBadge }: { icon: React.ReactNode; label: string; active?: boolean; badge?: string | number; mutedBadge?: boolean }) { return <button type="button" className={`nav-item ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} disabled={!active}>{icon}<span>{label}</span>{badge !== undefined && <span className={`nav-badge ${mutedBadge ? "muted" : ""}`}>{badge}</span>}</button>; }
function SummaryCard({ label, value, note, icon, tone = "default" }: { label: string; value: string; note: string; icon: React.ReactNode; tone?: string }) { return <div className={`summary-card ${tone}`}><div className="summary-top"><span>{label}</span><span className="summary-icon">{icon}</span></div><strong>{value}</strong><small>{note}</small></div>; }
function AttentionStrip({ accounts, onOpen }: { accounts: Account[]; onOpen: (account: Account) => void }) {
  return <section className={`attention-strip ${accounts.length ? "has-issues" : "clear"}`} aria-label="Accounts requiring attention"><div className="attention-heading"><div className="attention-mark"><ShieldCheck size={17} /></div><div><span className="detail-label">Attention center</span><strong>{accounts.length ? `${accounts.length} account${accounts.length === 1 ? "" : "s"} need review` : "No accounts need attention"}</strong></div></div>{accounts.length ? <div className="attention-items">{accounts.slice(0, 3).map((account) => <button type="button" className="attention-item" key={account.id} onClick={() => onOpen(account)}><span><strong>{account.email}</strong><small>{labelStatus(account.login_status)} · {account.group_name}</small></span><ArrowRight size={15} /></button>)}{accounts.length > 3 && <span className="attention-more">+{accounts.length - 3} more in the current view</span>}</div> : <span className="attention-clear-copy">Authentication, profile, and account records are clear in this view.</span>}</section>;
}

const registryActions: { kind: RegistryAction; label: string; shortLabel: string; icon: React.ReactNode; tone?: string }[] = [
  { kind: "login", label: "Login", shortLabel: "Login", icon: <Play size={15} /> },
  { kind: "credentials", label: "Credentials", shortLabel: "Credentials", icon: <KeyRound size={15} /> },
  { kind: "contacts", label: "Contacts", shortLabel: "Contacts", icon: <AddressBook size={15} /> },
  { kind: "security", label: "Browser security", shortLabel: "Security", icon: <ShieldCheck size={15} /> },
  { kind: "labels", label: "Markers & labels", shortLabel: "Labels", icon: <Tags size={15} /> },
  { kind: "apps", label: "Google apps", shortLabel: "Apps", icon: <Boxes size={15} /> },
  { kind: "mail", label: "Send & reply", shortLabel: "Mail", icon: <Mail size={15} /> },
  { kind: "addons", label: "Add-ons", shortLabel: "Add-ons", icon: <SlidersHorizontal size={15} /> },
  { kind: "alerts", label: "Google Alerts", shortLabel: "Alerts", icon: <Bell size={15} /> },
  { kind: "edit", label: "Edit metadata", shortLabel: "Edit", icon: <Pencil size={15} /> },
  { kind: "move", label: "Move group", shortLabel: "Move", icon: <MoveRight size={15} /> },
  { kind: "delete", label: "Archive", shortLabel: "Archive", icon: <Trash2 size={15} />, tone: "danger" },
];

function RegistryActionBar({ accounts, selectedAccountIds, actionScope, group, onScopeChange, onAction }: { accounts: Account[]; selectedAccountIds: string[]; actionScope: ActionScope; group?: Group; onScopeChange: (scope: ActionScope) => void; onAction: (kind: RegistryAction) => void }) {
  const selectedCount = selectedAccountIds.length;
  const scopeCount = actionScope === "group" ? group?.account_count ?? 0 : selectedCount;
  const credentialsDisabled = actionScope === "group" || selectedCount !== 1;
  return <section className="registry-actions" aria-label="Account actions"><div className="registry-actions-head"><div><span className="detail-label">Registry actions</span><strong>Choose an operation for the current scope</strong></div><div className="scope-control" role="group" aria-label="Action scope"><button type="button" className={actionScope === "selected" ? "selected" : ""} onClick={() => onScopeChange("selected")}>Selected <span>{selectedCount}</span></button><button type="button" className={actionScope === "group" ? "selected" : ""} disabled={!group} onClick={() => onScopeChange("group")}>Entire group <span>{group?.account_count ?? 0}</span></button></div></div><div className="action-scope-note" role="status"><span className={`scope-dot ${scopeCount ? "active" : ""}`} />{scopeCount ? `${scopeCount} ${scopeCount === 1 ? "account" : "accounts"} in scope` : actionScope === "group" ? "Choose a group with accounts" : "Select accounts below to enable actions"}{credentialsDisabled && <small>Credentials require exactly one selected account.</small>}</div><div className="registry-action-grid">{registryActions.map((action) => <button key={action.kind} type="button" className={`registry-action ${action.tone ?? ""}`} disabled={!scopeCount || (action.kind === "credentials" && credentialsDisabled)} title={action.kind === "credentials" && credentialsDisabled ? "Select exactly one account for credential changes" : action.label} onClick={() => onAction(action.kind)}><span className="registry-action-icon">{action.icon}</span><span>{action.shortLabel}</span></button>)}</div></section>;
}

function AccountTable({ accounts, loading, selectedAccountIds, onSelectionChange, onOpen, onAdd }: { accounts: Account[]; loading: boolean; selectedAccountIds: string[]; onSelectionChange: (ids: string[]) => void; onOpen: (account: Account) => void; onAdd: () => void }) {
  if (loading) return <div className="table-state loading-state"><span className="spinner" /><strong>Loading account registry</strong><span>Keeping the table stable while the latest view arrives.</span></div>;
  if (!accounts.length) return <div className="table-state empty-state"><div className="empty-icon"><Database size={21} /></div><strong>No accounts in this view</strong><span>Add an account or adjust the group and search filters.</span><button type="button" className="button primary small" onClick={onAdd}><Plus size={15} /> Add account</button></div>;
  const allSelected = accounts.length > 0 && accounts.every((account) => selectedAccountIds.includes(account.id));
  const toggleAll = () => onSelectionChange(allSelected ? selectedAccountIds.filter((id) => !accounts.some((account) => account.id === id)) : [...new Set([...selectedAccountIds, ...accounts.map((account) => account.id)])]);
  const toggleOne = (id: string) => onSelectionChange(selectedAccountIds.includes(id) ? selectedAccountIds.filter((selectedId) => selectedId !== id) : [...selectedAccountIds, id]);
  return <div className="table-scroll"><table><caption className="sr-only">Company Gmail account registry</caption><thead><tr><th scope="col" className="select-column"><input type="checkbox" aria-label="Select all accounts in this view" checked={allSelected} onChange={toggleAll} /></th><th scope="col">Account</th><th scope="col">Group</th><th scope="col">Phase status</th><th scope="col">Login status</th><th scope="col">Secrets</th><th scope="col">Proxy</th><th scope="col">Profile</th><th scope="col"><span className="sr-only">Open</span></th></tr></thead><tbody>{accounts.map((account) => <tr key={account.id} className={selectedAccountIds.includes(account.id) ? "selected-row" : ""}><td className="select-column"><input type="checkbox" aria-label={`Select ${account.email}`} checked={selectedAccountIds.includes(account.id)} onChange={() => toggleOne(account.id)} /></td><td className="account-cell"><button type="button" className="account-link" onClick={() => onOpen(account)}><span className="account-avatar">{account.email.slice(0, 2).toUpperCase()}</span><span><strong>{account.email}</strong><small>Added {formatDate(account.created_at)}</small></span></button></td><td><span className="group-tag"><span className="tag-dot" />{account.group_name}</span></td><td><StatusBadge status={account.readiness_status} /></td><td><LoginBadge status={account.login_status} /></td><td><span className="security-cell"><LockKeyhole size={14} /> Encrypted</span></td><td><span className="metadata-cell"><span className="status-dot neutral" /> {account.proxy.status === "UNASSIGNED" ? "Reserved" : account.proxy.status}</span></td><td><span className="metadata-cell"><span className={`status-dot ${account.profile.status === "SAVED" ? "teal" : "neutral"}`} /> {account.profile.status === "SAVED" ? "Saved · reopens without login" : account.profile.status === "OPEN" ? "Active session" : account.profile.status === "ARCHIVED" ? "Archived" : "Not saved yet"}</span></td><td><button type="button" className="row-open" aria-label={`Open ${account.email}`} onClick={() => onOpen(account)}><ArrowRight size={16} /></button></td></tr>)}</tbody></table></div>;
}
function StatusBadge({ status }: { status: string }) { const tone = status === "READY_FOR_LOGIN" ? "ready" : ["SETUP_ERROR", "INVALID"].includes(status) ? "danger" : "setup"; return <span className={`status-badge ${tone}`}><span className="status-mark">{tone === "ready" ? <Check size={11} /> : tone === "danger" ? <XCircle size={11} /> : <Clock3 size={11} />}</span>{labelStatus(status)}</span>; }
const loginTone = (status: string) => status === "NONEXISTENT" ? "danger" : status === "AUTHENTICATED" ? "ok" : ["VERIFICATION_REQUIRED", "NEEDS_ATTENTION", "UNKNOWN_PAGE", "ACCOUNT_NOT_FOUND"].includes(status) ? "warning" : "neutral";
function LoginBadge({ status }: { status: string }) { return <span className={`status-badge login ${loginTone(status)}`}><span className="status-mark">{status === "AUTHENTICATED" ? <Check size={11} /> : status === "NONEXISTENT" ? <XCircle size={11} /> : <Clock3 size={11} />}</span>{status === "NOT_STARTED" ? "Never logged in" : labelStatus(status)}</span>; }

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

function AccountDetail({ account, groups, onClose, onSessionOpened, onUpdated, onPrepared, onError }: { account: Account; groups: Group[]; onClose: () => void; onSessionOpened: (session: Session) => void; onUpdated: () => void; onPrepared: (message: string) => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState(false);
  const [detailAccount, setDetailAccount] = useState(account);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [auditEvents, setAuditEvents] = useState<AuditEvent[]>([]);
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
      const session = await api<Session>(`/api/v1/accounts/${account.id}/sessions`, { method: "POST" });
      onSessionOpened(session);
      void refreshDetail();
    } catch (error) {
      onError((error as Error).message);
    } finally {
      setPending(false);
    }
  };
  const profileValue = detailAccount.profile.id ?? "Reserved for first sign-in";
  const profileNote = detailAccount.profile.status === "SAVED" ? `${detailAccount.profile.storage_reference ?? "Profile storage"} · reopens without login` : `${labelStatus(detailAccount.profile.status)} · no saved profile yet`;
  const proxyValue = detailAccount.proxy.label ?? "Not assigned";
  const proxyNote = detailAccount.proxy.region ? `${detailAccount.proxy.region} · ${labelStatus(detailAccount.proxy.status)}` : `Assignment metadata · ${labelStatus(detailAccount.proxy.status)}`;
  const recentSessions = sessions.slice(0, 3);
  const detailContent = detailTab === "overview" ? <>
    <div className="detail-overview-grid">
      <div className="detail-overview-card"><span className="detail-label">Record metadata</span><dl className="detail-facts"><div><dt>Email</dt><dd>{detailAccount.email}</dd></div><div><dt>Group</dt><dd>{detailAccount.group_name}</dd></div><div><dt>Lifecycle</dt><dd>{labelStatus(detailAccount.lifecycle_status)}</dd></div><div><dt>Added</dt><dd>{formatDateTime(detailAccount.created_at)}</dd></div><div><dt>Last record update</dt><dd>{formatDateTime(detailAccount.updated_at)}</dd></div></dl></div>
      <div className="detail-overview-card"><div className="detail-section-heading"><span className="detail-label">Latest activity</span><button type="button" className="text-action" onClick={() => setDetailTab("sessions")}>View all</button></div>{recentSessions.length ? <div className="activity-list">{recentSessions.map((session) => <div className="activity-item" key={session.id}><span className={`status-dot ${session.login_state === "AUTHENTICATED" ? "teal" : session.login_state === "NONEXISTENT" ? "danger" : "neutral"}`} /><div><strong>{labelStatus(session.login_state || session.state)}</strong><small>{formatDateTime(session.last_event_at)} · {session.browser_opened ? "Browser open" : "Browser closed"}</small></div></div>)}</div> : <div className="detail-empty">No login sessions have been recorded.</div>}<div className="activity-footer"><span>{auditEvents.length} audit {auditEvents.length === 1 ? "event" : "events"}</span><button type="button" className="text-action" onClick={() => setDetailTab("audit")}>Review audit</button></div></div>
    </div>
    <div className="detail-note"><ShieldCheck size={17} /><span>Account data is read live from the registry. Credentials remain encrypted and are never returned to this view. Registry actions stay in the action rail so this panel remains a focused record.</span></div>
  </> : detailTab === "sessions" ? <div className="detail-section"><div className="detail-section-heading"><div><span className="detail-label">Session history</span><strong>{sessions.length} recorded {sessions.length === 1 ? "session" : "sessions"}</strong></div><button type="button" className="icon-text-button" onClick={() => void refreshDetail()} disabled={detailLoading}><RefreshCw size={13} className={detailLoading ? "spin" : ""} /> Refresh</button></div>{sessions.length ? <div className="session-history">{sessions.map((session) => <article className="session-history-item" key={session.id}><div className="session-history-main"><div><strong>{labelStatus(session.login_state || session.state)}</strong><span>{session.runtime_type} · {session.browser_opened ? "Browser open" : "Browser closed"}</span></div><LoginBadge status={session.login_state} /></div><div className="session-history-meta"><span>Opened {formatDateTime(session.opened_at)}</span><span>Last event {formatDateTime(session.last_event_at)}</span><span>{session.profile_saved ? "Profile saved" : "Profile not saved"}</span></div>{session.failure_reason && <p className="session-failure">{session.failure_reason}</p>}<code className="session-id">{session.id}</code></article>)}</div> : <div className="detail-empty">No sessions have been recorded for this account.</div>}</div> : <div className="detail-section"><div className="detail-section-heading"><div><span className="detail-label">Audit trail</span><strong>Account and session events</strong></div><button type="button" className="icon-text-button" onClick={() => void refreshDetail()} disabled={detailLoading}><RefreshCw size={13} className={detailLoading ? "spin" : ""} /> Refresh</button></div>{auditEvents.length ? <div className="audit-list">{auditEvents.map((event) => <div className="audit-item" key={event.id}><span className={`audit-result ${event.result.toLowerCase()}`}>{labelStatus(event.result)}</span><div><strong>{labelStatus(event.action)}</strong><small>{labelStatus(event.target_type)} · {formatDateTime(event.created_at)}</small></div><code>{event.target_id}</code></div>)}</div> : <div className="detail-empty">No audit events have been recorded for this account.</div>}</div>;

  return <>
    <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <section className="modal detail-modal" role="dialog" aria-modal="true" aria-labelledby="detail-title">
        <div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT DETAIL</div><h2 id="detail-title">{detailAccount.email}</h2><p>{detailAccount.group_name} · added {formatDate(detailAccount.created_at)}</p></div><div className="detail-header-actions"><span className="live-indicator"><span className={`status-dot ${detailLoading ? "neutral" : "teal"}`} />{detailLoading ? "Refreshing" : `Updated ${lastRefreshed ? formatDateTime(lastRefreshed) : "just now"}`}</span><button type="button" className="icon-button" aria-label="Refresh account detail" onClick={() => void refreshDetail()} disabled={detailLoading}><RefreshCw size={16} className={detailLoading ? "spin" : ""} /></button><button type="button" className="icon-button" aria-label="Close account detail" onClick={onClose}><X size={18} /></button></div></div>
        <div className="detail-body"><div className="detail-status"><div className="detail-status-grid"><div><span className="detail-label">Phase status</span><StatusBadge status={detailAccount.readiness_status} /></div><div><span className="detail-label">Login status</span><LoginBadge status={detailAccount.login_status} /></div><div><span className="detail-label">Gmail status</span><strong className="detail-status-value">{labelStatus(detailAccount.gmail_status)}</strong></div><div><span className="detail-label">Lifecycle</span><strong className="detail-status-value">{labelStatus(detailAccount.lifecycle_status)}</strong></div></div><div className="detail-id"><span>Account ID</span><code>{detailAccount.id}</code><button type="button" className="copy-button" aria-label="Copy account ID" onClick={() => navigator.clipboard?.writeText(detailAccount.id)}><Copy size={14} /></button></div></div>{detailError && <div className="detail-refresh-error" role="status"><XCircle size={15} /> Showing the last known record · {detailError}</div>}<div className="detail-grid"><DetailCard icon={<LockKeyhole size={17} />} label="Credential vault" value={detailAccount.secret_status === "STORED_ENCRYPTED" ? "Encrypted at rest" : labelStatus(detailAccount.secret_status)} note="Raw fields are never returned" /><DetailCard icon={<KeyRound size={17} />} label="Dedicated proxy" value={proxyValue} note={proxyNote} /><DetailCard icon={<Layers3 size={17} />} label="Persistent profile" value={profileValue} note={profileNote} /><DetailCard icon={<Globe2 size={17} />} label="Gmail observation" value={labelStatus(detailAccount.gmail_status)} note="Derived from the latest account session" /></div><div className="secret-summary"><div><span className="detail-label">Recovery</span><strong>{detailAccount.recovery_masked}</strong></div><div><span className="detail-label">2FA</span><strong>{detailAccount.two_fa_configured ? "Configured" : "Not configured"}</strong></div><div><span className="detail-label">Last login signal</span><strong>{labelStatus(detailAccount.login_status)}</strong></div><div><span className="detail-label">Profile state</span><strong>{labelStatus(detailAccount.profile.status)}</strong></div></div><div className="future-tabs" role="tablist" aria-label="Account detail sections"><button type="button" role="tab" aria-selected={detailTab === "overview"} className={detailTab === "overview" ? "selected" : ""} onClick={() => setDetailTab("overview")}>Overview</button><button type="button" role="tab" aria-selected={detailTab === "sessions"} className={detailTab === "sessions" ? "selected" : ""} onClick={() => setDetailTab("sessions")}>Sessions <span>{sessions.length}</span></button><button type="button" role="tab" aria-selected={detailTab === "audit"} className={detailTab === "audit" ? "selected" : ""} onClick={() => setDetailTab("audit")}>Audit <span>{auditEvents.length}</span></button></div><div className="detail-tab-panel" role="tabpanel">{detailContent}</div></div>
        <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close detail</button>{existingSession ? <button type="button" className="button primary" onClick={() => onSessionOpened(existingSession)}><Globe2 size={16} /> Resume login session</button> : <button type="button" className="button primary" onClick={openSession} disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}<Globe2 size={16} /> Open login session</button>}</div>
      </section>
    </div>
    {editingCredentials && <CredentialEditor account={account} onClose={() => setEditingCredentials(false)} onSaved={() => { setEditingCredentials(false); onUpdated(); }} onError={onError} />}
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
};

function OperationCard({ icon, title, note, onClick }: { icon: React.ReactNode; title: string; note: string; onClick: () => void }) { return <button type="button" className="operation-card" onClick={onClick}><span className="operation-icon">{icon}</span><span><strong>{title}</strong><small>{note}</small></span><ArrowRight size={14} /></button>; }

function ContactsImportModal({ accounts, onClose, onPrepared, onError, onStarted }: { accounts: Account[]; onClose: () => void; onPrepared: (message: string) => void; onError: (message: string) => void; onStarted?: (session: Session) => void }) {
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
      <section className="modal contact-library-modal" role="dialog" aria-modal="true" aria-labelledby="contacts-title">
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

function OperationDesigner({ account, kind, onClose, onSaved, onError }: { account: Account; kind: OperationKind; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const copy = operationCopy[kind];
  const [values, setValues] = useState<Record<string, string>>({});
  const [pending, setPending] = useState(false);
  const submit = async (event: FormEvent) => { event.preventDefault(); if (pending) return; setPending(true); try { await api(`/api/v1/accounts/${account.id}/operation-drafts`, { method: "POST", body: JSON.stringify({ operation_type: kind, configuration: values }) }); onSaved(`${copy.title} saved for ${account.email}`); } catch (error) { onError((error as Error).message); setPending(false); } };
  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal" role="dialog" aria-modal="true" aria-labelledby="operation-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> {copy.eyebrow}</div><h2 id="operation-title">{copy.title}</h2><p>{account.email} · saved workflow draft</p></div><button type="button" className="icon-button" aria-label={`Close ${copy.title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="operation-form" className="modal-body" onSubmit={submit} noValidate><div className="security-callout"><ShieldCheck size={17} /><div><strong>Preparation only</strong><span>{copy.description} Saving stores a typed plan only; no external action is sent or changed.</span></div></div><div className="operation-fields">{copy.fields.map((field) => <div className="field" key={field.id}><label htmlFor={`operation-${field.id}`}>{field.label}</label>{field.multiline ? <textarea className="resize-none" id={`operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} /> : <input id={`operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} />}</div>)}</div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="operation-form" className="button primary" disabled={pending}>{pending ? "Saving…" : "Save workflow draft"}</button></div></section></div>;
}

function BulkOperationDesigner({ accounts, kind, onClose, onSaved, onError }: { accounts: Account[]; kind: OperationKind; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
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
  return <div className="modal-backdrop" role="presentation"><section className="modal operation-modal bulk-operation-modal" role="dialog" aria-modal="true" aria-labelledby="bulk-operation-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> {copy.eyebrow}</div><h2 id="bulk-operation-title">{copy.title}</h2><p>{accounts.length} accounts · one audited plan per account</p></div><button type="button" className="icon-button" aria-label={`Close ${copy.title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="bulk-operation-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><ShieldCheck size={17} /><div><strong>Preparation only</strong><span>{copy.description} This saves the same typed plan as a separate draft for each account. It does not execute an external Google action.</span></div></div><div className="operation-fields">{copy.fields.map((field) => <div className="field" key={field.id}><label htmlFor={`bulk-operation-${field.id}`}>{field.label}</label>{field.multiline ? <textarea className="resize-none" id={`bulk-operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} /> : <input id={`bulk-operation-${field.id}`} value={values[field.id] ?? ""} placeholder={field.placeholder} onChange={(event) => setValues((current) => ({ ...current, [field.id]: event.target.value }))} disabled={pending} />}</div>)}</div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="bulk-operation-form" className="button primary" disabled={pending}>{pending ? "Saving drafts…" : `Save for ${accounts.length} accounts`}</button></div></section></div>;
}

function BulkAdminDesigner({ accounts, groups, kind, onClose, onSaved, onError }: { accounts: Account[]; groups: Group[]; kind: "edit" | "move" | "delete"; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const [proxyLabel, setProxyLabel] = useState("");
  const [proxyRegion, setProxyRegion] = useState("");
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
    const failures: string[] = [];
    for (const account of accounts) {
      try {
        if (kind === "edit") await api(`/api/v1/accounts/${account.id}`, { method: "PATCH", body: JSON.stringify({ proxy_label: proxyLabel || null, proxy_region: proxyRegion || null }) });
        if (kind === "move") await api(`/api/v1/accounts/${account.id}/group-move`, { method: "POST", body: JSON.stringify({ destination_group_id: groupId }) });
        if (kind === "delete") await api(`/api/v1/accounts/${account.id}`, { method: "DELETE", body: JSON.stringify({ confirmation_email: account.email }) });
        completed += 1;
      } catch { failures.push(account.email); }
    }
    setPending(false);
    if (failures.length) onError(`${completed} completed; ${failures.length} could not be completed. Review the registry and retry the failed accounts.`);
    if (completed) onSaved(`${title} completed for ${completed} ${completed === 1 ? "account" : "accounts"}`);
    else setError("No account changes were completed. Retry the operation.");
  };
  return <div className="modal-backdrop" role="presentation"><section className={`modal admin-modal bulk-admin-modal ${kind === "delete" ? "delete-modal" : ""}`} role="dialog" aria-modal="true" aria-labelledby="bulk-admin-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> REGISTRY ADMINISTRATION</div><h2 id="bulk-admin-title">{title}</h2><p>{accounts.length} accounts · each change is validated and audited individually</p></div><button type="button" className="icon-button" aria-label={`Close ${title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="bulk-admin-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{kind === "edit" && <div className="form-grid"><div className="field"><label htmlFor="bulk-proxy-label">Proxy label</label><input id="bulk-proxy-label" value={proxyLabel} onChange={(event) => setProxyLabel(event.target.value)} placeholder="e.g. US-NY-0142" disabled={pending} /></div><div className="field"><label htmlFor="bulk-proxy-region">Proxy region</label><input id="bulk-proxy-region" value={proxyRegion} onChange={(event) => setProxyRegion(event.target.value)} placeholder="e.g. New York, US" disabled={pending} /></div></div>}{kind === "move" && <div className="field"><label htmlFor="bulk-move-group">Destination group</label><select id="bulk-move-group" value={groupId} onChange={(event) => setGroupId(event.target.value)} disabled={pending}>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><small>The server checks capacity and records each move in audit history.</small></div>}{kind === "delete" && <><div className="delete-warning"><Trash2 size={18} /><div><strong>Archive {accounts.length} accounts</strong><span>Active sessions are closed and the accounts are archived. This cannot be undone from the registry without re-adding credentials.</span></div></div><div className="field"><label htmlFor="bulk-delete-confirm">Type ARCHIVE to confirm</label><input id="bulk-delete-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder="ARCHIVE" autoComplete="off" disabled={pending} /></div></>}{kind !== "delete" && <div className="security-callout"><ShieldCheck size={17} /><div><strong>Server-confirmed change</strong><span>The operation runs one account at a time so partial failures remain visible and recoverable.</span></div></div>}</form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="bulk-admin-form" className={`button ${kind === "delete" ? "danger" : "primary"}`} disabled={pending}>{pending ? "Applying changes…" : kind === "delete" ? `Archive ${accounts.length} accounts` : title}</button></div></section></div>;
}

function AccountAdminDesigner({ account, groups, kind, onClose, onSaved, onError }: { account: Account; groups: Group[]; kind: "edit" | "move" | "delete"; onClose: () => void; onSaved: (message: string) => void; onError: (message: string) => void }) {
  const [groupId, setGroupId] = useState(account.group_id);
  const [confirmation, setConfirmation] = useState("");
  const [proxyLabel, setProxyLabel] = useState(account.proxy.label ?? "");
  const [proxyRegion, setProxyRegion] = useState(account.proxy.region ?? "");
  const [pending, setPending] = useState(false);
  const title = kind === "edit" ? "Edit account metadata" : kind === "move" ? "Move account to another group" : "Delete account";
  const submit = async (event: FormEvent) => { event.preventDefault(); if (pending || (kind === "delete" && confirmation.trim().toLowerCase() !== account.email.toLowerCase())) return; setPending(true); try { if (kind === "edit") await api<Account>(`/api/v1/accounts/${account.id}`, { method: "PATCH", body: JSON.stringify({ proxy_label: proxyLabel || null, proxy_region: proxyRegion || null }) }); if (kind === "move") await api<Account>(`/api/v1/accounts/${account.id}/group-move`, { method: "POST", body: JSON.stringify({ destination_group_id: groupId }) }); if (kind === "delete") await api<Account>(`/api/v1/accounts/${account.id}`, { method: "DELETE", body: JSON.stringify({ confirmation_email: confirmation }) }); onSaved(`${title} completed for ${account.email}`); } catch (error) { onError((error as Error).message); setPending(false); } };
  return <div className="modal-backdrop" role="presentation"><section className={`modal admin-modal ${kind === "delete" ? "delete-modal" : ""}`} role="dialog" aria-modal="true" aria-labelledby="admin-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT ADMINISTRATION</div><h2 id="admin-title">{title}</h2><p>{account.email} · server-backed admin action</p></div><button type="button" className="icon-button" aria-label={`Close ${title}`} onClick={onClose} disabled={pending}><X size={18} /></button></div><form id="admin-form" className="modal-body" onSubmit={submit} noValidate>{kind === "edit" && <div className="form-grid"><div className="field"><label htmlFor="edit-email">Gmail address</label><input id="edit-email" value={account.email} readOnly /></div><div className="field"><label htmlFor="edit-proxy-label">Proxy label</label><input id="edit-proxy-label" value={proxyLabel} onChange={(event) => setProxyLabel(event.target.value)} placeholder="e.g. US-NY-0142" disabled={pending} /></div><div className="field"><label htmlFor="edit-proxy-region">Proxy region</label><input id="edit-proxy-region" value={proxyRegion} onChange={(event) => setProxyRegion(event.target.value)} placeholder="e.g. New York, US" disabled={pending} /></div></div>}{kind === "move" && <div className="field"><label htmlFor="move-group">Destination group</label><select id="move-group" value={groupId} onChange={(event) => setGroupId(event.target.value)} disabled={pending}>{groups.map((group) => <option key={group.id} value={group.id}>{group.name} · {group.account_count}/{group.capacity}</option>)}</select><small>The server checks destination capacity and records the group change in audit history.</small></div>}{kind === "delete" && <><div className="delete-warning"><Trash2 size={18} /><div><strong>This is a destructive action</strong><span>The account is archived, active sessions are closed, and its profile/secrets remain subject to retention policy.</span></div></div><div className="field"><label htmlFor="delete-confirm">Type the account email to confirm</label><input id="delete-confirm" value={confirmation} onChange={(event) => setConfirmation(event.target.value)} placeholder={account.email} autoComplete="off" disabled={pending} /></div></>}{kind !== "delete" && <div className="security-callout"><ShieldCheck size={17} /><div><strong>Server-confirmed change</strong><span>The API validates the request, writes an audit event, and reloads the account registry after success.</span></div></div>}</form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" form="admin-form" className={`button ${kind === "delete" ? "danger" : "primary"}`} disabled={pending || (kind === "delete" && confirmation.trim().toLowerCase() !== account.email.toLowerCase())}>{pending ? "Saving…" : kind === "delete" ? "Archive account" : kind === "move" ? "Move account" : "Save metadata"}</button></div></section></div>;
}

function CredentialEditor({ account, onClose, onSaved, onError }: { account: Account; onClose: () => void; onSaved: () => void; onError: (message: string) => void }) {
  const [form, setForm] = useState({ password: "", recovery: "", two_fa: "" });
  const [shown, setShown] = useState<Record<string, boolean>>({});
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const update = (key: keyof typeof form, value: string) => { setForm((current) => ({ ...current, [key]: value })); setError(""); };
  const save = async (event: FormEvent) => {
    event.preventDefault();
    if (Object.values(form).some((value) => !value.trim())) { setError("Complete all credential fields before saving the new revision."); return; }
    setPending(true);
    try { await api<Account>(`/api/v1/accounts/${account.id}/credentials`, { method: "PUT", body: JSON.stringify({ password: form.password, recovery: form.recovery, "2fa": form.two_fa }) }); onSaved(); }
    catch (requestError) { const message = (requestError as Error).message; setError(message); onError(message); }
    finally { setPending(false); }
  };
  return <div className="modal-backdrop credential-backdrop" role="presentation"><section className="modal credential-modal" role="dialog" aria-modal="true" aria-labelledby="credential-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ENCRYPTED REVISION</div><h2 id="credential-title">Update account credentials</h2><p>{account.email} · the previous revision remains audit-visible, never readable</p></div><button type="button" className="icon-button" aria-label="Close credential editor" onClick={onClose}><X size={18} /></button></div><form id="credential-editor" className="modal-body" onSubmit={save} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}<div className="security-callout"><LockKeyhole size={17} /><div><strong>Revision-safe update</strong><span>Values are encrypted at the API boundary. Saving marks this account ready for a fresh login run.</span></div></div><div className="credential-stack"><SecretField label="New password" id="revision-password" value={form.password} onChange={(value) => update("password", value)} visible={!!shown.password} onToggle={() => setShown((current) => ({ ...current, password: !current.password }))} autoComplete="new-password" /><SecretField label="Recovery email or code" id="revision-recovery" value={form.recovery} onChange={(value) => update("recovery", value)} visible={!!shown.recovery} onToggle={() => setShown((current) => ({ ...current, recovery: !current.recovery }))} autoComplete="off" /><SecretField label="2FA secret" id="revision-two-fa" value={form.two_fa} onChange={(value) => update("two_fa", value)} visible={!!shown.two_fa} onToggle={() => setShown((current) => ({ ...current, two_fa: !current.two_fa }))} autoComplete="off" /></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" form="credential-editor" className="button primary" disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}Save encrypted revision</button></div></section></div>;
}

function LoginBatchModal({ groups, initialGroup, accountIds = [], onClose, onStarted, onError }: { groups: Group[]; initialGroup: string; accountIds?: string[]; onClose: () => void; onStarted: (job: LoginJob) => void; onError: (message: string) => void }) {
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
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}><section className="modal batch-modal" role="dialog" aria-modal="true" aria-labelledby="batch-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CONTROLLED DISPATCH</div><h2 id="batch-title">Queue a login batch</h2><p>Open isolated saved profiles in waves. Each account keeps its own session lock.</p></div><button type="button" className="icon-button" aria-label="Close login batch dialog" onClick={onClose}><X size={18} /></button></div><form id="batch-form" className="modal-body" onSubmit={submit} noValidate>{error && <div className="form-error" role="alert"><XCircle size={17} /><span>{error}</span></div>}{!accountIds.length && <div className="field"><label htmlFor="batch-group">Account group <span className="required">Required</span></label><select id="batch-group" value={groupId} onChange={(event) => setGroupId(event.target.value)}><option value="">Select a group</option>{groups.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.name} · {candidate.ready_count}/{candidate.capacity} ready</option>)}</select></div>}<div className="batch-control"><div><label htmlFor="concurrency">Concurrent profiles</label><small>How many account sessions can be open at the same time.</small></div><input id="concurrency" type="number" min="1" max="50" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} /></div><div className="dispatch-preview"><div><span className="detail-label">Dispatch preview</span><strong>{targetCount} accounts in scope</strong></div><div className="dispatch-meter" aria-hidden="true"><span style={{ width: `${Math.min(100, (targetCount / Math.max(1, group?.capacity ?? 300)) * 100)}%` }} /></div><small>Remaining accounts stay queued until a running profile finishes or the batch is dispatched again.</small></div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Challenge-safe behavior</strong><span>Normal sign-in work can continue in isolated profiles. CAPTCHA, MFA, and unknown pages pause the affected account for operator attention.</span></div></div></form><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Cancel</button><button type="submit" form="batch-form" className="button primary" disabled={pending} aria-busy={pending}>{pending && <span className="button-spinner" />}Open controlled batch</button></div></section></div>;
}

function LoginJobWorkspace({ job, onUpdate, onClose, onError }: { job: LoginJob; onUpdate: (job: LoginJob) => void; onClose: () => void; onError: (message: string) => void }) {
  const [pending, setPending] = useState<string | null>(null);
  useEffect(() => {
    if (!["RUNNING", "QUEUED"].includes(job.status)) return;
    const timer = window.setInterval(() => { api<LoginJob>(`/api/v1/login-jobs/${job.id}/progress`).then(onUpdate).catch(() => undefined); }, 4000);
    return () => window.clearInterval(timer);
  }, [job.id, job.status, onUpdate]);
  const action = async (name: "dispatch-next" | "pause" | "resume" | "cancel") => {
    setPending(name);
    try { onUpdate(await api<LoginJob>(`/api/v1/login-jobs/${job.id}/${name}`, { method: "POST" })); }
    catch (requestError) { const message = (requestError as Error).message; onError(message); }
    finally { setPending(null); }
  };
  const completedCount = job.succeeded_count + job.attention_count + job.failed_count;
  const progress = job.total_count ? Math.round((completedCount / job.total_count) * 100) : 0;
  return <div className="modal-backdrop" role="presentation"><section className="modal job-modal" role="dialog" aria-modal="true" aria-labelledby="job-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> BATCH CONTROL</div><h2 id="job-title">Login batch progress</h2><p>{job.total_count} accounts · concurrency {job.concurrency_limit} · {labelStatus(job.status)}</p></div><button type="button" className="icon-button" aria-label="Close batch workspace" onClick={onClose}><X size={18} /></button></div><div className="job-body"><div className="job-progress-head"><div><span className="detail-label">Completion</span><strong>{completedCount} of {job.total_count}</strong></div><span className="job-percent">{progress}%</span></div><div className="job-progress" aria-label={`${progress}% complete`}><span style={{ width: `${progress}%` }} /></div><div className="job-stats"><JobStat label="Running" value={job.running_count} tone="running" /><JobStat label="Queued" value={job.queued_count} tone="queued" /><JobStat label="Completed" value={job.succeeded_count} tone="done" /><JobStat label="Attention" value={job.attention_count} tone="attention" /></div><div className="job-list" aria-label="Account batch progress"><div className="job-list-head"><span>ACCOUNT</span><span>STATE</span></div>{job.items.map((item) => <div className="job-item" key={item.id}><div><strong>{item.account_email}</strong><small>{item.last_error ?? (item.session_id ? `Session ${item.session_id}` : "Waiting for an isolated profile")}</small></div><span className={`job-item-state ${item.state.toLowerCase()}`}>{labelStatus(item.state)}</span></div>)}</div><div className="security-callout"><ShieldCheck size={17} /><div><strong>Audit-safe control</strong><span>Each profile is isolated and locked to one account. Google security challenges pause only the affected item.</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Keep workspace open</button>{job.status === "RUNNING" && <button type="button" className="button secondary" onClick={() => action("pause")} disabled={pending !== null} aria-busy={pending === "pause"}>Pause batch</button>}{job.status === "PAUSED" && <button type="button" className="button secondary" onClick={() => action("resume")} disabled={pending !== null} aria-busy={pending === "resume"}><Play size={16} /> Resume batch</button>}{job.status === "RUNNING" && job.queued_count > 0 && <button type="button" className="button secondary" onClick={() => action("dispatch-next")} disabled={pending !== null} aria-busy={pending === "dispatch-next"}><Play size={16} /> Dispatch next wave</button>}{["QUEUED", "RUNNING", "PAUSED"].includes(job.status) && <button type="button" className="button danger" onClick={() => action("cancel")} disabled={pending !== null} aria-busy={pending === "cancel"}>Cancel batch</button>}</div></section></div>;
}

function JobStat({ label, value, tone }: { label: string; value: number; tone: string }) { return <div className={`job-stat ${tone}`}><span>{label}</span><strong>{value}</strong></div>; }

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
  return <div className="modal-backdrop" role="presentation"><section className="modal session-modal" role="dialog" aria-modal="true" aria-labelledby="session-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> PROFILE-BACKED SESSION</div><h2 id="session-title">Sign in to {session.account_email}</h2><p>Session {session.id} · profile lock {session.profile_id}</p></div><button type="button" className="icon-button" aria-label="Close session workspace" onClick={onClose}><X size={18} /></button></div><div className="session-body"><div className="session-hero"><div className="session-orbit"><Globe2 size={24} /></div><div><span className="detail-label">Current session state</span><strong className={`session-state ${session.state.toLowerCase()}`}>{labelStatus(session.state)}</strong><p>{session.browser_opened ? (session.automation_hint ?? "A separate standard Chrome session is open with the saved browser profile. Sign in there manually.") : session.login_state === "REQUIRED" ? "The isolated standard Chrome session is launching for manual sign-in." : session.login_state === "ACCOUNT_NOT_FOUND" ? "Google reports that no account exists for this email address." : session.login_state === "AUTHENTICATED" ? "The browser reported a completed sign-in. Keep the profile open only while needed." : session.failure_reason ?? "Review the account in the controlled browser session."}</p></div></div><div className="session-grid"><DetailCard icon={<Layers3 size={17} />} label="Persistent profile" value={session.profile_status} note={session.profile_saved ? "Saved · reopens without login" : "Created once the sign-in completes"} /><DetailCard icon={<LockKeyhole size={17} />} label="Profile lock" value={isOpen ? "Locked to this session" : "Released"} note={session.profile_storage_reference} /><DetailCard icon={<ShieldCheck size={17} />} label="Runtime boundary" value={session.browser_opened ? "Separate Chrome session" : "Human hand-off"} note="Separate from the SaaS console" /></div><div className="session-steps"><div className={`session-step ${session.login_state === "REQUIRED" ? "current" : "done"}`}><span>01</span><div><strong>Manual sign-in in the separate browser</strong><small>{session.browser_opened ? (session.automation_hint ?? "Enter the saved account credentials in the separate standard Chrome window.") : "The isolated standard Chrome window is launching for manual sign-in."}</small></div></div><div className={`session-step ${session.login_state === "AUTHENTICATED" ? "done" : "current"}`}><span>02</span><div><strong>Record the browser result</strong><small>Only record completion after Google visibly confirms the sign-in.</small></div></div></div><div className="session-warning"><ShieldCheck size={17} /><span>This browser window is separate from the SaaS console. Passwords, recovery values, and 2FA secrets remain in the encrypted vault and are never injected into this interface.</span></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Keep workspace open</button>{isOpen && <>{session.browser_opened ? <span className="button secondary session-opened"><Globe2 size={16} /> Separate Chrome window open</span> : <span className="button secondary session-opened"><Globe2 size={16} /> Isolated browser launching</span>}<button type="button" className="button secondary" onClick={() => updateEvent("VERIFICATION_REQUIRED")} disabled={pending !== null} aria-busy={pending === "verify"}>Needs verification</button><button type="button" className="button primary" onClick={() => updateEvent("LOGIN_COMPLETED")} disabled={pending !== null} aria-busy={pending === "complete"}>{pending === "complete" && <span className="button-spinner" />}<Check size={16} /> Record sign-in complete</button><button type="button" className="button danger" onClick={close} disabled={pending !== null} aria-busy={pending === "close"}><Power size={16} /> Close session</button></>}</div></section></div>;
}
function DetailCard({ icon, label, value, note }: { icon: React.ReactNode; label: string; value: string; note: string }) { return <div className="detail-card"><span className="detail-card-icon">{icon}</span><span className="detail-label">{label}</span><strong>{value}</strong><small>{note}</small></div>; }
function ToastRegion({ toasts }: { toasts: Toast[] }) { return <div className="toast-region" aria-live="polite" aria-atomic="true">{toasts.map((toast) => <div className={`toast ${toast.tone}`} key={toast.id}><span>{toast.tone === "success" ? <Check size={15} /> : toast.tone === "error" ? <XCircle size={15} /> : <ShieldCheck size={15} />}</span>{toast.message}</div>)}</div>; }

export default App;
