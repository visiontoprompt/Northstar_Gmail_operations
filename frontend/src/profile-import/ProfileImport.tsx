import { useRef, useState } from "react";
import { CheckCircle2, FileArchive, ShieldCheck, Upload, X, XCircle } from "lucide-react";

type ImportRow = { account_id: string; email: string; status: "IMPORTED" | "REPLACED" | "SKIPPED" | "LOCKED" | "FAILED"; safety_copy?: string | null };
type ImportResult = { results: ImportRow[]; unmatched: string[]; imported: number; replaced: number; skipped: number; locked: number; failed: number };
type UploadState = { phase: "uploading" | "extracting"; percent: number } | null;

function sendArchive(url: string, file: File, policy: "skip" | "replace", onProgress: (state: UploadState) => void): Promise<ImportResult> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    const data = new FormData();
    data.append("file", file);
    if (url.endsWith("/import")) data.append("policy", policy);
    request.open("POST", url);
    request.withCredentials = true;
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress({ phase: "uploading", percent: Math.min(100, Math.round(event.loaded / event.total * 100)) });
    };
    request.upload.onload = () => onProgress({ phase: "extracting", percent: 100 });
    request.onerror = () => reject(new Error("The upload could not reach the server."));
    request.onload = () => {
      let response: { detail?: string } & Partial<ImportResult> = {};
      try { response = JSON.parse(request.responseText); } catch { /* HTTP failure can be HTML. */ }
      if (request.status >= 200 && request.status < 300) resolve(response as ImportResult);
      else reject(new Error(response.detail || `Profile import failed (HTTP ${request.status}).`));
    };
    onProgress({ phase: "uploading", percent: 0 });
    request.send(data);
  });
}

function ResultList({ result }: { result: ImportResult }) {
  return <div className="profile-import-results" role="status"><div className="profile-import-totals"><strong>{result.imported + result.replaced} saved</strong><span>{result.skipped} skipped · {result.locked} in use · {result.failed} failed</span></div><div className="profile-import-result-list">{result.results.map((row) => <div key={row.account_id} className={`profile-import-result ${row.status.toLowerCase()}`}><span>{row.status === "FAILED" || row.status === "LOCKED" ? <XCircle size={15} /> : <CheckCircle2 size={15} />}</span><strong>{row.email}</strong><small>{row.status === "REPLACED" ? "Replaced · previous profile retained on server" : row.status === "LOCKED" ? "Close its browser session, then retry" : row.status.toLowerCase()}</small></div>)}</div>{result.unmatched.length > 0 && <p className="profile-import-unmatched">Unmatched folders: {result.unmatched.join(", ")}</p>}</div>;
}

function Progress({ state }: { state: NonNullable<UploadState> }) {
  return <div className="profile-import-progress" role="status" aria-live="polite"><div><strong>{state.phase === "uploading" ? "Uploading archive" : "Validating and installing profiles"}</strong><span>{state.phase === "uploading" ? `${state.percent}%` : "Server processing"}</span></div><div className={`profile-import-track ${state.phase === "extracting" ? "indeterminate" : ""}`}><span style={{ width: state.phase === "uploading" ? `${state.percent}%` : undefined }} /></div></div>;
}

