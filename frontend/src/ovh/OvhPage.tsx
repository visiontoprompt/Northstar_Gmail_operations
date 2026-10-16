import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, ChevronDown, Cloud, KeyRound, Power, RefreshCw, Server, X } from "lucide-react";
import { api } from "../api";

type Resource = {
  resource_type: "vps" | "instance";
  service_name: string;
  instance_id: string | null;
  project_name: string | null;
  name: string;
  status: string;
  ip: string | null;
  error?: string;
};
type Inventory = { items: Resource[]; errors: string[] };
type Action = { resource: Resource; action: "start" | "stop" };
type ProxyGroupLink = { id: string; proxy_group: string; resource_type: Resource["resource_type"]; service_name: string; instance_id: string | null; server_name: string; status: string; ip: string | null };
type ProxyRecoveryJob = { id: string; status: string; stage: string; message: string; total_count: number; completed_count: number; cancel_requested: boolean; results: { floating_ip: string; status: string; exit_ip?: string; error?: string | null; attempts?: number; latency_ms?: number; recovered?: boolean }[]; created_at: string; updated_at: string };
const RECOVERY_PROJECT_ID = "239fa25938524e2f87575cc424a2b6fc";
const RECOVERY_INSTANCE_ID = "e998e455-3c20-40f3-bd9b-63d74a57d4de";

const keyOf = (resource: Resource) => `${resource.resource_type}:${resource.service_name}:${resource.instance_id ?? ""}`;
const stopped = (status: string) => /^(stopped|off|shutoff|halted|shelved|suspended)$/i.test(status);
const running = (status: string) => /^(running|active|enabled|started)$/i.test(status);

