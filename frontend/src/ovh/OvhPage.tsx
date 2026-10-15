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

const keyOf = (resource: Resource) => `${resource.resource_type}:${resource.service_name}:${resource.instance_id ?? ""}`;
const stopped = (status: string) => /^(stopped|off|shutoff|halted|shelved|suspended)$/i.test(status);
const running = (status: string) => /^(running|active|enabled|started)$/i.test(status);

export function OvhPage({ onOpenSettings, onNotify }: { onOpenSettings: () => void; onNotify: (tone: "success" | "error" | "info", message: string) => void }) {
  const [inventory, setInventory] = useState<Inventory | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [action, setAction] = useState<Action | null>(null);
  const [pending, setPending] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [proxyGroups, setProxyGroups] = useState<string[]>([]);
  const [groupLinks, setGroupLinks] = useState<ProxyGroupLink[]>([]);
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
      const [linkResult, proxyResult] = await Promise.allSettled([
        api<{ items: ProxyGroupLink[] }>("/api/v1/infrastructure/ovh/proxy-groups"),
        api<Array<{ proxy_group?: string | null }>>("/api/v1/proxies"),
      ]);
      if (linkResult.status === "fulfilled") setGroupLinks(linkResult.value.items);
      if (proxyResult.status === "fulfilled") setProxyGroups([...new Set(proxyResult.value.map((item) => item.proxy_group).filter((name): name is string => Boolean(name)))].sort((a, b) => a.localeCompare(b)));
      setError("");
    } catch (requestError) {
      setError((requestError as Error).message || "Could not load OVH resources.");
    } finally {
      loadingRef.current = false;
      if (!quiet) setLoading(false);
    }
  }, []);
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
    setPending(true);
    try {
      await api("/api/v1/infrastructure/ovh/power", { method: "POST", body: JSON.stringify({ resource_type: action.resource.resource_type, service_name: action.resource.service_name, instance_id: action.resource.instance_id, action: action.action }) });
      onNotify("success", `${action.action === "start" ? "Start" : "Stop"} requested for ${action.resource.name}. Status will update when OVH completes the task.`);
      setAction(null);
      void load(true);
    } catch (requestError) {
      onNotify("error", (requestError as Error).message);
    } finally { setPending(false); }
  };
  const vpsCount = inventory?.items.filter((item) => item.resource_type === "vps").length ?? 0;
  const instanceCount = inventory?.items.filter((item) => item.resource_type === "instance").length ?? 0;
  return <div className="workspace-page ovh-page">
    <div className="workspace-page-header"><div><div className="eyebrow"><span className="eyebrow-line" /> INFRASTRUCTURE CONTROL</div><h1>VPS providers</h1><p>OVHcloud services and Public Cloud instances. Power actions use your saved API credentials.</p></div><div className="ovh-header-actions"><button type="button" className="button secondary" onClick={() => void load()} disabled={loading}><RefreshCw size={16} className={loading ? "spin-icon" : ""} /> Refresh status</button><button type="button" className="button secondary" onClick={onOpenSettings}><KeyRound size={16} /> OVH settings</button></div></div>
    <section className="ovh-overview" aria-label="OVH resource totals"><div><Server size={18} /><span>VPS services</span><strong>{vpsCount}</strong></div><div><Cloud size={18} /><span>Public Cloud instances</span><strong>{instanceCount}</strong></div><div><Power size={18} /><span>Running</span><strong>{inventory?.items.filter((item) => running(item.status)).length ?? 0}</strong></div></section>
    {error && <div className="inline-error" role="alert"><AlertCircle size={18} /><span>{error}</span>{error.includes("Settings") && <button type="button" className="text-button" onClick={onOpenSettings}>Open settings</button>}<button type="button" className="text-button" onClick={() => void load()}>Retry</button></div>}
    {inventory?.errors.map((message) => <div className="inline-error" role="status" key={message}><AlertCircle size={18} /><span>{message}</span></div>)}
    <section className="workspace-panel"><div className="panel-header"><div><h2>OVHcloud inventory</h2><p>Automatically refreshes every minute while this page is visible{lastUpdated ? ` · last checked ${lastUpdated.toLocaleTimeString()}` : ""}.</p></div><span className="inventory-count">{inventory?.items.length ?? 0} resources</span></div>
      {loading && !inventory ? <div className="table-state loading-state" role="status"><span className="spinner" /><strong>Loading OVH resources</strong></div> : inventory && inventory.items.length === 0 ? <div className="table-state"><Server size={22} /><strong>No accessible resources</strong><p>Check the API region and the token permissions in Settings, then refresh.</p></div> : <div className="ovh-resource-list">{inventory?.items.map((resource) => {
        const resourceKey = keyOf(resource);
        const linkedGroups = groupLinks.filter((link) => link.resource_type === resource.resource_type && link.service_name === resource.service_name && (link.instance_id ?? "") === (resource.instance_id ?? ""));
        return <div className="ovh-resource" key={resourceKey}><span className="ovh-resource-icon">{resource.resource_type === "vps" ? <Server size={18} /> : <Cloud size={18} />}</span><div className="ovh-resource-main"><strong>{resource.name}</strong><small>{resource.resource_type === "vps" ? "VPS service" : `Public Cloud · ${resource.project_name}`} · {resource.service_name}{resource.instance_id ? ` / ${resource.instance_id}` : ""}</small>{resource.error && <small className="ovh-resource-error">{resource.error}</small>}<div className="ovh-group-link-editor"><label className="select-wrap"><span className="sr-only">Proxy group for {resource.name}</span><select aria-label={`Proxy group for ${resource.name}`} value={linkSelection[resourceKey] ?? ""} onChange={(event) => setLinkSelection((current) => ({ ...current, [resourceKey]: event.target.value }))}><option value="">Link to proxy group…</option>{proxyGroups.map((group) => <option value={group} key={group}>{group}</option>)}</select><ChevronDown size={15} /></label><button type="button" className="button secondary small" disabled={!linkSelection[resourceKey] || pending || !proxyGroups.length} onClick={() => void linkGroup(resource)}>Link group</button></div>{linkedGroups.length > 0 && <div className="ovh-linked-groups"><span>Linked proxy groups</span>{linkedGroups.map((link) => <span className="ovh-linked-group" key={link.id}>{link.proxy_group}<button type="button" title={`Unlink ${link.proxy_group}`} aria-label={`Unlink ${link.proxy_group}`} onClick={() => void unlinkGroup(link)} disabled={pending}><X size={12} /></button></span>)}</div>}</div><div className="ovh-resource-meta">{resource.ip && <code>{resource.ip}</code>}<span className={`ovh-state ${running(resource.status) ? "running" : stopped(resource.status) ? "stopped" : "unknown"}`}>{resource.status}</span></div><div className="ovh-resource-actions"><button type="button" className="button secondary" disabled={!stopped(resource.status) || pending} onClick={() => setAction({ resource, action: "start" })}>Start</button><button type="button" className="button secondary" disabled={!running(resource.status) || pending} onClick={() => setAction({ resource, action: "stop" })}>Stop</button></div></div>;
      })}</div>}
    </section>
    {action && <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !pending) setAction(null); }}><section className="modal ovh-confirm-modal" role="dialog" aria-modal="true" aria-labelledby="ovh-action-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> OVHCLOUD POWER</div><h2 id="ovh-action-title">{action.action === "start" ? "Start" : "Stop"} {action.resource.name}?</h2><p>{action.resource.resource_type === "vps" ? "VPS service" : "Public Cloud instance"} · {action.resource.service_name}</p></div><button type="button" className="icon-button" aria-label="Close power confirmation" onClick={() => setAction(null)} disabled={pending}><X size={18} /></button></div><div className="modal-body"><p>OVH will process this request asynchronously. Refresh status to see when the state changes.{action.action === "stop" ? " This resource will become unavailable while stopped." : ""}</p></div><div className="modal-footer"><button type="button" className="button secondary" onClick={() => setAction(null)} disabled={pending}>Cancel</button><button type="button" className="button primary" onClick={() => void runAction()} disabled={pending}>{pending ? "Sending request…" : `${action.action === "start" ? "Start" : "Stop"} resource`}</button></div></section></div>}
  </div>;
}