export function AccountProfileImportModal({ account, onClose, onImported }: { account: { id: string; email: string }; onClose: () => void; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [progress, setProgress] = useState<UploadState>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const busy = progress !== null;
  const upload = async () => {
    if (!file || !confirmed || busy) return;
    setError(""); setResult(null);
    try {
      const next = await sendArchive(`/api/v1/backups/profiles/account/${encodeURIComponent(account.id)}`, file, "replace", setProgress);
      setResult(next);
      if (next.imported + next.replaced > 0) onImported();
    } catch (failure) { setError((failure as Error).message); }
    finally { setProgress(null); }
  };
  return <div className="modal-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget && !busy) onClose(); }}><section className="modal profile-import-modal" role="dialog" aria-modal="true" aria-labelledby="account-profile-import-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> BROWSER PROFILE</div><h2 id="account-profile-import-title">Upload saved profile</h2><p>{account.email}</p></div><button type="button" className="icon-button" onClick={onClose} disabled={busy} aria-label="Close profile upload"><X size={18} /></button></div><div className="modal-body profile-import-body"><div className="profile-import-callout"><ShieldCheck size={18} /><span>Import a ZIP or RAR of this account's Chrome user-data folder. The browser session must be closed. An existing profile is replaced, with the previous copy retained on the server.</span></div><label className="field"><span>Profile archive</span><input type="file" accept=".zip,.rar,application/zip,application/vnd.rar" disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); setConfirmed(false); }} /></label><small className="profile-import-help">A direct profile folder or a ZIP/RAR containing one profile is accepted. Maximum archive size: 20 GB.</small><label className="backup-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /><span>Replace the current profile for this account. I have closed its browser session.</span></label>{progress && <Progress state={progress} />}{error && <div className="inline-error" role="alert"><XCircle size={16} /><span>{error}</span></div>}{result && <ResultList result={result} />}</div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={busy}>{result ? "Done" : "Cancel"}</button><button type="button" className="button primary" disabled={!file || !confirmed || busy} onClick={() => void upload()}><Upload size={15} />{busy ? "Importing…" : "Upload profile"}</button></div></section></div>;
}

export function BulkProfileImportSection() {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [policy, setPolicy] = useState<"skip" | "replace">("skip");
  const [confirmed, setConfirmed] = useState(false);
  const [progress, setProgress] = useState<UploadState>(null);
  const [error, setError] = useState("");
  const [result, setResult] = useState<ImportResult | null>(null);
  const busy = progress !== null;
  const upload = async () => {
    if (!file || busy || (policy === "replace" && !confirmed)) return;
    setError(""); setResult(null);
    try { setResult(await sendArchive("/api/v1/backups/profiles/import", file, policy, setProgress)); }
    catch (failure) { setError((failure as Error).message); }
    finally { setProgress(null); }
  };
  return <section className="backup-card profile-import-section" aria-labelledby="bulk-profile-import-title"><div className="backup-card-heading"><span className="backup-card-icon"><FileArchive size={18} /></span><div><h3 id="bulk-profile-import-title">Import browser profiles</h3><p>Move one or many saved Chrome profiles from your computer to this server. Match each archive folder by account email, account ID, or profile ID.</p></div></div><div className="profile-import-layout"><div className="profile-import-picker"><label className="field"><span>ZIP or RAR archive</span><input ref={inputRef} type="file" accept=".zip,.rar,application/zip,application/vnd.rar" disabled={busy} onChange={(event) => { setFile(event.target.files?.[0] ?? null); setResult(null); }} /></label><small>Place account profile folders inside one parent folder, then archive that folder. Files from unknown accounts are skipped.</small></div><fieldset className="profile-import-policy"><legend>When a profile already exists</legend><label><input type="radio" name="profile-policy" checked={policy === "skip"} disabled={busy} onChange={() => { setPolicy("skip"); setConfirmed(false); }} /><span><strong>Skip existing</strong><small>Keep the current server profile</small></span></label><label><input type="radio" name="profile-policy" checked={policy === "replace"} disabled={busy} onChange={() => setPolicy("replace")} /><span><strong>Replace existing</strong><small>Retain the previous copy on the server</small></span></label></fieldset></div>{policy === "replace" && <label className="backup-confirm"><input type="checkbox" checked={confirmed} disabled={busy} onChange={(event) => setConfirmed(event.target.checked)} /><span>I have closed affected browser sessions and want existing profiles replaced.</span></label>}{progress && <Progress state={progress} />}{error && <div className="inline-error" role="alert"><XCircle size={16} /><span>{error}</span></div>}{result && <ResultList result={result} />}<button type="button" className="button primary" disabled={!file || busy || (policy === "replace" && !confirmed)} onClick={() => void upload()}><Upload size={16} />{busy ? "Importing profiles…" : "Upload profiles"}</button></section>;
}
