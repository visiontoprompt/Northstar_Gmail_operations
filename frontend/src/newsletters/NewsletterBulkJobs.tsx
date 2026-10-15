import { useEffect, useRef, useState } from "react";
import { Check, ChevronDown, ClipboardList, FileCog, Pause, Play, RefreshCw, RotateCcw, Square, Trash2, X, XCircle } from "lucide-react";
import { newsletterRequests } from "./newsletterApi";
import type { AccountGroupOption, Newsletter, NewsletterGroup, NewsletterJob, NewsletterTask } from "./newsletterTypes";

const label = (value: string) => value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
const terminal = new Set(["SUBMITTED", "CONFIRMED", "SKIPPED", "FAILED", "MANUAL_REVIEW", "CANCELLED"]);

export function NewsletterBulkJobs({ jobs, newsletters, newsletterGroups, accountGroups, canOperate, onCreate, onRefresh, onStopAll, onViewResults }: { jobs: NewsletterJob[]; newsletters: Newsletter[]; newsletterGroups: NewsletterGroup[]; accountGroups: AccountGroupOption[]; canOperate: boolean; onCreate: () => void; onRefresh: () => void; onStopAll: () => void; onViewResults: (jobId: string) => void }) {
  const [detail, setDetail] = useState<NewsletterJob | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [pending, setPending] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [confirmCancelJobId, setConfirmCancelJobId] = useState<string | null>(null);
  const [confirmDeleteJobId, setConfirmDeleteJobId] = useState<string | null>(null);
  const [confirmBulkDelete, setConfirmBulkDelete] = useState(false);
  const deletableJobs = jobs.filter((job) => ["DRAFT", "COMPLETED", "CANCELLED", "FAILED"].includes(job.status));
  const selectedDeletableIds = selected.filter((id) => deletableJobs.some((job) => job.id === id));

  const loadDetail = async (id: string) => {
    try { setDetail(await newsletterRequests.getJob(id)); setSelected([]); setError(""); } catch (requestError) { setError((requestError as Error).message); }
  };
  useEffect(() => {
    if (!detail || !["RUNNING", "PAUSED"].includes(detail.status)) return;
    let current = true;
    let requestPending = false;
    const refreshDetail = async () => {
      if (!current || requestPending || document.visibilityState !== "visible") return;
      requestPending = true;
      try {
        const next = await newsletterRequests.getJob(detail.id);
        if (current) setDetail(next);
      } catch { /* the next refresh will surface a durable error */ }
      finally { requestPending = false; }
    };
    const timer = window.setInterval(() => { void refreshDetail(); }, 5000);
    return () => { current = false; window.clearInterval(timer); };
  }, [detail?.id, detail?.status]);

  const act = async (action: string, request: () => Promise<NewsletterJob>) => {
    setPending(action); setError("");
    try { const next = await request(); setDetail(next); onRefresh(); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(null); setConfirmCancel(false); setConfirmCancelJobId(null); }
  };
  const deleteHistory = async (id: string) => {
    setPending(`delete-${id}`); setError("");
    try {
      await newsletterRequests.deleteJob(id);
      if (detail?.id === id) setDetail(null);
      setConfirmDeleteJobId(null);
      onRefresh();
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setPending(null); }
  };
  const deleteSelectedHistory = async () => {
    if (!selectedDeletableIds.length) return;
    setPending("delete-selected"); setError("");
    try {
      const result = await newsletterRequests.deleteJobs(selectedDeletableIds);
      if (detail && selectedDeletableIds.includes(detail.id)) setDetail(null);
      setSelected([]); setConfirmBulkDelete(false); onRefresh();
      setError("");
      // A concise success message remains in the jobs panel after refresh.
      setBulkDeleteMessage(`${result.deleted_count} job ${result.deleted_count === 1 ? "history" : "histories"} deleted.`);
    } catch (requestError) { setError((requestError as Error).message); }
    finally { setPending(null); }
  };
  const [bulkDeleteMessage, setBulkDeleteMessage] = useState("");
  const counts = detail?.task_counts ?? {};
  const unresolved = detail ? Object.entries(counts).filter(([state]) => !terminal.has(state)).reduce((sum, [, count]) => sum + count, 0) : 0;
  const selectedTasks = detail?.tasks.filter((task) => selected.includes(task.id)) ?? [];
  const retrySelectionReady = selectedTasks.length > 0 && selectedTasks.every((task) => ["FAILED", "MANUAL_REVIEW"].includes(task.state));
  const skipSelectionReady = selectedTasks.length > 0 && selectedTasks.every((task) => ["QUEUED", "RETRY_PENDING", "WAITING_CONFIRMATION"].includes(task.state));
  const toggle = (task: NewsletterTask) => setSelected((current) => current.includes(task.id) ? current.filter((id) => id !== task.id) : [...current, task.id]);

  return <section className="newsletter-panel">
    <div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> BULK EXECUTION</div><h2>Newsletter jobs</h2><p>Each run creates one auditable task per account and newsletter, then dispatches only within the configured concurrency limit.</p></div>{canOperate && <div className="newsletter-actions"><button type="button" className="button primary small" onClick={onCreate}><FileCog size={15} /> New bulk job</button>{jobs.some((job) => ["RUNNING", "PAUSED"].includes(job.status)) && <button type="button" className="button danger small" onClick={onStopAll}><Square size={14} /> Stop all active</button>}</div>}</div>
    {error && <div className="form-error" role="alert"><XCircle size={16} /><span>{error}</span></div>}
    {bulkDeleteMessage && <div className="newsletter-inline-notice" role="status">{bulkDeleteMessage}<button type="button" className="icon-button" aria-label="Dismiss message" onClick={() => setBulkDeleteMessage("")}><X size={13} /></button></div>}
    {jobs.length ? <><div className="newsletter-actions newsletter-bulk-toolbar">{canOperate && <label className="newsletter-select-all"><input type="checkbox" aria-label="Select all deletable newsletter jobs" checked={deletableJobs.length > 0 && deletableJobs.every((job) => selected.includes(job.id))} onChange={(event) => setSelected(event.target.checked ? deletableJobs.map((job) => job.id) : [])} /> Select finished and draft jobs</label>}{canOperate && selectedDeletableIds.length > 0 && (confirmBulkDelete ? <><span className="table-subtext">Delete {selectedDeletableIds.length} job histories? Active jobs are excluded.</span><button type="button" className="button danger small" disabled={pending !== null} onClick={() => void deleteSelectedHistory()}><Trash2 size={14} /> Confirm delete</button><button type="button" className="button secondary small" onClick={() => setConfirmBulkDelete(false)}>Keep</button></> : <button type="button" className="button danger small" disabled={pending !== null} onClick={() => setConfirmBulkDelete(true)}><Trash2 size={14} /> Delete selected ({selectedDeletableIds.length})</button>)}</div><div className="table-scroll newsletter-table-scroll"><table className="newsletter-table"><caption className="sr-only">Newsletter bulk jobs</caption><thead><tr><th>Select</th><th>Job</th><th>Target</th><th>Account group</th><th>Status</th><th>Progress</th><th>Selection</th><th>Concurrency</th><th>Actions</th></tr></thead><tbody>{jobs.map((job) => { const done = Object.entries(job.task_counts ?? {}).filter(([state]) => terminal.has(state)).reduce((sum, [, count]) => sum + count, 0); const stoppable = ["RUNNING", "PAUSED"].includes(job.status); const canDelete = ["DRAFT", "COMPLETED", "CANCELLED", "FAILED"].includes(job.status); return <tr key={job.id}><td><input type="checkbox" aria-label={`Select job ${job.id}`} disabled={!canOperate || !canDelete || pending !== null} checked={selected.includes(job.id)} onChange={() => setSelected((current) => current.includes(job.id) ? current.filter((id) => id !== job.id) : [...current, job.id])} /></td><td><button type="button" className="newsletter-name-button" onClick={() => void loadDetail(job.id)}><span className="newsletter-avatar"><FileCog size={14} /></span><span><strong>{job.id}</strong><small>{job.total_tasks || "Not materialized"} tasks</small></span></button></td><td><strong>{job.newsletter_name ?? job.newsletter_group_name ?? "No target"}</strong><small className="table-subtext">{job.newsletter_id ? "Single newsletter" : "Newsletter group"}</small></td><td>{job.account_group_name}</td><td><span className={`newsletter-boolean ${job.status === "COMPLETED" ? "yes" : job.status === "CANCELLED" ? "no" : ""}`}>{label(job.status)}</span></td><td>{job.total_tasks ? `${done}/${job.total_tasks} resolved` : "Ready to start"}</td><td>{job.randomize_newsletters ? `Random · ${job.random_newsletter_count}/account` : "All eligible"}</td><td>{job.concurrency_limit}</td><td><div className="newsletter-actions">{canOperate && job.status === "DRAFT" && <button type="button" className="icon-text-button" disabled={pending !== null} onClick={() => void act(`start-${job.id}`, () => newsletterRequests.startJob(job.id))}><Play size={13} /> Start</button>}{canOperate && job.status === "RUNNING" && <button type="button" className="icon-text-button" disabled={pending !== null} onClick={() => void act(`pause-${job.id}`, () => newsletterRequests.pauseJob(job.id))}><Pause size={13} /> Pause</button>}{canOperate && job.status === "PAUSED" && <button type="button" className="icon-text-button" disabled={pending !== null} onClick={() => void act(`resume-${job.id}`, () => newsletterRequests.resumeJob(job.id))}><Play size={13} /> Resume</button>}{canOperate && stoppable && (confirmCancelJobId === job.id ? <><span className="table-subtext">Stop this run?</span><button type="button" className="icon-text-button danger-text" disabled={pending !== null} onClick={() => void act(`cancel-${job.id}`, () => newsletterRequests.cancelJob(job.id))}>Confirm stop</button></> : <button type="button" className="icon-text-button danger-text" disabled={pending !== null} onClick={() => setConfirmCancelJobId(job.id)}><Square size={13} /> Stop</button>)}<button type="button" className="icon-text-button" onClick={() => void loadDetail(job.id)}><ChevronDown size={13} /> Details</button><button type="button" className="icon-button newsletter-results-button" title="View operation results" aria-label={`View results for ${job.id}`} onClick={() => onViewResults(job.id)}><ClipboardList size={15} /></button>{canOperate && canDelete && (confirmDeleteJobId === job.id ? <><span className="table-subtext">Delete this run history?</span><button type="button" className="icon-text-button danger-text" disabled={pending !== null} onClick={() => void deleteHistory(job.id)}>Confirm delete</button><button type="button" className="icon-text-button" onClick={() => setConfirmDeleteJobId(null)}>Keep</button></> : <button type="button" className="icon-text-button danger-text" disabled={pending !== null} onClick={() => setConfirmDeleteJobId(job.id)}><Trash2 size={13} /> Delete history</button>)}</div></td></tr>; })}</tbody></table></div></> : <div className="newsletter-empty"><FileCog size={20} /><strong>No bulk jobs yet</strong><span>Create a job to run a bounded newsletter operation for an account group.</span>{canOperate && accountGroups.length > 0 && <button type="button" className="button primary small" onClick={onCreate}>Create bulk job</button>}</div>}
    {detail && <div className="newsletter-panel newsletter-job-detail"><div className="panel-header"><div><div className="eyebrow"><span className="eyebrow-line" /> RUN DETAIL</div><h3>{detail.newsletter_name ?? detail.newsletter_group_name} · {detail.account_group_name}</h3><p>{detail.id} · {detail.total_tasks} tasks · {unresolved} unresolved</p></div><div className="newsletter-actions"><button type="button" className="button secondary small" onClick={() => void loadDetail(detail.id)}><RefreshCw size={13} /> Refresh detail</button>{canOperate && ["RUNNING", "PAUSED"].includes(detail.status) && (confirmCancel ? <><span className="table-subtext">Cancel this run?</span><button type="button" className="button danger small" disabled={pending !== null} onClick={() => void act("cancel", () => newsletterRequests.cancelJob(detail.id))}>Confirm cancel</button></> : <button type="button" className="button secondary small" onClick={() => setConfirmCancel(true)}><Square size={13} /> Cancel</button>)}</div></div><div className="newsletter-metric-grid">{Object.entries(detail.task_counts ?? {}).map(([state, count]) => <div className="newsletter-metric" key={state}><div className="newsletter-metric-top"><span>{label(state)}</span><Check size={14} /></div><strong>{count}</strong><small>tasks</small></div>)}</div>{detail.tasks.length ? <div className="table-scroll newsletter-table-scroll"><table className="newsletter-table"><caption className="sr-only">Newsletter task detail</caption><thead><tr><th><input type="checkbox" aria-label="Select retryable tasks" checked={selected.length > 0 && selected.length === detail.tasks.filter((task) => ["FAILED", "MANUAL_REVIEW", "QUEUED", "RETRY_PENDING", "WAITING_CONFIRMATION"].includes(task.state)).length} onChange={(event) => setSelected(event.target.checked ? detail.tasks.filter((task) => ["FAILED", "MANUAL_REVIEW", "QUEUED", "RETRY_PENDING", "WAITING_CONFIRMATION"].includes(task.state)).map((task) => task.id) : [])} /></th><th>Account</th><th>Newsletter</th><th>State</th><th>Attempts</th><th>Last error</th></tr></thead><tbody>{detail.tasks.map((task) => <tr key={task.id}><td><input type="checkbox" aria-label={`Select ${task.account_email}`} disabled={!["FAILED", "MANUAL_REVIEW", "QUEUED", "RETRY_PENDING", "WAITING_CONFIRMATION"].includes(task.state)} checked={selected.includes(task.id)} onChange={() => toggle(task)} /></td><td><strong className="mono">{task.account_email}</strong></td><td>{task.newsletter_name}</td><td><span className="newsletter-boolean">{label(task.state)}</span></td><td>{task.attempts}</td><td className="newsletter-error-cell">{task.last_error || "—"}</td></tr>)}</tbody></table></div> : <div className="newsletter-empty"><span>Start the job to materialize account tasks.</span></div>}{canOperate && detail.tasks.length > 0 && <div className="modal-footer"><button type="button" className="button secondary" disabled={!skipSelectionReady || pending !== null} onClick={() => void act("skip", () => newsletterRequests.skipSelected(detail.id, selected))}>Skip selected</button><button type="button" className="button secondary" disabled={!retrySelectionReady || pending !== null} onClick={() => void act("retry", () => newsletterRequests.retryFailed(detail.id, selected))}><RotateCcw size={14} /> Retry selected</button><button type="button" className="button primary" disabled={pending !== null || !Object.entries(detail.task_counts ?? {}).some(([state, count]) => ["FAILED", "MANUAL_REVIEW"].includes(state) && count > 0)} onClick={() => void act("retry-all", () => newsletterRequests.retryFailed(detail.id))}>Retry all failed</button></div>}</div>}
  </section>;
}

