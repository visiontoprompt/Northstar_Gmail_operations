import { useEffect, useState } from "react";
import { Archive, CheckCircle2, ShieldCheck, X, XCircle } from "lucide-react";
import { api } from "../api";

type Target = { id: string; email: string };
type Row = { account_id: string; email: string; status: "COMPACTED" | "UNCHANGED" | "MISSING" | "SKIPPED_ACTIVE" | "FAILED"; removed_bytes: number; removed_directories: number };
type Scope = "selected" | "all";

const size = (bytes: number) => bytes < 1024 * 1024 ? `${Math.round(bytes / 1024)} KB` : `${(bytes / (1024 * 1024)).toFixed(1)} MB`;

export function ProfileCompactionModal({ selected, onClose }: { selected: Target[]; onClose: () => void }) {
  const [scope, setScope] = useState<Scope>(selected.length ? "selected" : "all");
  const [allTargets, setAllTargets] = useState<Target[]>([]);
  const [loadingTargets, setLoadingTargets] = useState(true);
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState(0);
  const [total, setTotal] = useState(0);
  const [rows, setRows] = useState<Row[]>([]);
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    api<{ items: Target[] }>("/api/v1/profiles/compact-targets").then((data) => { if (!cancelled) setAllTargets(data.items); }).catch((failure) => { if (!cancelled) setError((failure as Error).message); }).finally(() => { if (!cancelled) setLoadingTargets(false); });
    return () => { cancelled = true; };
  }, []);
  const run = async () => {
    const targets = scope === "selected" ? selected : allTargets;
    if (!targets.length || busy) return;
    setBusy(true); setError(""); setRows([]); setCompleted(0); setTotal(targets.length);
    let processed = 0;
    try {
      for (let index = 0; index < targets.length; index += 10) {
        const chunk = targets.slice(index, index + 10);
        const result = await api<{ results: Row[] }>("/api/v1/profiles/compact", { method: "POST", body: JSON.stringify({ account_ids: chunk.map((item) => item.id) }) });
        setRows((current) => [...current, ...result.results]);
        processed = Math.min(index + chunk.length, targets.length);
        setCompleted(processed);
      }
    } catch (failure) {
      setError(`${(failure as Error).message} ${processed} account${processed === 1 ? "" : "s"} completed before this request stopped. You can retry safely.`);
    } finally { setBusy(false); }
  };
  const removed = rows.reduce((sum, row) => sum + row.removed_bytes, 0);
  const compacted = rows.filter((row) => row.status === "COMPACTED").length;
  const skipped = rows.filter((row) => row.status === "SKIPPED_ACTIVE").length;
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}><section className="modal profile-compact-modal" role="dialog" aria-modal="true" aria-labelledby="profile-compact-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> BROWSER STORAGE</div><h2 id="profile-compact-title">Compact browser profiles</h2><p>Remove regenerable cache files from closed account browsers.</p></div><button type="button" className="icon-button" aria-label="Close profile compaction" onClick={onClose} disabled={busy}><X size={18} /></button></div><div className="modal-body profile-compact-body"><div className="profile-compact-safety"><ShieldCheck size={18} /><div><strong>Saved sign-in data stays intact</strong><span>Cookies, Local State, site storage, Service Workers, and preferences are not touched. Open profiles are skipped automatically.</span></div></div><fieldset className="profile-compact-scope"><legend>Accounts to compact</legend><label><input type="radio" name="compact-scope" checked={scope === "selected"} disabled={busy || !selected.length} onChange={() => setScope("selected")} /><span><strong>Selected accounts</strong><small>{selected.length} selected in the registry</small></span></label><label><input type="radio" name="compact-scope" checked={scope === "all"} disabled={busy || loadingTargets} onChange={() => setScope("all")} /><span><strong>All accessible accounts</strong><small>{loadingTargets ? "Loading count…" : `${allTargets.length} accounts across your permitted groups`}</small></span></label></fieldset>{busy && <div className="profile-compact-progress" role="status" aria-live="polite"><div><strong>Checking closed profiles</strong><span>{completed} / {total}</span></div><progress value={completed} max={Math.max(total, 1)} /></div>}{error && <div className="inline-error" role="alert"><XCircle size={16} /><span>{error}</span></div>}{rows.length > 0 && <div className="profile-compact-outcome" role="status"><div className="profile-compact-summary"><span><CheckCircle2 size={17} /> <strong>{size(removed)}</strong> cache data removed</span><small>{compacted} compacted · {skipped} open and skipped · {rows.length - compacted - skipped} unchanged or unavailable</small></div><div className="profile-compact-list">{rows.map((row) => <div key={row.account_id}><span>{row.email}</span><strong>{row.status === "SKIPPED_ACTIVE" ? "Browser open" : row.status === "COMPACTED" ? size(row.removed_bytes) : row.status === "MISSING" ? "No saved profile" : row.status === "FAILED" ? "Failed" : "Already clean"}</strong></div>)}</div></div>}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={busy}>{rows.length ? "Done" : "Cancel"}</button><button type="button" className="button primary" onClick={() => void run()} disabled={busy || loadingTargets || (scope === "selected" ? !selected.length : !allTargets.length)}><Archive size={15} />{busy ? "Compacting…" : rows.length ? "Run again" : "Compact profiles"}</button></div></section></div>;
}