export function OvhPage({ onOpenSettings, onNotify, canManageSettings = true }: { onOpenSettings: () => void; onNotify: (tone: "success" | "error" | "info", message: string) => void; canManageSettings?: boolean }) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [pending, setPending] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [proxyGroups, setProxyGroups] = useState<string[]>([]);
  const [groupLinks, setGroupLinks] = useState<ProxyGroupLink[]>([]);
  const [recoveryJob, setRecoveryJob] = useState<ProxyRecoveryJob | null>(null);
  const [recoveryNotice, setRecoveryNotice] = useState("");
  const [powerNotice, setPowerNotice] = useState("");
  const [linkSelection, setLinkSelection] = useState<Record<string, string>>({});
  const loadingRef = useRef(false);
  const load = useCallback(async (quiet = false) => {
    if (loadingRef.current) return;
    loadingRef.current = true;
    if (!quiet) setLoading(true);
    try {
      const next = await api<Inventory>("/api/v1/infrastructure/ovh");
      setInventory(next);
      setLastUpdated(new Date());
      const [linkResult, proxyResult, recoveryResult] = await Promise.allSettled([
        api<{ items: ProxyGroupLink[] }>("/api/v1/infrastructure/ovh/proxy-groups"),
        api<Array<{ proxy_group?: string | null }>>("/api/v1/proxies"),
        api<ProxyRecoveryJob>(`/api/v1/infrastructure/ovh/recovery/latest?service_name=${encodeURIComponent(RECOVERY_PROJECT_ID)}&instance_id=${encodeURIComponent(RECOVERY_INSTANCE_ID)}`),
      ]);
      if (linkResult.status === "fulfilled") setGroupLinks(linkResult.value.items);
      if (proxyResult.status === "fulfilled") setProxyGroups([...new Set(proxyResult.value.map((item) => item.proxy_group).filter((name): name is string => Boolean(name)))].sort((a, b) => a.localeCompare(b)));
      if (recoveryResult.status === "fulfilled") setRecoveryJob(recoveryResult.value);
      setError("");
    } catch (requestError) {
      setError((requestError as Error).message || "Could not load OVH resources.");
    } finally {
      loadingRef.current = false;
      if (!quiet) setLoading(false);
    }
  }, []);
  useEffect(() => {
    if (!recoveryJob || !["QUEUED", "STARTING", "WAITING", "PROBING", "RECOVERING", "VERIFYING", "STOPPING"].includes(recoveryJob.status)) return;
    let cancelled = false;
    const poll = async () => {
      try { const latest = await api<ProxyRecoveryJob>(`/api/v1/infrastructure/ovh/recovery/${encodeURIComponent(recoveryJob.id)}`); if (!cancelled) setRecoveryJob(latest); }
      catch { /* Keep last known progress visible during brief polling failures. */ }
    };
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void poll(); }, 2000);
    void poll();
    return () => { cancelled = true; window.clearInterval(timer); };
  }, [recoveryJob?.id, recoveryJob?.status]);
  const linkGroup = async (resource: Resource) => {
    const key = keyOf(resource);
    const proxy_group = linkSelection[key];
    if (!proxy_group) return;
    setPending(true);
    try {
      await api("/api/v1/infrastructure/ovh/proxy-groups", { method: "POST", body: JSON.stringify({ proxy_group, resource_type: resource.resource_type, service_name: resource.service_name, instance_id: resource.instance_id }) });
      const next = await api<{ items: ProxyGroupLink[] }>("/api/v1/infrastructure/ovh/proxy-groups");
      setGroupLinks(next.items);
      onNotify("success", `${resource.name} linked to proxy group ${proxy_group}.`);
    } catch (requestError) { onNotify("error", (requestError as Error).message || "Could not link this OVH resource."); }
    finally { setPending(false); }
  };
  const unlinkGroup = async (link: ProxyGroupLink) => {
    setPending(true);
    try {
      await api(`/api/v1/infrastructure/ovh/proxy-groups/${link.id}`, { method: "DELETE" });
      setGroupLinks((current) => current.filter((item) => item.id !== link.id));
      onNotify("success", `${link.server_name} unlinked from ${link.proxy_group}.`);
    } catch (requestError) { onNotify("error", (requestError as Error).message || "Could not remove the proxy group link."); }
    finally { setPending(false); }
  };
  useEffect(() => { void load(); const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(true); }, 60000); return () => window.clearInterval(timer); }, [load]);
  const runAction = async () => {
    if (!action) return;
    const requestedAction = action;
    setPending(true);
    try {
      const result = await api<{ status: string; recovery_job?: ProxyRecoveryJob; recovery_warning?: string | null }>("/api/v1/infrastructure/ovh/power", { method: "POST", body: JSON.stringify({ resource_type: requestedAction.resource.resource_type, service_name: requestedAction.resource.service_name, instance_id: requestedAction.resource.instance_id, action: requestedAction.action }) });
      if (result.recovery_job) setRecoveryJob(result.recovery_job);
      setRecoveryNotice(requestedAction.action === "start" ? result.recovery_warning ?? "" : "");
      setPowerNotice(result.recovery_job ? `Start requested for ${requestedAction.resource.name}. Proxy verification is running.` : `${requestedAction.action === "start" ? "Start" : "Stop"} requested for ${requestedAction.resource.name}. Status will update when OVH completes the task.`);
      setAction(null);
      void load(true);
    } catch (requestError) {
      onNotify("error", (requestError as Error).message);
    } finally { setPending(false); }
  };
  const recoverProxies = async () => {
    setPending(true);
    try {
      const job = await api<ProxyRecoveryJob>("/api/v1/infrastructure/ovh/recovery/start", { method: "POST" });
      setRecoveryJob(job);
      setRecoveryNotice("");
      onNotify("success", "Proxy verification started. Each Floating IP will be checked before any recovery action.");
    } catch (requestError) { onNotify("error", (requestError as Error).message || "Proxy recovery could not start."); }
    finally { setPending(false); }
  };
  const vpsCount = inventory?.items.filter((item) => item.resource_type === "vps").length ?? 0;
  const instanceCount = inventory?.items.filter((item) => item.resource_type === "instance").length ?? 0;
  const legacyConfigurationFailure = recoveryJob?.status === "FAILED" && recoveryJob.completed_count === 0 && recoveryJob.message.includes("Proxy recovery is unavailable:");
  return <div className="workspace-page ovh-page">
    <div className="workspace-page-header"><div><div className="eyebrow"><span className="eyebrow-line" /> INFRASTRUCTURE CONTROL</div><h1>VPS providers</h1><p>OVHcloud services and Public Cloud instances. Power actions use your saved API credentials.</p></div><div className="ovh-header-actions"><button type="button" className="button secondary" onClick={() => void load()} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh status</button>{canManageSettings && <button type="button" className="button secondary" onClick={onOpenSettings}><KeyRound size={16} /> OVH settings</button>}</div></div>
    <section className="ovh-overview" aria-label="OVH resource totals"><div><Server size={18} /><span>VPS services</span><strong>{vpsCount}</strong></div><div><Cloud size={18} /><span>Public Cloud instances</span><strong>{instanceCount}</strong></div><div><Power size={18} /><span>Running</span><strong>{inventory?.items.filter((item) => running(item.status)).length ?? 0}</strong></div></section>
    {error && <div className="inline-error" role="alert"><AlertCircle size={18} /><span>{error}</span>{canManageSettings && error.includes("Settings") && <button type="button" className="text-button" onClick={onOpenSettings}>Open settings</button>}<button type="button" className="text-button" onClick={() => void load()}>Retry</button></div>}
    {inventory?.errors.map((message) => <div className="inline-error" role="status" key={message}><AlertCircle size={18} /><span>{message}</span></div>)}
    {powerNotice && <div className="ovh-power-notice" role="status"><Power size={16} /><span>{powerNotice}</span><button type="button" className="icon-button" aria-label="Dismiss power action status" onClick={() => setPowerNotice("")}><X size={14} /></button></div>}
    {recoveryNotice && <div className="ovh-recovery-notice" role="status"><AlertCircle size={16} /><span>{recoveryNotice}</span><button type="button" className="icon-button" aria-label="Dismiss proxy verification notice" onClick={() => setRecoveryNotice("")}><X size={14} /></button></div>}
    {recoveryJob && !legacyConfigurationFailure && <section className={`ovh-recovery-panel ${recoveryJob.status === "PROXY_READY" ? "ready" : recoveryJob.status === "FAILED" ? "failed" : "running"}`} aria-live="polite"><div className="ovh-recovery-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> FLOATING IP RECOVERY · {recoveryJob.stage.replaceAll("_", " ")}</div><h2>{recoveryJob.status === "PROXY_READY" ? "Proxy ready at last check" : recoveryJob.status === "FAILED" ? "Previous recovery attempt needs attention" : recoveryJob.status === "CANCELLED" ? "Recovery stopped safely" : "Restoring proxy connectivity"}</h2><p>{recoveryJob.message}</p><p className="ovh-recovery-timestamp">Attempt updated {new Date(recoveryJob.updated_at).toLocaleString()}. This is saved job history; use Verify/recover proxies to check the current setup.</p></div><strong className="ovh-recovery-count">{recoveryJob.completed_count}/{recoveryJob.total_count} checked</strong></div>{["QUEUED", "STARTING", "WAITING", "PROBING", "RECOVERING", "VERIFYING", "STOPPING"].includes(recoveryJob.status) && <progress value={recoveryJob.completed_count} max={Math.max(1, recoveryJob.total_count)} aria-label="Proxy recovery progress" />}{recoveryJob.results.length > 0 && <div className="ovh-recovery-results">{recoveryJob.results.map((result) => <div className="ovh-recovery-result" key={result.floating_ip}><code>{result.floating_ip}</code><span className={`ovh-recovery-badge ${result.status.toLowerCase()}`}>{result.status.replaceAll("_", " ")}</span>{result.exit_ip && <small>Exit {result.exit_ip}</small>}{result.latency_ms != null && <small>{result.latency_ms} ms</small>}{result.error && <small className="ovh-recovery-error">{result.error}</small>}</div>)}</div>}</section>}
    <section className="workspace-panel"><div className="panel-header"><div><h2>OVHcloud inventory</h2><p>Automatically refreshes every minute while this page is visible{lastUpdated ? ` · last checked ${lastUpdated.toLocaleTimeString()}` : ""}.</p></div><span className="inventory-count">{inventory?.items.length ?? 0} resources</span></div>
      {loading && !inventory ? <div className="table-state loading-state" role="status"><span className="spinner" /><strong>Loading OVH resources</strong></div> : inventory && inventory.items.length === 0 ? <div className="table-state"><Server size={22} /><strong>No accessible resources</strong><p>Check the API region and the token permissions in Settings, then refresh.</p></div> : <div className="ovh-resource-list">{inventory?.items.map((resource) => {
        const resourceKey = keyOf(resource);
        const linkedGroups = groupLinks.filter((link) => link.resource_type === resource.resource_type && link.service_name === resource.service_name && (link.instance_id ?? "") === (resource.instance_id ?? ""));
        return <div className="ovh-resource" key={resourceKey}><span className="ovh-resource-icon">{resource.resource_type === "vps" ? <Server size={18} /> : <Cloud size={18} />}</span><div className="ovh-resource-main"><strong>{resource.name}</strong><small>{resource.resource_type === "vps" ? "VPS service" : `Public Cloud · ${resource.project_name}`} · {resource.service_name}{resource.instance_id ? ` / ${resource.instance_id}` : ""}</small>{resource.error && <small className="ovh-resource-error">{resource.error}</small>}<div className="ovh-group-link-editor"><label className="select-wrap"><span className="sr-only">Proxy group for {resource.name}</span><select aria-label={`Proxy group for ${resource.name}`} value={linkSelection[resourceKey] ?? ""} onChange={(event) => setLinkSelection((current) => ({ ...current, [resourceKey]: event.target.value }))}><option value="">Link to proxy group…</option>{proxyGroups.map((group) => <option value={group} key={group}>{group}</option>)}</select><ChevronDown size={15} /></label><button type="button" className="button secondary small" disabled={!linkSelection[resourceKey] || pending || !proxyGroups.length} onClick={() => void linkGroup(resource)}>Link group</button></div>{linkedGroups.length > 0 && <div className="ovh-linked-groups"><span>Linked proxy groups</span>{linkedGroups.map((link) => <span className="ovh-linked-group" key={link.id}>{link.proxy_group}<button type="button" title={`Unlink ${link.proxy_group}`} aria-label={`Unlink ${link.proxy_group}`} onClick={() => void unlinkGroup(link)} disabled={pending}><X size={12} /></button></span>)}</div>}</div><div className="ovh-resource-meta">{resource.ip && <code>{resource.ip}</code>}<span className={`ovh-state ${running(resource.status) ? "running" : stopped(resource.status) ? "stopped" : "unknown"}`}>{resource.status}</span></div><div className="ovh-resource-actions"><button type="button" className="button secondary" disabled={!stopped(resource.status) || pending} onClick={() => setAction({ resource, action: "start" })}>Start</button><button type="button" className="button secondary" disabled={!running(resource.status) || pending} onClick={() => setAction({ resource, action: "stop" })}>Stop</button>{resource.resource_type === "instance" && resource.service_name === RECOVERY_PROJECT_ID && resource.instance_id === RECOVERY_INSTANCE_ID && running(resource.status) && <button type="button" className="button secondary" disabled={pending || Boolean(recoveryJob && ["QUEUED", "STARTING", "WAITING", "PROBING", "RECOVERING", "VERIFYING", "STOPPING"].includes(recoveryJob.status))} onClick={() => void recoverProxies()}>Verify/recover proxies</button>}</div></div>;
      })}</div>}
    </section>
    {action && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) setAction(null); }}><section className="modal ovh-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="ovh-action-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> OVHCLOUD POWER</div><h2 id="ovh-action-title">{action.action === "start" ? "Start" : "Stop"} {action.resource.name}?</h2><p>{action.resource.resource_type === "vps" ? "VPS service" : "Public Cloud instance"} · {action.resource.service_name}</p></div><button type="button" className="icon-button" aria-label="Close power confirmation" onClick={() => setAction(null)} disabled={pending}><X size={18} /></button></div><div className="modal-body"><p>OVH will process this request asynchronously. Refresh status to see when the state changes.{action.action === "stop" ? " This resource will become unavailable while stopped." : action.resource.resource_type === "instance" && action.resource.service_name === RECOVERY_PROJECT_ID && action.resource.instance_id === RECOVERY_INSTANCE_ID ? " If recovery credentials are missing, the VM will still start, but proxy readiness will remain unverified. Configure them on the server, then use Verify/recover proxies." : ""}</p></div><div className="modal-footer"><button type="button" className="button secondary" onClick={() => setAction(null)} disabled={pending}>Cancel</button><button type="button" className="button primary" onClick={() => void runAction()} disabled={pending}>{pending ? "Sending request…" : `${action.action === "start" ? "Start" : "Stop"} resource`}</button></div></section></div>}
  </div>;
}