export function NewsletterJobResultsDialog({ job, loading, error, onRetry, onClose }: { job: NewsletterJob | null; loading: boolean; error: string; onRetry: () => void; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const taskStates = Object.entries(job?.task_counts ?? {}).sort(([left], [right]) => left.localeCompare(right));
  useEffect(() => {
    const dialog = dialogRef.current;
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    return () => { if (dialog.open) dialog.close(); };
  }, []);
  return <dialog ref={dialogRef} className="modal newsletter-modal newsletter-modal-wide newsletter-results-modal" aria-labelledby="newsletter-results-title" aria-describedby="newsletter-results-description" onCancel={(event) => { event.preventDefault(); onClose(); }} onClick={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> BULK SUBSCRIBE · OPERATION REPORT</div><h2 id="newsletter-results-title">{job ? `Results · ${job.newsletter_name ?? job.newsletter_group_name ?? "Newsletter run"}` : "Operation results"}</h2><p id="newsletter-results-description">{job ? `${job.id} · ${job.account_group_name} · ${label(job.status)} · ${job.completed_at ? `Finished ${new Intl.DateTimeFormat("en-US", { dateStyle: "medium", timeStyle: "short" }).format(new Date(job.completed_at))}` : "Latest saved results"}` : "Loading the saved results for this operation."}</p></div><button type="button" className="icon-button" aria-label="Close results" autoFocus onClick={onClose}><X size={18} /></button></div>
    {loading && <div className="newsletter-results-state" role="status"><span className="spinner" /><strong>Loading operation results…</strong></div>}
    {!loading && error && <div className="newsletter-results-state error" role="alert"><XCircle size={18} /><strong>{error}</strong><button type="button" className="button secondary small" onClick={onRetry}>Try again</button></div>}
    {!loading && !error && job && <>
      <div className="newsletter-results-summary"><div><span>Overall status</span><strong>{label(job.status)}</strong></div><div><span>Resolved tasks</span><strong>{Object.entries(job.task_counts ?? {}).filter(([state]) => terminal.has(state)).reduce((sum, [, count]) => sum + count, 0)} / {job.total_tasks}</strong></div><div><span>Concurrency</span><strong>{job.concurrency_limit} accounts</strong></div><div><span>Newsletter selection</span><strong>{job.randomize_newsletters ? `Random · ${job.random_newsletter_count ?? 0} per account` : "All eligible"}</strong></div></div>
      <h3 className="newsletter-results-section-title">Outcome totals</h3>
      {taskStates.length ? <div className="newsletter-results-counts">{taskStates.map(([state, count]) => <div key={state}><span className={`newsletter-results-dot ${["FAILED", "MANUAL_REVIEW"].includes(state) ? "danger" : ["SUBMITTED", "CONFIRMED", "SKIPPED"].includes(state) ? "success" : ""}`} />{label(state)}<strong>{count}</strong></div>)}</div> : <p className="newsletter-results-empty">No task outcomes have been recorded for this operation.</p>}
      <h3 className="newsletter-results-section-title">Account and newsletter outcomes</h3>
      {job.tasks.length ? <div className="table-scroll newsletter-table-scroll newsletter-results-table-scroll"><table className="newsletter-table"><caption className="sr-only">Account and newsletter results for this bulk operation</caption><thead><tr><th>Account</th><th>Newsletter</th><th>Result</th><th>Attempts</th><th>Notes</th></tr></thead><tbody>{job.tasks.map((task) => <tr key={task.id}><td><strong className="mono">{task.account_email}</strong></td><td>{task.newsletter_name}</td><td><span className="newsletter-boolean">{label(task.state)}</span></td><td>{task.attempts}</td><td className="newsletter-error-cell">{task.last_error || "—"}</td></tr>)}</tbody></table></div> : <p className="newsletter-results-empty">This job has no materialized account tasks yet.</p>}
    </>}
    <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close report</button></div>
  </dialog>;
}
