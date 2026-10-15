import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  AlertCircle,
  Archive,
  ArrowRight,
  BarChart3,
  Check,
  ChevronDown,
  Clock3,
  ExternalLink,
  FileCog,
  Inbox,
  ListFilter,
  Mail,
  Newspaper,
  Pencil,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  ShieldCheck,
  Square,
  UsersRound,
  Wand2,
  Waypoints,
  X,
  XCircle,
} from "lucide-react";
import { newsletterRequests } from "./newsletterApi";
import { newlyFinishedNewsletterJobIds } from "./newsletterJobResults";
import { NewsletterBulkJobs, NewsletterJobResultsDialog } from "./NewsletterBulkJobs";
import type {
  AccountGroupOption,
  AuditEvent,
  Newsletter,
  NewsletterDiscoveryResult,
  NewsletterHealth,
  NewsletterAccountOption,
  NewsletterAgentSettings,
  NewsletterGroup,
  NewsletterJob,
  NewsletterJobAccountTarget,
  NewsletterPageProps,
  NewsletterProfile,
  NewsletterSkill,
  NewsletterSubscription,
  NewsletterSignupTest,
  NewsletterConfirmationTest,
} from "./newsletterTypes";

type Tab = "overview" | "registry" | "groups" | "automation" | "jobs" | "subscriptions" | "activity";
type Modal = "group" | "newsletter" | "job" | "confirm" | "profile" | "detail" | "discovery" | "signup" | "confirmation" | null;
type ConfirmState = { title: string; description: string; action: string; onConfirm: () => void };
type OptimisticBulkRun = { jobIds: string[]; concurrency: number };

const tabs: { id: Tab; label: string; icon: typeof Newspaper }[] = [
  { id: "overview", label: "Overview", icon: BarChart3 },
  { id: "groups", label: "Groups", icon: UsersRound },
  { id: "registry", label: "Newsletters", icon: Newspaper },
  { id: "jobs", label: "Bulk subscribe", icon: FileCog },
];

const secondaryTabs: { id: Tab; label: string }[] = [
  { id: "automation", label: "Website profiles" },
  { id: "subscriptions", label: "Subscription records" },
  { id: "activity", label: "Activity log" },
];

const newsletterStatuses = ["ALL", "ACTIVE", "TEST_REQUIRED", "AUTOMATION_BROKEN", "DISABLED"];
const discoveryStatuses = ["ALL", "NOT_DISCOVERED", "DISCOVERING", "DISCOVERED", "DISCOVERY_BLOCKED", "FAILED"];
const subscriptionStates = ["ALL", "PENDING", "SUBMITTED", "WAITING_CONFIRMATION", "CONFIRMED", "SKIPPED", "FAILED", "MANUAL_REVIEW"];

const labelStatus = (value: string | null | undefined) => value ? value.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()) : "Not set";
const formatDateTime = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value)) : "—";
const formatDate = (value: string | null | undefined) => value ? new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", year: "numeric" }).format(new Date(value)) : "—";
const statusTone = (value: string) => value === "ACTIVE" || value === "CONFIRMED" || value === "TEST_PASSED" ? "ok" : ["FAILED", "DISCOVERY_FAILED", "DISCOVERY_BLOCKED", "AUTOMATION_BROKEN", "DISABLED", "REJECTED"].includes(value) ? "danger" : "warning";

// Mirrors the backend's signable-status set so the dialog and the API always
// agree on which profile a run will use. Discovery auto-creates a CANDIDATE
// profile, so a discovered newsletter is runnable without the adaptive agent;
// ordering is most-approved first, then newest version.
const signableProfileStatuses = ["ACTIVE", "TEST_PASSED", "CANDIDATE", "TEST_FAILED"];
const bestProfileForNewsletter = (profiles: NewsletterProfile[], newsletterId: string | undefined): NewsletterProfile | null => {
  if (!newsletterId) return null;
  return profiles
    .filter((item) => item.newsletter_id === newsletterId && signableProfileStatuses.includes(item.status))
    .sort((a, b) => signableProfileStatuses.indexOf(a.status) - signableProfileStatuses.indexOf(b.status) || b.version - a.version)[0] ?? null;
};

export function NewsletterPage({ accounts, accountGroups, canManage, canOperate, refreshKey, onNotify, onOpenSettings, initialAccountIds = [], onClearInitialAccountIds }: NewsletterPageProps) {
  const [tab, setTab] = useState<Tab>("overview");
  const [groups, setGroups] = useState<NewsletterGroup[]>([]);
  const [newsletters, setNewsletters] = useState<Newsletter[]>([]);
  const [profiles, setProfiles] = useState<NewsletterProfile[]>([]);
  const [agentSettings, setAgentSettings] = useState<NewsletterAgentSettings | null>(null);
  const [subscriptions, setSubscriptions] = useState<NewsletterSubscription[]>([]);
  const [jobs, setJobs] = useState<NewsletterJob[]>([]);
  const [jobResultQueue, setJobResultQueue] = useState<string[]>([]);
  const [jobResult, setJobResult] = useState<NewsletterJob | null>(null);
  const [jobResultLoading, setJobResultLoading] = useState(false);
  const [jobResultError, setJobResultError] = useState("");
  const [jobResultLoadAttempt, setJobResultLoadAttempt] = useState(0);
  const previousJobStatuses = useRef<Map<string, string> | null>(null);
  const [activity, setActivity] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [modal, setModal] = useState<Modal>(null);
  const [confirm, setConfirm] = useState<ConfirmState | null>(null);
  const [editingGroup, setEditingGroup] = useState<NewsletterGroup | null>(null);
  const [editingNewsletter, setEditingNewsletter] = useState<Newsletter | null>(null);
  const [detailNewsletter, setDetailNewsletter] = useState<Newsletter | null>(null);
  const [detailHealth, setDetailHealth] = useState<NewsletterHealth | null>(null);
  const [selectedProfile, setSelectedProfile] = useState<NewsletterProfile | null>(null);
  const [selectedNewsletterId, setSelectedNewsletterId] = useState("");
  const [discoveryNewsletter, setDiscoveryNewsletter] = useState<Newsletter | null>(null);
  const [discoveryResult, setDiscoveryResult] = useState<NewsletterDiscoveryResult | null>(null);
  const [discoveryPending, setDiscoveryPending] = useState(false);
  const [discoverAllPending, setDiscoverAllPending] = useState(false);
  const [discoveryError, setDiscoveryError] = useState("");
  const [signupProfile, setSignupProfile] = useState<NewsletterProfile | null>(null);
  const [signupNewsletter, setSignupNewsletter] = useState<Newsletter | null>(null);
  const [signupProfileLoading, setSignupProfileLoading] = useState(false);
  const [signupDiscovering, setSignupDiscovering] = useState(false);
  const [signupPending, setSignupPending] = useState(false);
  const [signupError, setSignupError] = useState("");
  const [signupResult, setSignupResult] = useState<NewsletterSignupTest | null>(null);
  const [confirmationSubscription, setConfirmationSubscription] = useState<NewsletterSubscription | null>(null);
  const [confirmationProfile, setConfirmationProfile] = useState<NewsletterProfile | null>(null);
  const [confirmationPending, setConfirmationPending] = useState(false);
  const [confirmationError, setConfirmationError] = useState("");
  const [confirmationResult, setConfirmationResult] = useState<NewsletterConfirmationTest | null>(null);
  const [registryGroupId, setRegistryGroupId] = useState("ALL");
  const [revision, setRevision] = useState(0);
  const [optimisticBulkRun, setOptimisticBulkRun] = useState<OptimisticBulkRun | null>(null);
  const [launchTargetAccounts, setLaunchTargetAccounts] = useState<NewsletterJobAccountTarget[]>([]);
  const [launchTargetsLoading, setLaunchTargetsLoading] = useState(false);
  const launchTargetKey = initialAccountIds.join(",");
  const moreToolsRef = useRef<HTMLDetailsElement | null>(null);

  useEffect(() => {
    if (!launchTargetKey) return;
    let current = true;
    const ids = launchTargetKey.split(",").filter(Boolean);
    setLaunchTargetsLoading(true);
    setLaunchTargetAccounts([]);
    void newsletterRequests.getJobAccountTargets(ids).then((targets) => {
      if (!current) return;
      setLaunchTargetAccounts(targets);
      setModal("job");
    }).catch((requestError) => {
      if (!current) return;
      onNotify("error", (requestError as Error).message || "Could not load the selected accounts for newsletter subscription.");
      onClearInitialAccountIds?.();
    }).finally(() => { if (current) setLaunchTargetsLoading(false); });
    return () => { current = false; };
  }, [launchTargetKey]);

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true); else setLoading(true);
    setError("");
    try {
      const [groupData, newsletterData, profileData, subscriptionData, jobData, auditData, agentSettingsData] = await Promise.all([
        newsletterRequests.listGroups(),
        newsletterRequests.listNewsletters(),
        newsletterRequests.listProfiles(),
        newsletterRequests.listSubscriptions(),
        newsletterRequests.listJobs(),
        newsletterRequests.listActivity(),
        newsletterRequests.getNewsletterAgentSettings().catch(() => null),
      ]);
      setGroups(groupData);
      setNewsletters(newsletterData);
      setProfiles(profileData);
      setSubscriptions(subscriptionData.items);
      setJobs(jobData.items);
      setActivity(auditData.filter((event) => event.action.startsWith("newsletter.") || event.action.startsWith("newsletter_")));
      setAgentSettings(agentSettingsData);
    } catch (requestError) {
      setError((requestError as Error).message || "Newsletter data is unavailable.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { document.title = "Newsletters — Gmail Operations"; void load(); }, [load, revision, refreshKey]);

  // A completed bulk run opens its report once when the persisted job changes
  // from active to terminal. The same report remains available from each row.
  useEffect(() => {
    if (!previousJobStatuses.current && jobs.length === 0) return;
    let previous = previousJobStatuses.current;
    if (!previous) {
      try {
        const saved = JSON.parse(window.sessionStorage.getItem("newsletter-job-statuses") ?? "null") as [string, string][] | null;
        if (Array.isArray(saved)) previous = new Map(saved.filter((entry): entry is [string, string] => Array.isArray(entry) && entry.length === 2 && typeof entry[0] === "string" && typeof entry[1] === "string"));
      } catch { /* An unavailable or stale browser snapshot should not block the page. */ }
    }
    const finishedIds = previous ? newlyFinishedNewsletterJobIds(jobs, previous) : [];
    if (finishedIds.length) setJobResultQueue((current) => [...current, ...finishedIds.filter((id) => !current.includes(id))]);
    const nextStatuses = new Map(jobs.map((job) => [job.id, job.status]));
    previousJobStatuses.current = nextStatuses;
    try { window.sessionStorage.setItem("newsletter-job-statuses", JSON.stringify(Array.from(nextStatuses.entries()))); } catch { /* Session storage is only a navigation-survival aid. */ }
  }, [jobs]);

  const resultJobId = jobResultQueue[0] ?? null;
  useEffect(() => {
    if (!resultJobId) { setJobResult(null); setJobResultError(""); setJobResultLoading(false); return; }
    let current = true;
    setJobResult(null);
    setJobResultError("");
    setJobResultLoading(true);
    void newsletterRequests.getJob(resultJobId).then((result) => { if (current) setJobResult(result); }).catch((requestError) => { if (current) setJobResultError((requestError as Error).message || "Could not load this operation report."); }).finally(() => { if (current) setJobResultLoading(false); });
    return () => { current = false; };
  }, [resultJobId, jobResultLoadAttempt]);

  const openJobResults = (jobId: string) => setJobResultQueue((current) => [jobId, ...current.filter((id) => id !== jobId)]);
  const closeJobResults = () => setJobResultQueue((current) => current.slice(1));
  const closeJobConfig = () => { setModal(null); setLaunchTargetAccounts([]); onClearInitialAccountIds?.(); };
  const jobConfigSaved = (message: string, startedJobIds?: string[], concurrency?: number) => {
    if (startedJobIds?.length && concurrency) setOptimisticBulkRun({ jobIds: startedJobIds, concurrency });
    setLaunchTargetAccounts([]);
    onClearInitialAccountIds?.();
    setTab("jobs");
    afterMutation(message);
  };

  // The run state is persisted by the backend, so this poll keeps the banner
  // current while the operator is on any newsletter tab. When the page is
  // left and opened again, the initial load reconstructs the same indicator.
  const runningJobKey = useMemo(() => jobs.filter((job) => job.status === "RUNNING").map((job) => job.id).sort().join("|"), [jobs]);
  useEffect(() => {
    if (!runningJobKey) return;
    let current = true;
    let requestPending = false;
    const refreshJobStatus = async () => {
      if (requestPending) return;
      requestPending = true;
      try {
        const result = await newsletterRequests.listJobs();
        if (current) setJobs(result.items);
      } catch {
        // A transient status poll failure should not blank the page or replace
        // its full-load error state; the next poll or manual refresh can retry.
      } finally {
        requestPending = false;
      }
    };
    const timer = window.setInterval(() => { void refreshJobStatus(); }, 4000);
    return () => { current = false; window.clearInterval(timer); };
  }, [runningJobKey]);

  useEffect(() => {
    if (!optimisticBulkRun) return;
    const knownJobIds = new Set(jobs.map((job) => job.id));
    if (optimisticBulkRun.jobIds.every((id) => knownJobIds.has(id))) setOptimisticBulkRun(null);
  }, [jobs, optimisticBulkRun]);

  const switchTab = (next: Tab) => setTab(next);
  const refresh = () => setRevision((value) => value + 1);
  const afterMutation = (message: string) => { setModal(null); setEditingGroup(null); setEditingNewsletter(null); onNotify("success", message); refresh(); };
  const openGroupForm = (group?: NewsletterGroup) => { setEditingGroup(group ?? null); setModal("group"); };
  const openNewsletterForm = (newsletter?: Newsletter) => { setEditingNewsletter(newsletter ?? null); setModal("newsletter"); };
  const setGroupStatus = (group: NewsletterGroup, next: "enable" | "disable") => setConfirm({ title: `${next === "disable" ? "Disable" : "Enable"} ${group.name}?`, description: next === "disable" ? "The group will remain available for audit, but it will no longer be active in the registry." : "The group will become available for registry use again.", action: next === "disable" ? "Disable group" : "Enable group", onConfirm: async () => { try { await newsletterRequests.setGroupStatus(group.id, next); afterMutation(`Newsletter group ${next === "disable" ? "disabled" : "enabled"}.`); } catch (requestError) { onNotify("error", (requestError as Error).message); } finally { setConfirm(null); } } });
  const seedDirectory = async () => { try { const result = await newsletterRequests.seedDirectory(); onNotify("success", `${result.created_count} newsletter topic groups created; ${result.existing_count} already existed.`); refresh(); } catch (requestError) { onNotify("error", (requestError as Error).message || "The newsletter topic directory could not be seeded."); } };
  const importCatalog = async () => { try { const result = await newsletterRequests.importCatalog(); onNotify("success", `${result.created_count} newsletter sources imported into ${result.created_groups || 5} topic groups${result.skipped_count ? `; ${result.skipped_count} unusable entries skipped` : ""}. They require discovery before signup.`); refresh(); } catch (requestError) { onNotify("error", (requestError as Error).message || "The supplied newsletter catalog could not be imported."); } };
  const setNewsletterStatus = (newsletter: Newsletter, next: "enable" | "disable") => setConfirm({ title: `${next === "disable" ? "Disable" : "Enable"} ${newsletter.name}?`, description: next === "disable" ? "The registry record and historical subscriptions remain intact. Future execution is not part of this phase." : "The newsletter will become active in the registry again.", action: next === "disable" ? "Disable newsletter" : "Enable newsletter", onConfirm: async () => { try { await newsletterRequests.setNewsletterStatus(newsletter.id, next); afterMutation(`Newsletter ${next === "disable" ? "disabled" : "enabled"}.`); } catch (requestError) { onNotify("error", (requestError as Error).message); } finally { setConfirm(null); } } });
  const openProfiles = (newsletterId: string) => { setSelectedNewsletterId(newsletterId); setTab("automation"); };
  const openNewsletterDetail = async (newsletter: Newsletter) => {
    setDetailNewsletter(newsletter);
    setDetailHealth(null);
    setModal("detail");
    try { setDetailHealth(await newsletterRequests.getNewsletterHealth(newsletter.id)); } catch (requestError) { onNotify("error", (requestError as Error).message || "Newsletter health is unavailable."); }
  };
  const openProfile = async (profile: NewsletterProfile) => { try { setSelectedProfile(await newsletterRequests.getProfile(profile.id)); setModal("profile"); } catch (requestError) { onNotify("error", (requestError as Error).message); } };
  const openDiscovery = async (newsletter: Newsletter) => {
    setDiscoveryNewsletter(newsletter);
    setDiscoveryResult(null);
    setDiscoveryError("");
    setDiscoveryPending(true);
    setModal("discovery");
    try {
      setDiscoveryResult(await newsletterRequests.discoverNewsletter(newsletter.id));
      refresh();
    } catch (requestError) {
      setDiscoveryError((requestError as Error).message || "The public page could not be inspected.");
    } finally {
      setDiscoveryPending(false);
    }
  };
  const discoverAll = async () => {
    if (!newsletters.length) {
      onNotify("error", "There are no configured newsletter websites to discover.");
      return;
    }
    setDiscoverAllPending(true);
    try {
      const result = await newsletterRequests.discoverAllNewsletters();
      const attentionCount = result.failed_count + result.blocked_count + result.skipped_count;
      onNotify(
        attentionCount ? "error" : "success",
        `Website discovery finished: ${result.discovered_count} discovered, ${result.blocked_count} blocked, ${result.failed_count} failed${result.skipped_count ? `, ${result.skipped_count} skipped` : ""}.`,
      );
      refresh();
    } catch (requestError) {
      onNotify("error", (requestError as Error).message || "All-website discovery could not be completed.");
    } finally {
      setDiscoverAllPending(false);
    }
  };
  const createCandidateProfile = async (candidate: NewsletterDiscoveryResult["candidates"][number]) => {
    if (!discoveryNewsletter) return;
    try {
      await newsletterRequests.createProfile({ newsletter_id: discoveryNewsletter.id, status: "CANDIDATE", confidence: candidate.confidence, configuration: candidate.configuration });
      onNotify("success", "Candidate profile created. It is not active and still requires validation.");
      setModal(null);
      setDiscoveryNewsletter(null);
      setDiscoveryResult(null);
      refresh();
    } catch (requestError) {
      setDiscoveryError((requestError as Error).message || "The candidate profile could not be created.");
    }
  };
  const rediscoverNewsletter = async (newsletter: Newsletter) => {
    try {
      const result = await newsletterRequests.rediscoverNewsletter(newsletter.id);
      onNotify("success", result.candidate_profile ? `Safe candidate profile v${result.candidate_profile.version} created. Test it before promotion.` : "Rediscovery completed, but no safe candidate profile was found.");
      setModal(null);
      setDetailNewsletter(null);
      setDetailHealth(null);
      refresh();
    } catch (requestError) { onNotify("error", (requestError as Error).message || "Newsletter rediscovery failed."); }
  };
  const openSignupTest = async (profile: NewsletterProfile) => {
    setSignupResult(null); setSignupError(""); setSignupNewsletter(newsletters.find((item) => item.id === profile.newsletter_id) ?? null); setSignupProfile(profile); setSignupProfileLoading(true); setSignupDiscovering(false); setModal("signup");
    try { setSignupProfile(await newsletterRequests.getProfile(profile.id)); }
    catch (requestError) { setSignupError((requestError as Error).message || "The saved profile could not be loaded."); }
    finally { setSignupProfileLoading(false); }
  };
  const openAdaptiveSignup = (newsletter: Newsletter) => {
    // Reuse the profile discovery already created for this newsletter rather
    // than starting an adaptive run. Opening with no profile forced the AI
    // agent and its workspace gates into the path of a form the deterministic
    // resolver can already drive. Only a newsletter with nothing discovered
    // falls through to the learning route.
    const discovered = bestProfileForNewsletter(profiles, newsletter.id);
    setSignupResult(null); setSignupError(""); setSignupProfile(discovered); setSignupNewsletter(newsletter); setSignupProfileLoading(false); setSignupDiscovering(false); setModal("signup");
  };
  // Plain discovery inspects the page but deliberately persists nothing; only the
  // rediscovery route writes a candidate. The signup dialog needs a profile to
  // run at all, so this inspects the page and adopts the result in one step
  // rather than sending the operator to a second dialog to press another button.
  const discoverProfileForSignup = async () => {
    const newsletter = signupNewsletter;
    if (!newsletter || signupDiscovering) return;
    setSignupDiscovering(true);
    setSignupError("");
    try {
      const result = await newsletterRequests.discoverNewsletter(newsletter.id);
      const candidates = result.candidates ?? [];
      const complete = candidates.find((item) => item.configuration?.email_selector && item.configuration?.submit_selector);
      if (!complete) {
        if (result.discovery_blocked) {
          setSignupError(`The public page could not be read (${result.blocked_reasons.join(", ") || "blocked"}). Inspection only ever reads public pages, so a page that refuses automated reads needs a profile built by hand.`);
          return;
        }
        // Discovery keeps a candidate when *either* half of the form was found,
        // so a half-read page lands here. Naming the missing half is the
        // difference between an unreachable form and a selector gap.
        const closest = candidates[0];
        const detail = closest
          ? `Closest read was ${closest.url}: ${closest.configuration?.email_selector ? "email field found" : "no email field"}, ${closest.configuration?.submit_selector ? "submit control found" : "no submit control"}.`
          : "No signup form was found on any inspected page.";
        setSignupError(`No complete signup form was found, so no profile could be saved. Inspected ${result.inspected_url_count} page${result.inspected_url_count === 1 ? "" : "s"}. ${detail}${result.warnings.length ? ` ${result.warnings[0]}` : ""}`);
        return;
      }
      const created = await newsletterRequests.createProfile({ newsletter_id: newsletter.id, status: "CANDIDATE", confidence: complete.confidence, configuration: complete.configuration });
      // The create response is the detail shape, so it already carries the
      // configuration the dialog needs; no second read is required.
      setSignupProfile(created);
      onNotify("success", `Candidate profile v${created.version} created from the public page. Review the fields below, then start the signup.`);
      refresh();
    } catch (requestError) {
      setSignupError((requestError as Error).message || "The public signup page could not be inspected.");
    } finally {
      setSignupDiscovering(false);
    }
  };
  const runSignupTest = async (accountId: string, testIdentity: Record<string, string>, saveIdentity: boolean, signupPassword?: string) => {
    const newsletterId = signupProfile?.newsletter_id ?? signupNewsletter?.id;
    if (!newsletterId) return;
    setSignupPending(true); setSignupError("");
    try {
      if (saveIdentity) await newsletterRequests.saveAccountIdentity(accountId, testIdentity);
      const initial = await newsletterRequests.startSignupTest(newsletterId, { account_id: accountId, ...(signupProfile ? { profile_id: signupProfile.id } : {}), test_identity: testIdentity, ...(signupPassword ? { signup_password: signupPassword } : {}), auto_confirm: true });
      setSignupResult(initial);
      const startedAt = Date.now();
      const poll = async (): Promise<void> => {
        if (!initial.session?.id || Date.now() - startedAt > 90000) return;
        const next = await newsletterRequests.getSignupTest(initial.session.id);
        setSignupResult(next);
        const confirmationStillRunning = next.confirmation_required && next.auto_confirm && ["SUBMITTED", "WAITING_CONFIRMATION"].includes(next.result);
        if (next.result === "IN_PROGRESS" || confirmationStillRunning) { await new Promise((resolve) => window.setTimeout(resolve, 1500)); return poll(); }
      };
      await poll();
      refresh();
    } catch (requestError) { setSignupError((requestError as Error).message || "The signup test could not start."); }
    finally { setSignupPending(false); }
  };
  const openConfirmationTest = (subscription: NewsletterSubscription) => {
    const profile = profiles.filter((item) => item.newsletter_id === subscription.newsletter_id).sort((a, b) => Number(b.status === "ACTIVE") - Number(a.status === "ACTIVE") || b.version - a.version)[0];
    if (!profile) { onNotify("error", "Create or activate a newsletter automation profile before confirming."); return; }
    setConfirmationSubscription(subscription); setConfirmationProfile(profile); setConfirmationResult(null); setConfirmationError(""); setModal("confirmation");
  };
  const runConfirmationTest = async () => {
    if (!confirmationSubscription || !confirmationProfile) return;
    setConfirmationPending(true); setConfirmationError("");
    try {
      const initial = await newsletterRequests.startConfirmationTest(confirmationSubscription.newsletter_id, { account_id: confirmationSubscription.account_id, profile_id: confirmationProfile.id, subscription_id: confirmationSubscription.id });
      setConfirmationResult(initial);
      const startedAt = Date.now();
      const poll = async (): Promise<void> => {
        if (!initial.session?.id || Date.now() - startedAt > 90000) return;
        const next = await newsletterRequests.getConfirmationTest(initial.session.id);
        setConfirmationResult(next);
        if (["IN_PROGRESS", "WAITING_CONFIRMATION"].includes(next.result)) { await new Promise((resolve) => window.setTimeout(resolve, 1500)); return poll(); }
      };
      await poll(); refresh();
    } catch (requestError) { setConfirmationError((requestError as Error).message || "The confirmation test could not start."); }
    finally { setConfirmationPending(false); }
  };
  const updateProfile = (profile: NewsletterProfile, action: "promote" | "reject" | "rollback") => setConfirm({ title: `${labelStatus(action)} profile v${profile.version}?`, description: action === "promote" ? "This profile becomes the active version for future newsletter work. The previous active version remains auditable." : action === "rollback" ? "This version becomes active again. No browser automation is started by this action." : "The candidate will be marked rejected and retained for audit.", action: labelStatus(action), onConfirm: async () => { try { const updated = action === "promote" ? await newsletterRequests.promoteProfile(profile.id) : action === "reject" ? await newsletterRequests.rejectProfile(profile.id) : await newsletterRequests.rollbackProfile(profile.id); setSelectedProfile(updated); onNotify("success", `Profile v${profile.version} ${action === "promote" ? "promoted" : action === "rollback" ? "rolled back" : "rejected"}.`); refresh(); } catch (requestError) { onNotify("error", (requestError as Error).message); } finally { setConfirm(null); } } });

  const saveEntryStep = async (profile: NewsletterProfile, entrySelector: string): Promise<void> => {
    const updated = await newsletterRequests.updateProfile(profile.id, { ...(profile.configuration ?? {}), entry_selector: entrySelector });
    setSelectedProfile(updated);
    onNotify("success", `Candidate version v${updated.version} saved. Run a signup test against it.`);
    refresh();
  };

  const metrics = useMemo(() => ({
    total: newsletters.length,
    active: newsletters.filter((item) => item.status === "ACTIVE").length,
    testRequired: newsletters.filter((item) => item.status === "TEST_REQUIRED").length,
    broken: newsletters.filter((item) => item.status === "AUTOMATION_BROKEN").length,
    disabled: newsletters.filter((item) => item.status === "DISABLED").length,
    activeProfiles: profiles.filter((item) => item.status === "ACTIVE").length,
    subscriptions: subscriptions.length,
  }), [newsletters, profiles, subscriptions]);
  const activeJobs = useMemo(() => jobs.filter((job) => ["RUNNING", "PAUSED"].includes(job.status)), [jobs]);
  const runningJobs = useMemo(() => jobs.filter((job) => job.status === "RUNNING"), [jobs]);
  const pausedJobs = useMemo(() => jobs.filter((job) => job.status === "PAUSED"), [jobs]);
  const optimisticRunningCount = optimisticBulkRun?.jobIds.filter((id) => !jobs.some((job) => job.id === id)).length ?? 0;
  const runningJobCount = runningJobs.length + optimisticRunningCount;
  const activeConcurrency = useMemo(() => {
    const caps = new Map<string, number>();
    runningJobs.forEach((job) => {
      const scope = typeof job.retry_policy_json?.bulk_run_id === "string" ? job.retry_policy_json.bulk_run_id : job.id;
      caps.set(scope, Math.max(caps.get(scope) ?? 0, job.concurrency_limit));
    });
    return [...caps.values()].reduce((total, cap) => total + cap, 0);
  }, [runningJobs]);
  const visibleActiveJobIds = useMemo(() => [...new Set([...activeJobs.map((job) => job.id), ...(optimisticBulkRun?.jobIds ?? [])])], [activeJobs, optimisticBulkRun]);
  const visibleActiveCount = visibleActiveJobIds.length;
  const visibleActiveConcurrency = useMemo(() => {
    if (!optimisticBulkRun) return activeConcurrency;
    const actualIds = new Set(activeJobs.map((job) => job.id));
    return activeConcurrency + (optimisticBulkRun.jobIds.some((id) => !actualIds.has(id)) ? optimisticBulkRun.concurrency : 0);
  }, [activeConcurrency, activeJobs, optimisticBulkRun]);
  const stopAllActiveRuns = () => {
    if (!visibleActiveJobIds.length) return;
    const jobIds = [...visibleActiveJobIds];
    setConfirm({
      title: "Stop all active newsletter subscriptions?",
      description: `This will stop ${jobIds.length} active bulk job${jobIds.length === 1 ? "" : "s"}. Account sessions already completed remain recorded; running sessions will be asked to stop safely.`,
      action: "Stop all subscriptions",
      onConfirm: async () => {
        const results = await Promise.allSettled(jobIds.map((jobId) => newsletterRequests.cancelJob(jobId)));
        const failed = results.filter((result) => result.status === "rejected").length;
        setOptimisticBulkRun(null);
        setConfirm(null);
        onNotify(failed ? "error" : "success", failed ? `${failed} bulk job${failed === 1 ? "" : "s"} could not be stopped. Review Bulk subscribe.` : "All active newsletter subscription jobs were stopped.");
        refresh();
      },
    });
  };

  return <div className="newsletter-page workspace-page">
    <section className="page-heading newsletter-heading">
      <div><div className="eyebrow"><span className="eyebrow-line" /> NEWSLETTER WORKSPACE</div><h1>Newsletters</h1><p>Organize websites, choose account groups, and track subscription runs in one place.</p></div>
      <div className="heading-actions"><button type="button" className="button secondary" onClick={() => void load(true)} disabled={loading || refreshing}><RefreshCw size={16} className={refreshing ? "spin-icon" : ""} /> Sync data</button>{canOperate && <button type="button" className="button primary" onClick={() => setModal("job")}><FileCog size={16} /> Start bulk subscribe</button>}{canManage && <button type="button" className="button secondary" onClick={() => openNewsletterForm()}><Plus size={16} /> Add newsletter</button>}</div>
    </section>
    {visibleActiveCount > 0 && <section className="newsletter-live-run" role="status" aria-live="polite"><span className="newsletter-live-run-spinner">{runningJobCount > 0 && <span className="spinner" />}</span><div><strong>{runningJobCount} run{runningJobCount === 1 ? " is" : "s are"} running{pausedJobs.length > 0 ? ` · ${pausedJobs.length} paused` : ""}</strong><span>{visibleActiveConcurrency} configured concurrent account slot{visibleActiveConcurrency === 1 ? "" : "s"} currently available to running jobs · {[...runningJobs, ...pausedJobs].map((job) => `${job.newsletter_name ?? job.newsletter_group_name ?? "Newsletter group"} · ${job.account_group_name} (${job.status.toLowerCase()})`).join("  •  ") || "Starting the selected newsletter subscriptions…"}</span></div><button type="button" className="button secondary small" onClick={() => setTab("jobs")}><FileCog size={14} /> View bulk controls</button>{canOperate && <button type="button" className="button danger small" onClick={stopAllActiveRuns}><Square size={14} /> Stop all</button>}</section>}
    {launchTargetsLoading && <div className="newsletter-live-run" role="status"><span className="newsletter-live-run-spinner"><span className="spinner" /></span><div><strong>Loading selected accounts</strong><span>Checking access and preparing the newsletter configuration.</span></div></div>}
    {error && <div className="inline-error newsletter-error" role="alert"><AlertCircle size={17} /><span>{error}</span><button type="button" className="text-button" onClick={() => void load()}>Retry</button></div>}
    <nav className="newsletter-navigation" aria-label="Newsletter workspace sections">
      <div className="newsletter-tabs">{tabs.map(({ id, label, icon: Icon }) => <button key={id} type="button" aria-current={tab === id ? "page" : undefined} className={tab === id ? "selected" : ""} onClick={() => switchTab(id)}><Icon size={15} /><span>{label}</span></button>)}</div>
      <details className="newsletter-more-tools" ref={moreToolsRef}>
        <summary aria-label={secondaryTabs.find((item) => item.id === tab) ? `More tools, current view: ${secondaryTabs.find((item) => item.id === tab)?.label}` : "More newsletter tools"}><span>More tools</span>{secondaryTabs.find((item) => item.id === tab) && <small>{secondaryTabs.find((item) => item.id === tab)?.label}</small>}<ChevronDown size={14} /></summary>
        <div className="newsletter-tools-menu" aria-label="Additional newsletter views">{secondaryTabs.map(({ id, label }) => <button key={id} type="button" aria-current={tab === id ? "page" : undefined} className={tab === id ? "selected" : ""} onClick={() => { switchTab(id); if (moreToolsRef.current) moreToolsRef.current.open = false; }}>{label}</button>)}</div>
      </details>
    </nav>
    <div id={`newsletter-panel-${tab}`} className="newsletter-tab-panel">
      {loading ? <LoadingState /> : tab === "overview" ? <NewsletterOverview metrics={metrics} groups={groups} newsletters={newsletters} jobs={jobs} onOpenRegistry={() => switchTab("registry")} onOpenProfiles={openProfiles} onOpenJobs={() => switchTab("jobs")} /> : tab === "registry" ? <NewsletterRegistry newsletters={newsletters} groups={groups} initialGroupId={registryGroupId} canManage={canManage} canOperate={canOperate} onAdd={() => openNewsletterForm()} onEdit={openNewsletterForm} onView={openNewsletterDetail} onDiscover={openDiscovery} onDiscoverAll={() => void discoverAll()} discoverAllPending={discoverAllPending} onStatus={setNewsletterStatus} onProfiles={openProfiles} onStartLearning={openAdaptiveSignup} /> : tab === "groups" ? <NewsletterGroups groups={groups} newsletters={newsletters} canManage={canManage} canOperate={canOperate} onAdd={() => openGroupForm()} onEdit={openGroupForm} onStatus={setGroupStatus} onSeedDirectory={seedDirectory} onImportCatalog={importCatalog} onStartBulk={() => setModal("job")} onOpenNewsletters={(groupId) => { setRegistryGroupId(groupId); setTab("registry"); }} /> : tab === "automation" ? <NewsletterAutomation newsletters={newsletters} profiles={profiles} selectedNewsletterId={selectedNewsletterId} canManage={canManage} canOperate={canOperate} onSelect={setSelectedNewsletterId} onView={openProfile} onAction={updateProfile} onStartSignup={openSignupTest} /> : tab === "jobs" ? <NewsletterBulkJobs jobs={jobs} newsletters={newsletters} newsletterGroups={groups} accountGroups={accountGroups} canOperate={canOperate} onCreate={() => setModal("job")} onRefresh={() => void load(true)} onStopAll={stopAllActiveRuns} onViewResults={openJobResults} /> : tab === "subscriptions" ? <NewsletterSubscriptions subscriptions={subscriptions} newsletters={newsletters} accountGroups={accountGroups} canOperate={canOperate} profiles={profiles} onStartConfirmation={openConfirmationTest} /> : <NewsletterActivity activity={activity} />}
    </div>
    <footer className="page-footer"><span>Northstar Operations</span><span>Newsletter control plane · governed execution</span><span>Unsafe or unverified actions stop for manual review</span></footer>
    {modal === "group" && <GroupForm group={editingGroup} onClose={() => { setModal(null); setEditingGroup(null); }} onSaved={afterMutation} />}
    {modal === "newsletter" && <NewsletterForm newsletter={editingNewsletter} groups={groups} onClose={() => { setModal(null); setEditingNewsletter(null); }} onSaved={afterMutation} />}
    {modal === "job" && <JobForm newsletters={newsletters} groups={groups} accountGroups={accountGroups} presetAccounts={launchTargetAccounts} onClose={closeJobConfig} onSaved={jobConfigSaved} />}
    {modal === "profile" && selectedProfile && <ProfileDetail profile={selectedProfile} newsletter={newsletters.find((item) => item.id === selectedProfile.newsletter_id)} canManage={canManage} onClose={() => { setModal(null); setSelectedProfile(null); }} onAction={updateProfile} onSaveEntryStep={saveEntryStep} />}
    {modal === "detail" && detailNewsletter && <NewsletterDetail newsletter={detailNewsletter} health={detailHealth} onClose={() => { setModal(null); setDetailNewsletter(null); setDetailHealth(null); }} onEdit={() => { setModal(null); openNewsletterForm(detailNewsletter); }} onProfiles={() => { setModal(null); openProfiles(detailNewsletter.id); }} onDiscover={() => { setModal(null); void openDiscovery(detailNewsletter); }} onRediscover={() => void rediscoverNewsletter(detailNewsletter)} onStartLearning={() => { setModal(null); openAdaptiveSignup(detailNewsletter); }} canManage={canManage} />}
    {modal === "discovery" && discoveryNewsletter && <DiscoveryDialog newsletter={discoveryNewsletter} result={discoveryResult} pending={discoveryPending} error={discoveryError} canManage={canManage} onClose={() => { setModal(null); setDiscoveryNewsletter(null); setDiscoveryResult(null); setDiscoveryError(""); }} onRetry={() => void openDiscovery(discoveryNewsletter)} onCreateProfile={createCandidateProfile} />}
    {modal === "signup" && (signupProfile || signupNewsletter) && <SignupTestDialog profile={signupProfile} newsletter={signupNewsletter ?? undefined} accounts={accounts} agentReady={Boolean(agentSettings?.newsletter_agent_enabled && (agentSettings.openrouter_api_key_configured || agentSettings.openai_api_key_configured) && agentSettings.newsletter_agent_live_execution_enabled && agentSettings.newsletter_agent_learning_mode && !agentSettings.newsletter_agent_shadow_mode && agentSettings.newsletter_agent_chain.some((entry) => entry.enabled))} agentReadinessMessage={!agentSettings ? "Agent readiness could not be read from Settings." : !(agentSettings.openrouter_api_key_configured || agentSettings.openai_api_key_configured) ? "Enter an OpenRouter API key in Settings, then enable live learning." : !agentSettings.newsletter_agent_enabled ? "Newsletter Agent is disabled in Settings." : !agentSettings.newsletter_agent_chain.some((entry) => entry.enabled) ? "Enable at least one agent in the ordered chain." : agentSettings.newsletter_agent_shadow_mode ? "Shadow mode is enabled, so browser actions are blocked." : !agentSettings.newsletter_agent_live_execution_enabled ? "Live execution is disabled." : !agentSettings.newsletter_agent_learning_mode ? "Learning mode is disabled." : "Agent learning is ready."} onOpenSettings={onOpenSettings} profileLoading={signupProfileLoading} pending={signupPending} error={signupError} result={signupResult} onClose={() => { if (!signupPending && !signupProfileLoading) { setModal(null); setSignupProfile(null); setSignupNewsletter(null); setSignupResult(null); } }} onStart={runSignupTest} discovering={signupDiscovering} canManage={canManage} onDiscover={() => void discoverProfileForSignup()} />}
    {modal === "confirmation" && confirmationSubscription && confirmationProfile && <ConfirmationTestDialog subscription={confirmationSubscription} profile={confirmationProfile} newsletter={newsletters.find((item) => item.id === confirmationSubscription.newsletter_id)} pending={confirmationPending} error={confirmationError} result={confirmationResult} onClose={() => { if (!confirmationPending) { setModal(null); setConfirmationSubscription(null); setConfirmationResult(null); } }} onStart={runConfirmationTest} />}
    {confirm && <ConfirmDialog state={confirm} onCancel={() => setConfirm(null)} />}
    {resultJobId && <NewsletterJobResultsDialog job={jobResult} loading={jobResultLoading} error={jobResultError} onRetry={() => setJobResultLoadAttempt((attempt) => attempt + 1)} onClose={closeJobResults} />}
  </div>;
}

function AdaptiveWorkflow({ newsletters, skills, agentSettings, canOperate, onOpenRegistry, onOpenAutomation, onOpenSettings, onStartLearning }: { newsletters: Newsletter[]; skills: Record<string, NewsletterSkill>; agentSettings: NewsletterAgentSettings | null; canOperate: boolean; onOpenRegistry: () => void; onOpenAutomation: () => void; onOpenSettings: () => void; onStartLearning: (newsletter: Newsletter) => void }) {
  const learned = newsletters.filter((item) => skills[item.id]?.status === "ACTIVE").length;
  const savedSkills = newsletters.filter((item) => skills[item.id]?.status === "ACTIVE");
  const next = newsletters.find((item) => (!skills[item.id] || ["NOT_LEARNED", "STALE", "INVALID", "MANUAL_REVIEW"].includes(skills[item.id].status)) && (item.signup_url || item.website_url));
  const providersReady = [agentSettings?.openrouter_api_key_configured, agentSettings?.openai_api_key_configured].filter(Boolean).length;
  const enabledAgents = agentSettings?.newsletter_agent_chain?.filter((entry) => entry.enabled) ?? [];
  const learningReady = Boolean(agentSettings?.newsletter_agent_enabled && providersReady > 0 && agentSettings.newsletter_agent_live_execution_enabled && agentSettings.newsletter_agent_learning_mode && !agentSettings.newsletter_agent_shadow_mode && enabledAgents.length);
  // Shadow mode ships enabled, so it is the default state rather than a fault an
  // operator caused. It only constrains the agent route; a run driven by a saved
  // form profile never calls the agent and is unaffected.
  const shadowModeOnly = Boolean(agentSettings?.newsletter_agent_enabled && providersReady > 0 && enabledAgents.length && agentSettings.newsletter_agent_live_execution_enabled && agentSettings.newsletter_agent_learning_mode && agentSettings.newsletter_agent_shadow_mode);
  const readinessMessage = !agentSettings
    ? "Agent readiness could not be read from Settings. Open Settings and refresh after saving the configuration."
    : !agentSettings.newsletter_agent_enabled
      ? "Newsletter Agent is disabled."
      : providersReady === 0
        ? "Configure an OpenRouter or OpenAI key in Settings."
        : enabledAgents.length === 0
          ? "Enable at least one agent in the ordered chain."
          : agentSettings.newsletter_agent_shadow_mode
            ? "Shadow mode is on, so the agent may plan but will not act in the browser. Signups that run from a saved form profile do not use the agent and are unaffected."
            : !agentSettings.newsletter_agent_live_execution_enabled
              ? "Live execution is disabled."
              : !agentSettings.newsletter_agent_learning_mode
                ? "Learning mode is disabled."
                : "Ready for a bounded agent-first learning run.";
  return <section className="newsletter-adaptive-workflow" aria-label="Adaptive newsletter workflow"><div className="newsletter-adaptive-header"><div><div className="eyebrow"><span className="eyebrow-line" /> ADAPTIVE WEBSITE SKILLS</div><h2>Learn once, replay safely</h2><p>Learning starts from the configured website or signup URL. A run needs one of two things: a saved form profile, which inspecting the public page creates, or a fully enabled agent that can resolve the live form itself.</p></div><div className="newsletter-adaptive-actions"><button type="button" className="button primary small" onClick={() => next && onStartLearning(next)} disabled={!next || !canOperate} title={!next ? "Configure a signup URL or website URL first" : !learningReady ? readinessMessage : undefined}>Start signup <ArrowRight size={14} /></button><button type="button" className="button secondary small" onClick={onOpenRegistry}>Open website registry <ArrowRight size={14} /></button><button type="button" className="button secondary small" onClick={() => document.getElementById("newsletter-saved-skills")?.scrollIntoView({ behavior: "smooth", block: "center" })}>Saved skills <Waypoints size={14} /></button></div></div><div className={`newsletter-agent-readiness ${learningReady ? "ready" : shadowModeOnly ? "optional" : "attention"}`}><span className="newsletter-agent-readiness-icon">{learningReady ? <Check size={16} /> : <AlertCircle size={16} />}</span><div><strong>{learningReady ? "Agent-first learning is ready" : shadowModeOnly ? "Agent is in shadow mode (plans only)" : "Adaptive signup needs setup"}</strong><span>{readinessMessage}</span></div>{!learningReady && <button type="button" className="text-button" onClick={onOpenSettings}>{providersReady ? "Open Settings" : "Enter OpenRouter API key"} <ArrowRight size={13} /></button>}</div><div className="newsletter-control-strip"><div className="newsletter-control-card"><div className="newsletter-control-card-top"><span className="detail-label">AGENT CONTROL PLANE</span><span className={`newsletter-control-state${learningReady ? " ready" : shadowModeOnly ? "" : " attention"}`}>{learningReady ? "READY" : shadowModeOnly ? "SHADOW" : "NOT SET UP"}</span></div><strong>{enabledAgents.length ? `${enabledAgents.length} agent${enabledAgents.length === 1 ? "" : "s"} in order` : "No agent selected"}</strong><span>{providersReady} provider{providersReady === 1 ? "" : "s"} configured · max {agentSettings?.newsletter_agent_chain?.length ?? 0} chain slots</span></div><div className="newsletter-control-card"><div className="newsletter-control-card-top"><span className="detail-label">EXECUTION POLICY</span><span className="newsletter-control-policy">SAFE BY DEFAULT</span></div><strong>Skill replay before AI</strong><span>Saved skill → deterministic controls → bounded agent → manual review.</span></div><div className="newsletter-control-card"><div className="newsletter-control-card-top"><span className="detail-label">DISCOVERY</span><span className="newsletter-control-policy">OPTIONAL</span></div><strong>Inspect to build a profile</strong><span>Inspecting the public page saves the email field and submit control as a candidate profile. Without that profile, signup needs the agent.</span></div></div><div className="newsletter-adaptive-steps"><div className="newsletter-adaptive-step"><span className="newsletter-adaptive-number">1</span><div><strong>Open URL</strong><small>No deterministic discovery gate</small></div></div><ArrowRight size={15} /><div className="newsletter-adaptive-step"><span className="newsletter-adaptive-number">2</span><div><strong>Learn</strong><small>Agent resolves and validates the live form</small></div></div><ArrowRight size={15} /><div className="newsletter-adaptive-step"><span className="newsletter-adaptive-number">3</span><div><strong>Replay</strong><small>{learned} saved website skill{learned === 1 ? "" : "s"} currently available</small></div></div><ArrowRight size={15} /><div className="newsletter-adaptive-step"><span className="newsletter-adaptive-number">4</span><div><strong>Review</strong><small>Changed or unsafe controls stop for manual review</small></div></div></div><div id="newsletter-saved-skills" className="newsletter-skill-inventory"><div className="newsletter-skill-inventory-heading"><div><strong>Saved website skills</strong><span>Redacted control recipes validated against the live DOM before replay.</span></div><button type="button" className="text-button" onClick={onOpenAutomation}>Open automation profiles <ArrowRight size={13} /></button></div>{savedSkills.length ? <div className="newsletter-skill-grid">{savedSkills.map((item) => { const skill = skills[item.id]; return <div className="newsletter-skill-card" key={item.id}><div className="newsletter-skill-card-top"><span className="newsletter-skill-status"><span /> ACTIVE</span><span>v{skill.version ?? "—"}</span></div><strong>{item.name}</strong><small>{skill.website_host || item.website_url || item.signup_url || "Configured website"}</small><div className="newsletter-skill-stats"><span><b>{skill.action_count}</b> actions</span><span><b>{skill.use_count}</b> replays</span><span><b>{skill.success_count}</b> successes</span></div></div>; })}</div> : <div className="newsletter-skill-empty"><Waypoints size={16} /><span>No active website skills yet. Complete one verified learning run to create the first redacted replay skill.</span></div>}</div>{next ? <div className="newsletter-adaptive-next"><span><strong>Ready to learn:</strong> {next.name}</span><button type="button" className="text-button" onClick={() => onStartLearning(next)} disabled={!canOperate}>Start signup <ArrowRight size={13} /></button></div> : <div className="newsletter-adaptive-next"><span><strong>No unlearned URL is ready.</strong> Configure a signup URL or replay an existing skill.</span></div>}</section>;
}

function LoadingState() { return <div className="newsletter-panel newsletter-loading" role="status"><span className="spinner" /><strong>Loading newsletter registry</strong><span>Fetching groups, profiles, subscriptions, jobs, and activity.</span></div>; }

function NewsletterOverview({ metrics, groups, newsletters, jobs, onOpenRegistry, onOpenProfiles, onOpenJobs }: { metrics: { total: number; testRequired: number; broken: number; subscriptions: number }; groups: NewsletterGroup[]; newsletters: Newsletter[]; jobs: NewsletterJob[]; onOpenRegistry: () => void; onOpenProfiles: (id: string) => void; onOpenJobs: () => void }) {
  const needsAttention = newsletters.filter((item) => item.status === "TEST_REQUIRED" || item.status === "AUTOMATION_BROKEN" || (!item.active_profile_version && item.status !== "DISABLED"));
  const recentJobs = [...jobs].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 5);
  return <div className="newsletter-overview">
    <div className="newsletter-metric-grid newsletter-summary-grid">
      <NewsletterMetric label="Newsletters" value={metrics.total} note="In your registry" icon={<Newspaper size={17} />} />
      <NewsletterMetric label="Groups" value={groups.length} note="Organized by topic" icon={<UsersRound size={17} />} />
      <NewsletterMetric label="Needs attention" value={needsAttention.length} note={`${metrics.broken} broken · ${metrics.testRequired} need testing`} icon={<AlertCircle size={17} />} tone={needsAttention.length ? "amber" : "teal"} />
      <NewsletterMetric label="Subscriptions" value={metrics.subscriptions} note="Recorded account results" icon={<Inbox size={17} />} />
    </div>
    <div className="newsletter-overview-grid">
      <section className="newsletter-panel">
        <div className="panel-header"><div><h2>Recent newsletters</h2><p>Websites saved in your registry.</p></div><button type="button" className="text-button" onClick={onOpenRegistry}>View all <ArrowRight size={13} /></button></div>
        {newsletters.length ? <div className="newsletter-list">{newsletters.slice(0, 5).map((item) => <button type="button" className="newsletter-list-row" key={item.id} onClick={onOpenRegistry}><span className="newsletter-row-icon"><Mail size={15} /></span><span><strong>{item.name}</strong><small>{item.group_name} · {labelStatus(item.discovery_status)}</small></span><StatusBadge value={item.registry_status} /></button>)}</div> : <EmptyState icon={<Newspaper size={20} />} title="Your registry is empty" copy="Add a newsletter website to start organizing your subscription list." />}
      </section>
      <section className="newsletter-panel">
        <div className="panel-header"><div><h2>Bulk subscribe runs</h2><p>Latest activity and run status.</p></div><button type="button" className="text-button" onClick={onOpenJobs}>Open bulk subscribe <ArrowRight size={13} /></button></div>
        {recentJobs.length ? <div className="newsletter-list">{recentJobs.map((job) => <button type="button" className="newsletter-list-row" key={job.id} onClick={onOpenJobs}><span className="newsletter-row-icon"><FileCog size={15} /></span><span><strong>{job.newsletter_name ?? job.newsletter_group_name ?? "Newsletter selection"}</strong><small>{job.account_group_name} · {formatDateTime(job.created_at)}</small></span><StatusBadge value={job.status} /></button>)}</div> : <EmptyState icon={<FileCog size={20} />} title="No subscription runs yet" copy="Choose your newsletter and account groups in Bulk subscribe when you’re ready." action={<button type="button" className="button primary small" onClick={onOpenJobs}>Open bulk subscribe <ArrowRight size={14} /></button>} />}
      </section>
      <section className="newsletter-panel newsletter-attention-panel">
        <div className="panel-header"><div><h2>Needs attention</h2><p>Websites needing a test or profile review.</p></div></div>
        {needsAttention.length ? <div className="newsletter-list">{needsAttention.slice(0, 5).map((item) => <button type="button" className="newsletter-list-row" key={item.id} onClick={() => onOpenProfiles(item.id)}><span className="newsletter-row-icon warning"><ListFilter size={15} /></span><span><strong>{item.name}</strong><small>{item.active_profile_version ? `Profile v${item.active_profile_version}` : "Profile not set up"} · {labelStatus(item.status)}</small></span><span className="newsletter-list-arrow"><ArrowRight size={15} /></span></button>)}</div> : <EmptyState icon={<ShieldCheck size={20} />} title="Nothing needs attention" copy="No active newsletter is currently flagged for profile review." />}
      </section>
    </div>
  </div>;
}

function NewsletterMetric({ label, value, note, icon, tone = "" }: { label: string; value: number; note: string; icon: React.ReactNode; tone?: string }) { return <div className={`newsletter-metric ${tone}`}><div className="newsletter-metric-top"><span>{label}</span><span className="newsletter-metric-icon">{icon}</span></div><strong>{value}</strong><small>{note}</small></div>; }

function NewsletterRegistry({ newsletters, groups, initialGroupId, canManage, canOperate, discoverAllPending, onDiscoverAll, onAdd, onEdit, onView, onDiscover, onStatus, onProfiles, onStartLearning }: { newsletters: Newsletter[]; groups: NewsletterGroup[]; initialGroupId: string; canManage: boolean; canOperate: boolean; discoverAllPending: boolean; onDiscoverAll: () => void; onAdd: () => void; onEdit: (newsletter: Newsletter) => void; onView: (newsletter: Newsletter) => void; onDiscover: (newsletter: Newsletter) => void; onStatus: (newsletter: Newsletter, next: "enable" | "disable") => void; onProfiles: (id: string) => void; onStartLearning: (newsletter: Newsletter) => void }) {
  const [query, setQuery] = useState(""); const [group, setGroup] = useState(initialGroupId); const [status, setStatus] = useState("ALL"); const [discovery, setDiscovery] = useState("ALL"); const [confirmation, setConfirmation] = useState("ALL"); const [reviewOrder, setReviewOrder] = useState("name");
  useEffect(() => setGroup(initialGroupId), [initialGroupId]);
  const filtered = newsletters.filter((item) => (!query.trim() || [item.name, item.group_name, item.website_url ?? ""].some((field) => field.toLowerCase().includes(query.trim().toLowerCase()))) && (group === "ALL" || item.group_id === group) && (status === "ALL" || item.status === status) && (discovery === "ALL" || item.discovery_status === discovery) && (confirmation === "ALL" || (confirmation === "YES" ? item.confirmation_required : !item.confirmation_required)));
  if (reviewOrder === "lowest") filtered.sort((a, b) => (a.signup_score ?? 101) - (b.signup_score ?? 101) || a.name.localeCompare(b.name));
  const hasFilters = Boolean(query.trim()) || group !== "ALL" || status !== "ALL" || discovery !== "ALL" || confirmation !== "ALL";
  const clearFilters = () => { setQuery(""); setGroup("ALL"); setStatus("ALL"); setDiscovery("ALL"); setConfirmation("ALL"); };
  return <section className="newsletter-panel"><div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> REGISTRY</div><h2>Newsletter registry</h2><p>Start a signup from a configured website or signup URL. A newsletter with no saved form profile is inspected first, so its public page is read once and saved for review.</p></div>{canManage && <div className="newsletter-actions"><button type="button" className="button secondary small" onClick={onDiscoverAll} disabled={discoverAllPending || !newsletters.length} title="Inspect every configured public website without starting signup automation"><RefreshCw size={15} className={discoverAllPending ? "spin-icon" : ""} />{discoverAllPending ? "Inspecting websites…" : "Optional: inspect websites"}</button><button type="button" className="button primary small" onClick={onAdd}><Plus size={15} /> Add newsletter</button></div>}</div><div className="newsletter-filters"><label className="search-field"><Search size={16} /><span className="sr-only">Search newsletters</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search newsletters, groups, or websites" />{query && <button type="button" className="clear-search" aria-label="Clear newsletter search" onClick={() => setQuery("")}><X size={14} /></button>}</label><FilterSelect label="Group" value={group} onChange={setGroup} options={[{ value: "ALL", label: "All groups" }, ...groups.map((item) => ({ value: item.id, label: item.name }))]} /><FilterSelect label="Status" value={status} onChange={setStatus} options={newsletterStatuses.map((item) => ({ value: item, label: item === "ALL" ? "All statuses" : labelStatus(item) }))} /><FilterSelect label="Discovery" value={discovery} onChange={setDiscovery} options={discoveryStatuses.map((item) => ({ value: item, label: item === "ALL" ? "All discovery states" : labelStatus(item) }))} /><FilterSelect label="Confirmation" value={confirmation} onChange={setConfirmation} options={[{ value: "ALL", label: "Any confirmation" }, { value: "YES", label: "Confirmation required" }, { value: "NO", label: "No confirmation" }]} /><FilterSelect label="Review order" value={reviewOrder} onChange={setReviewOrder} options={[{ value: "name", label: "Name" }, { value: "lowest", label: "Lowest signup score" }]} />{hasFilters && <button type="button" className="text-button newsletter-clear-filters" onClick={clearFilters}>Clear filters</button>}</div><div className="newsletter-result-summary" aria-live="polite">Showing {filtered.length} of {newsletters.length} newsletter{newsletters.length === 1 ? "" : "s"}</div>{filtered.length ? <div className="table-scroll newsletter-table-scroll"><table className="newsletter-table"><caption className="sr-only">Newsletter registry</caption><thead><tr><th>Newsletter</th><th>Group</th><th>Website / signup</th><th>Signup score</th><th>Confirmation</th><th>Status</th><th>Discovery</th><th>Profile</th><th>Last verified</th><th>Actions</th></tr></thead><tbody>{filtered.map((item) => <tr key={item.id}><td><button type="button" className="newsletter-name-button" onClick={() => onView(item)}><span className="newsletter-avatar"><Mail size={14} /></span><span><strong>{item.name}</strong><small>{item.id}</small></span></button></td><td><span className="newsletter-group-label"><span className="tag-dot" />{item.group_name}</span></td><td><div className="newsletter-link-cell"><span>{item.website_url ? <a href={item.website_url} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>{item.website_url.replace(/^https?:\/\//, "")}</a> : "Not configured"}</span><small>{item.signup_url ? item.signup_url.replace(/^https?:\/\//, "") : "No signup URL"}</small></div></td><td><div className="newsletter-score"><strong>{item.signup_score == null ? "No score" : `${item.signup_score}%`}</strong><small>{item.confirmed_count} confirmed · {item.subscribed_count - item.confirmed_count} submitted · {item.review_count} review</small></div></td><td><span className={`newsletter-boolean ${item.confirmation_required ? "yes" : "no"}`}>{item.confirmation_required ? "Required" : "Not required"}</span></td><td><div className="newsletter-status-stack"><StatusBadge value={item.registry_status} />{item.registry_status !== item.status && <small className="table-subtext">Operational: {labelStatus(item.status)}</small>}{item.failure_reason && <small className="table-subtext" title={item.failure_reason}>{item.failure_reason}</small>}</div></td><td><StatusBadge value={item.discovery_status} /></td><td><span className="newsletter-profile-value">{item.active_profile_version ? `v${item.active_profile_version} active` : "Not configured"}</span><small className="table-subtext">{item.profile_versions} version{item.profile_versions === 1 ? "" : "s"}</small></td><td className="newsletter-date-cell">{formatDate(item.last_verified_at)}</td><td><div className="newsletter-actions"><button type="button" className="icon-text-button" onClick={() => onView(item)}><ExternalLink size={13} /> View</button>{canOperate && (item.signup_url || item.website_url) && <button type="button" className="icon-text-button newsletter-start-action" onClick={() => onStartLearning(item)} title="Opens the signup dialog. If no form profile is saved yet, the dialog inspects the public signup page first."><ArrowRight size={13} /> Start signup</button>}{canManage && <><button type="button" className="icon-text-button" onClick={() => onEdit(item)}><Pencil size={13} /> Edit</button><button type="button" className="icon-text-button" onClick={() => onDiscover(item)} title="Optional website inspection; it does not gate signup"><Search size={13} /> Optional discovery</button><button type="button" className="icon-text-button" onClick={() => onStatus(item, item.status === "DISABLED" ? "enable" : "disable")}>{item.status === "DISABLED" ? "Enable" : "Disable"}</button></>}<button type="button" className="icon-text-button" onClick={() => onProfiles(item.id)}>Profiles</button></div></td></tr>)}</tbody></table></div> : <EmptyState icon={<Newspaper size={20} />} title={newsletters.length ? "No newsletters match these filters" : "No newsletters yet"} copy={newsletters.length ? "Clear a filter or search term to see more registry records." : "Add your first newsletter to begin building the automation registry."} action={canManage && !newsletters.length ? <button type="button" className="button primary small" onClick={onAdd}><Plus size={14} /> Add newsletter</button> : undefined} />}</section>;
}

function NewsletterGroups({ groups, newsletters, canManage, canOperate, onAdd, onEdit, onStatus, onSeedDirectory, onImportCatalog, onStartBulk, onOpenNewsletters }: { groups: NewsletterGroup[]; newsletters: Newsletter[]; canManage: boolean; canOperate: boolean; onAdd: () => void; onEdit: (group: NewsletterGroup) => void; onStatus: (group: NewsletterGroup, next: "enable" | "disable") => void; onSeedDirectory: () => void; onImportCatalog: () => void; onStartBulk: () => void; onOpenNewsletters: (id: string) => void }) { const statsFor = (groupId: string) => { const items = newsletters.filter((item) => item.group_id === groupId); return { active: items.filter((item) => item.status === "ACTIVE").length, broken: items.filter((item) => item.status === "AUTOMATION_BROKEN").length }; }; return <section className="newsletter-panel"><div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> REGISTRY ORGANIZATION</div><h2>Newsletter groups</h2><p>These groups organize newsletter records only. They are separate from Gmail account groups.</p></div><div className="newsletter-actions">{canOperate && <button type="button" className="button primary small" onClick={onStartBulk}><FileCog size={15} /> Bulk subscribe</button>}{canManage && <><button type="button" className="button secondary small" onClick={onImportCatalog}>Import supplied list</button><button type="button" className="button secondary small" onClick={onSeedDirectory}>Seed topic directory</button><button type="button" className="button primary small" onClick={onAdd}><Plus size={15} /> Create group</button></>}</div></div>{groups.length ? <div className="table-scroll newsletter-table-scroll"><table className="newsletter-table groups-table"><caption className="sr-only">Newsletter groups</caption><thead><tr><th>Group</th><th>Description</th><th>Status</th><th>Newsletters</th><th>Active</th><th>Broken</th><th>Created</th><th>Updated</th><th>Actions</th></tr></thead><tbody>{groups.map((group) => { const stats = statsFor(group.id); return <tr key={group.id}><td><strong>{group.name}</strong><small className="table-subtext">{group.id}</small></td><td className="newsletter-description-cell">{group.description || "No description"}</td><td><StatusBadge value={group.status} /></td><td><strong className="newsletter-count-value">{group.newsletter_count}</strong></td><td><strong className="newsletter-count-value">{stats.active}</strong></td><td><strong className={`newsletter-count-value ${stats.broken ? "danger-text" : ""}`}>{stats.broken}</strong></td><td>{formatDate(group.created_at)}</td><td>{formatDate(group.updated_at)}</td><td><div className="newsletter-actions">{canManage && <button type="button" className="icon-text-button" onClick={() => onEdit(group)}><Pencil size={13} /> Edit</button>}<button type="button" className="icon-text-button" onClick={() => onOpenNewsletters(group.id)}>View newsletters</button>{canManage && <button type="button" className="icon-text-button" onClick={() => onStatus(group, group.status === "DISABLED" ? "enable" : "disable")}>{group.status === "DISABLED" ? "Enable" : "Disable"}</button>}</div></td></tr>; })}</tbody></table></div> : <EmptyState icon={<UsersRound size={20} />} title="No newsletter groups yet" copy="Create a group to organize the registry by source, audience, or business area." action={canManage ? <button type="button" className="button primary small" onClick={onAdd}><Plus size={14} /> Create group</button> : undefined} />}</section>; }

function NewsletterAutomation({ newsletters, profiles, selectedNewsletterId, canManage, canOperate, onSelect, onView, onAction, onStartSignup }: { newsletters: Newsletter[]; profiles: NewsletterProfile[]; selectedNewsletterId: string; canManage: boolean; canOperate: boolean; onSelect: (value: string) => void; onView: (profile: NewsletterProfile) => void; onAction: (profile: NewsletterProfile, action: "promote" | "reject" | "rollback") => void; onStartSignup: (profile: NewsletterProfile) => void }) { const visible = selectedNewsletterId ? profiles.filter((profile) => profile.newsletter_id === selectedNewsletterId) : profiles; return <section className="newsletter-panel"><div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> VERSIONED CONFIGURATION</div><h2>Automation profiles</h2><p>Run a bounded one-account signup test from a candidate, tested, or active profile. Bulk jobs remain definitions only.</p></div><label className="group-context newsletter-selector"><span>NEWSLETTER</span><span className="select-wrap"><select value={selectedNewsletterId} onChange={(event) => onSelect(event.target.value)} aria-label="Filter automation profiles by newsletter"><option value="">All newsletters</option>{newsletters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select><ChevronDown size={15} /></span></label></div>{visible.length ? <div className="table-scroll newsletter-table-scroll"><table className="newsletter-table"><caption className="sr-only">Automation profiles</caption><thead><tr><th>Newsletter</th><th>Version</th><th>Status</th><th>Confidence</th><th>Discovered</th><th>Tested</th><th>Promoted</th><th>Last success / failure</th><th>Actions</th></tr></thead><tbody>{visible.map((profile) => <tr key={profile.id}><td><strong>{newsletters.find((item) => item.id === profile.newsletter_id)?.name ?? profile.newsletter_id}</strong><small className="table-subtext">{profile.id}</small></td><td><strong className="profile-version">v{profile.version}</strong></td><td><StatusBadge value={profile.status} /></td><td><div className="confidence-cell"><span><span style={{ width: `${Math.round(profile.confidence * 100)}%` }} /></span><strong>{Math.round(profile.confidence * 100)}%</strong></div></td><td>{formatDateTime(profile.discovered_at)}</td><td>{formatDateTime(profile.tested_at)}</td><td>{formatDateTime(profile.promoted_at)}</td><td><span>{formatDateTime(profile.last_success_at)}</span><small className="table-subtext">Failure: {formatDateTime(profile.last_failure_at)}</small></td><td><div className="newsletter-actions"><button type="button" className="icon-text-button" onClick={() => onView(profile)}>View</button>{canOperate && ["CANDIDATE", "TEST_FAILED", "TEST_PASSED", "ACTIVE"].includes(profile.status) && <button type="button" className="icon-text-button" onClick={() => onStartSignup(profile)}>Test one account</button>}{canManage && profile.status === "TEST_PASSED" && <button type="button" className="icon-text-button" onClick={() => onAction(profile, "promote")}>Promote</button>}{canManage && ["DISCOVERED", "CANDIDATE", "TEST_FAILED"].includes(profile.status) && <button type="button" className="icon-text-button" onClick={() => onAction(profile, "reject")}>Reject</button>}{canManage && ["SUPERSEDED", "TEST_PASSED"].includes(profile.status) && <button type="button" className="icon-text-button" onClick={() => onAction(profile, "rollback")}>Rollback</button>}</div></td></tr>)}</tbody></table></div> : <EmptyState icon={<Waypoints size={20} />} title="No automation profiles" copy={selectedNewsletterId ? "No automation profiles exist for this newsletter yet. Run public discovery first." : "No automation profiles exist yet. Run public discovery first."} />}</section>; }

function NewsletterJobs({ jobs, newsletters, newsletterGroups, accountGroups, canOperate, onCreate }: { jobs: NewsletterJob[]; newsletters: Newsletter[]; newsletterGroups: NewsletterGroup[]; accountGroups: AccountGroupOption[]; canOperate: boolean; onCreate: () => void }) { return <section className="newsletter-panel"><div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> DEFINITIONS ONLY</div><h2>Bulk job definitions</h2><p>Definitions are stored for a future dispatcher. Creating one does not start accounts or browsers.</p></div>{canOperate && <button type="button" className="button primary small" onClick={onCreate}><Plus size={15} /> Define job</button>}</div>{jobs.length ? <div className="table-scroll newsletter-table-scroll"><table className="newsletter-table"><caption className="sr-only">Newsletter job definitions</caption><thead><tr><th>Job</th><th>Newsletter / group</th><th>Target Gmail group</th><th>Status</th><th>Concurrency</th><th>Auto confirm</th><th>Created by</th><th>Created</th></tr></thead><tbody>{jobs.map((job) => <tr key={job.id}><td><strong className="mono">{job.id}</strong><small className="table-subtext">{job.total_tasks} tasks persisted</small></td><td><strong>{job.newsletter_name ?? job.newsletter_group_name ?? "No target"}</strong><small className="table-subtext">{job.newsletter_id ? "Newsletter target" : "Newsletter group target"}</small></td><td><span className="newsletter-group-label"><span className="tag-dot" />{job.account_group_name}</span></td><td><StatusBadge value={job.status} /></td><td><strong>{job.concurrency_limit}</strong> <small className="table-subtext">profiles</small></td><td>{job.auto_confirm ? "Enabled" : "Off"}</td><td className="mono">{job.created_by}</td><td>{formatDate(job.created_at)}</td></tr>)}</tbody></table></div> : <EmptyState icon={<FileCog size={20} />} title="No newsletter jobs have been defined yet" copy="Create a definition when you are ready to prepare a future bulk run. It will not dispatch work in this phase." action={canOperate && accountGroups.length ? <button type="button" className="button primary small" onClick={onCreate}><Plus size={14} /> Define job</button> : undefined} />}</section>; }

function NewsletterSubscriptions({ subscriptions, newsletters, accountGroups, canOperate = false, profiles = [], onStartConfirmation }: { subscriptions: NewsletterSubscription[]; newsletters: Newsletter[]; accountGroups: AccountGroupOption[]; canOperate?: boolean; profiles?: NewsletterProfile[]; onStartConfirmation?: (subscription: NewsletterSubscription) => void }) {
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("ALL");
  const [newsletter, setNewsletter] = useState("ALL");
  const [state, setState] = useState("ALL");
  const filtered = subscriptions.filter((item) => (!query.trim() || [item.account_email, item.newsletter_name].some((field) => field.toLowerCase().includes(query.trim().toLowerCase()))) && (group === "ALL" || item.account_group_id === group) && (newsletter === "ALL" || item.newsletter_id === newsletter) && (state === "ALL" || item.state === state));
  return <section className="newsletter-panel"><div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> ACCOUNT RELATIONSHIPS</div><h2>Subscriptions</h2><p>Signup and Gmail confirmation results from Phases 5 and 6.</p></div></div><div className="newsletter-filters"><label className="search-field"><Search size={16} /><span className="sr-only">Search subscriptions</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search account or newsletter" />{query && <button type="button" className="clear-search" aria-label="Clear subscription search" onClick={() => setQuery("")}><X size={14} /></button>}</label><FilterSelect label="Account group" value={group} onChange={setGroup} options={[{ value: "ALL", label: "All account groups" }, ...accountGroups.map((item) => ({ value: item.id, label: item.name }))]} /><FilterSelect label="Newsletter" value={newsletter} onChange={setNewsletter} options={[{ value: "ALL", label: "All newsletters" }, ...newsletters.map((item) => ({ value: item.id, label: item.name }))]} /><FilterSelect label="State" value={state} onChange={setState} options={subscriptionStates.map((item) => ({ value: item, label: item === "ALL" ? "All states" : labelStatus(item) }))} /></div>{filtered.length ? <div className="table-scroll newsletter-table-scroll"><table className="newsletter-table subscriptions-table"><caption className="sr-only">Newsletter subscriptions</caption><thead><tr><th>Gmail account</th><th>Account group</th><th>Newsletter</th><th>Newsletter group</th><th>State</th><th>Submitted</th><th>Confirmed</th><th>Last checked</th><th>Last error</th><th>Action</th></tr></thead><tbody>{filtered.map((item) => { const profile = profiles.find((candidate) => candidate.newsletter_id === item.newsletter_id && ["ACTIVE", "TEST_PASSED", "CANDIDATE"].includes(candidate.status)); const canConfirm = canOperate && Boolean(onStartConfirmation) && Boolean(profile) && ["SUBMITTED", "WAITING_CONFIRMATION", "MANUAL_REVIEW", "FAILED"].includes(item.state); return <tr key={item.id}><td><strong className="mono">{item.account_email}</strong></td><td>{item.account_group_name}</td><td><strong>{item.newsletter_name}</strong></td><td>{item.newsletter_group_name}</td><td><StatusBadge value={item.state} /></td><td>{formatDateTime(item.submitted_at)}</td><td>{formatDateTime(item.confirmed_at)}</td><td>{formatDateTime(item.last_checked_at)}</td><td className="newsletter-error-cell">{item.last_error || "—"}</td><td>{canConfirm ? <button type="button" className="button secondary small" onClick={() => onStartConfirmation?.(item)}>{item.state === "SUBMITTED" ? "Confirm" : "Retry confirmation"}</button> : "—"}</td></tr>; })}</tbody></table></div> : <EmptyState icon={<Inbox size={20} />} title="No newsletter subscriptions have been recorded yet" copy="Subscription records appear here after signup creates a relationship." />}</section>;
}

function NewsletterActivity({ activity }: { activity: AuditEvent[] }) { const [query, setQuery] = useState(""); const [result, setResult] = useState("ALL"); const filtered = activity.filter((event) => (!query.trim() || [event.action, event.target_type, event.target_id].some((field) => field.toLowerCase().includes(query.trim().toLowerCase()))) && (result === "ALL" || event.result === result)); return <section className="newsletter-panel"><div className="panel-header newsletter-panel-heading"><div><div className="eyebrow"><span className="eyebrow-line" /> AUDIT TRAIL</div><h2>Newsletter activity</h2><p>Mutation evidence from the existing audit API, filtered to newsletter events.</p></div></div><div className="newsletter-filters"><label className="search-field"><Search size={16} /><span className="sr-only">Search newsletter activity</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search action, target, or ID" />{query && <button type="button" className="clear-search" aria-label="Clear activity search" onClick={() => setQuery("")}><X size={14} /></button>}</label><FilterSelect label="Result" value={result} onChange={setResult} options={[{ value: "ALL", label: "All results" }, { value: "success", label: "Success" }, { value: "failure", label: "Failure" }]} /></div><div className="newsletter-result-summary" aria-live="polite">Showing {filtered.length} of {activity.length} newsletter event{activity.length === 1 ? "" : "s"}</div>{filtered.length ? <div className="newsletter-activity-list">{filtered.map((event) => <div className="newsletter-activity-row" key={event.id}><span className={`newsletter-activity-mark ${event.result === "success" ? "ok" : "danger"}`}><Activity size={14} /></span><div><strong>{event.action}</strong><small>{event.target_type} · {event.target_id}</small></div><span className={`newsletter-activity-result ${event.result}`}>{event.result}</span><time>{formatDateTime(event.created_at)}</time></div>)}</div> : <EmptyState icon={<Activity size={20} />} title={activity.length ? "No activity matches these filters" : "No newsletter activity yet"} copy={activity.length ? "Clear the search or result filter to see more audit events." : "Registry mutations will appear here when they are recorded by the backend audit trail."} />}</section>; }

function StatusBadge({ value }: { value: string }) { return <span className={`newsletter-status ${statusTone(value)}`}><span />{labelStatus(value)}</span>; }
function EmptyState({ icon, title, copy, action }: { icon: React.ReactNode; title: string; copy: string; action?: React.ReactNode }) { return <div className="newsletter-empty"><div className="newsletter-empty-icon">{icon}</div><strong>{title}</strong><span>{copy}</span>{action}</div>; }
function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: { value: string; label: string }[] }) { return <label className="newsletter-filter-select"><span>{label}</span><span className="select-wrap"><select aria-label={label} value={value} onChange={(event) => onChange(event.target.value)}>{options.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select><ChevronDown size={14} /></span></label>; }

function GroupForm({ group, onClose, onSaved }: { group: NewsletterGroup | null; onClose: () => void; onSaved: (message: string) => void }) { const [name, setName] = useState(group?.name ?? ""); const [description, setDescription] = useState(group?.description ?? ""); const [error, setError] = useState(""); const [pending, setPending] = useState(false); const submit = async (event: FormEvent) => { event.preventDefault(); if (!name.trim()) { setError("Enter a group name."); return; } setPending(true); setError(""); try { if (group) await newsletterRequests.updateGroup(group.id, { name: name.trim(), description: description.trim() || null }); else await newsletterRequests.createGroup({ name: name.trim(), description: description.trim() || null }); onSaved(group ? "Newsletter group updated." : "Newsletter group created."); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); } }; return <ModalShell title={group ? "Edit newsletter group" : "Create newsletter group"} description="Organize newsletters without changing Gmail account-group permissions." onClose={onClose}><form className="newsletter-form" onSubmit={submit} noValidate>{error && <FormError message={error} /> }<div className="field"><label htmlFor="newsletter-group-name">Name <span className="required">Required</span></label><input id="newsletter-group-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={120} autoFocus /></div><div className="field"><label htmlFor="newsletter-group-description">Description</label><textarea className="resize-none" id="newsletter-group-description" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={500} rows={4} /></div><ModalFooter onClose={onClose} pending={pending} label={group ? "Save changes" : "Create group"} /></form></ModalShell>; }

function NewsletterForm({ newsletter, groups, onClose, onSaved }: { newsletter: Newsletter | null; groups: NewsletterGroup[]; onClose: () => void; onSaved: (message: string) => void }) { const [name, setName] = useState(newsletter?.name ?? ""); const [groupId, setGroupId] = useState(newsletter?.group_id ?? groups[0]?.id ?? ""); const [websiteUrl, setWebsiteUrl] = useState(newsletter?.website_url ?? ""); const [signupUrl, setSignupUrl] = useState(newsletter?.signup_url ?? ""); const [confirmation, setConfirmation] = useState(newsletter?.confirmation_required ?? false); const [domains, setDomains] = useState(newsletter?.allowed_confirmation_domains.join("\n") ?? ""); const [senders, setSenders] = useState(newsletter?.sender_patterns.join("\n") ?? ""); const [subjects, setSubjects] = useState(newsletter?.subject_patterns.join("\n") ?? ""); const [status, setStatus] = useState(newsletter?.status ?? "ACTIVE"); const [error, setError] = useState(""); const [pending, setPending] = useState(false); const splitLines = (value: string) => value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean); const submit = async (event: FormEvent) => { event.preventDefault(); if (!name.trim() || !groupId) { setError("Name and newsletter group are required."); return; } setPending(true); setError(""); const payload = { group_id: groupId, name: name.trim(), website_url: websiteUrl.trim() || null, signup_url: signupUrl.trim() || null, confirmation_required: confirmation, allowed_confirmation_domains: splitLines(domains), sender_patterns: splitLines(senders), subject_patterns: splitLines(subjects), ...(newsletter ? { status } : {}) }; try { if (newsletter) await newsletterRequests.updateNewsletter(newsletter.id, payload); else await newsletterRequests.createNewsletter(payload); onSaved(newsletter ? "Newsletter changes saved." : "Newsletter added to the registry."); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); } }; return <ModalShell title={newsletter ? "Edit newsletter" : "Add newsletter"} description="Keep the registry record factual. Save the record first, then run public discovery as a separate review step." onClose={onClose} wide><form className="newsletter-form" onSubmit={submit} noValidate>{error && <FormError message={error} />}<div className="form-grid"><div className="field"><label htmlFor="newsletter-name">Name <span className="required">Required</span></label><input id="newsletter-name" value={name} onChange={(event) => setName(event.target.value)} maxLength={160} autoFocus /></div><div className="field"><label htmlFor="newsletter-group">Newsletter group <span className="required">Required</span></label><select id="newsletter-group" value={groupId} onChange={(event) => setGroupId(event.target.value)}>{groups.map((group) => <option key={group.id} value={group.id}>{group.name}</option>)}</select></div><div className="field"><label htmlFor="newsletter-website">Website URL</label><input id="newsletter-website" type="url" value={websiteUrl} onChange={(event) => setWebsiteUrl(event.target.value)} placeholder="https://example.com" maxLength={500} /></div><div className="field"><label htmlFor="newsletter-signup">Signup URL</label><input id="newsletter-signup" type="url" value={signupUrl} onChange={(event) => setSignupUrl(event.target.value)} placeholder="https://example.com/subscribe" maxLength={500} /></div></div><label className="newsletter-checkbox"><input type="checkbox" checked={confirmation} onChange={(event) => setConfirmation(event.target.checked)} /><span><strong>Confirmation required</strong><small>Keep this fact in the registry for the later confirmation phase.</small></span></label>{newsletter && <div className="field"><label htmlFor="newsletter-status">Registry status</label><select id="newsletter-status" value={status} onChange={(event) => setStatus(event.target.value)}>{newsletterStatuses.filter((item) => item !== "ALL").map((item) => <option value={item} key={item}>{labelStatus(item)}</option>)}</select></div>}<div className="form-divider">Matching metadata</div><div className="form-grid"><div className="field"><label htmlFor="newsletter-domains">Allowed confirmation domains</label><textarea className="resize-none" id="newsletter-domains" value={domains} onChange={(event) => setDomains(event.target.value)} placeholder="example.com\nupdates.example.org" rows={4} /><small>One domain per line. The backend normalizes and validates these values.</small></div><div className="field"><label htmlFor="newsletter-senders">Sender patterns</label><textarea className="resize-none" id="newsletter-senders" value={senders} onChange={(event) => setSenders(event.target.value)} placeholder="newsletter@example.com\nExample News" rows={4} /></div><div className="field"><label htmlFor="newsletter-subjects">Subject patterns</label><textarea className="resize-none" id="newsletter-subjects" value={subjects} onChange={(event) => setSubjects(event.target.value)} placeholder="Weekly digest\nLatest updates" rows={4} /></div></div><div className="security-callout"><ShieldCheck size={16} /><div><strong>Safe registry boundary</strong><span>No passwords, cookies, browser data, or raw account credentials are accepted by this form.</span></div></div><ModalFooter onClose={onClose} pending={pending} label={newsletter ? "Save changes" : "Save newsletter"} /></form></ModalShell>; }

function LegacyJobForm({ newsletters, groups, accountGroups, onClose, onSaved }: { newsletters: Newsletter[]; groups: NewsletterGroup[]; accountGroups: AccountGroupOption[]; onClose: () => void; onSaved: (message: string) => void }) { const [targetType, setTargetType] = useState<"newsletter" | "group">("newsletter"); const [newsletterId, setNewsletterId] = useState(newsletters[0]?.id ?? ""); const [newsletterGroupId, setNewsletterGroupId] = useState(groups[0]?.id ?? ""); const [accountGroupId, setAccountGroupId] = useState(accountGroups[0]?.id ?? ""); const [concurrency, setConcurrency] = useState("4"); const [autoConfirm, setAutoConfirm] = useState(true); const [error, setError] = useState(""); const [pending, setPending] = useState(false); const submit = async (event: FormEvent) => { event.preventDefault(); if (!accountGroupId || (targetType === "newsletter" ? !newsletterId : !newsletterGroupId)) { setError("Choose a target and a Gmail account group."); return; } setPending(true); setError(""); try { await newsletterRequests.createJob({ account_group_id: accountGroupId, concurrency_limit: Number(concurrency), auto_confirm: autoConfirm, retry_policy_json: {}, ...(targetType === "newsletter" ? { newsletter_id: newsletterId } : { newsletter_group_id: newsletterGroupId }) }); onSaved("Newsletter job definition created."); } catch (requestError) { setError((requestError as Error).message); } finally { setPending(false); } }; return <ModalShell title="Define a bulk newsletter job" description="This stores a definition only. Starting it will submit each account separately and automatically confirm through Gmail when required." onClose={onClose}><form className="newsletter-form" onSubmit={submit} noValidate>{error && <FormError message={error} />}<div className="field"><label htmlFor="job-target-type">Target type</label><select id="job-target-type" value={targetType} onChange={(event) => setTargetType(event.target.value as "newsletter" | "group")}><option value="newsletter">One newsletter</option><option value="group">Newsletter group</option></select></div>{targetType === "newsletter" ? <div className="field"><label htmlFor="job-newsletter">Newsletter</label><select id="job-newsletter" value={newsletterId} onChange={(event) => setNewsletterId(event.target.value)}>{newsletters.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div> : <div className="field"><label htmlFor="job-newsletter-group">Newsletter group</label><select id="job-newsletter-group" value={newsletterGroupId} onChange={(event) => setNewsletterGroupId(event.target.value)}>{groups.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}</select></div>}<div className="field"><label htmlFor="job-account-group">Target Gmail account group</label><select id="job-account-group" value={accountGroupId} onChange={(event) => setAccountGroupId(event.target.value)}>{accountGroups.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.account_count} accounts</option>)}</select><small>Account-group access is verified by the backend.</small></div><div className="form-grid"><div className="field"><label htmlFor="job-concurrency">Concurrency limit</label><input id="job-concurrency" type="number" min="1" max="10" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} /><small>Up to 10 different accounts at once; one browser session per saved account.</small></div><label className="newsletter-checkbox"><input type="checkbox" checked={autoConfirm} onChange={(event) => setAutoConfirm(event.target.checked)} /><span><strong>Auto confirm requested</strong><small>Automatically searches Gmail after each submitted signup and verifies an allowlisted confirmation link.</small></span></label></div><ModalFooter onClose={onClose} pending={pending} label="Create definition" /></form></ModalShell>; }

function JobForm({ newsletters, groups, accountGroups, presetAccounts, onClose, onSaved }: { newsletters: Newsletter[]; groups: NewsletterGroup[]; accountGroups: AccountGroupOption[]; presetAccounts: NewsletterJobAccountTarget[]; onClose: () => void; onSaved: (message: string, startedJobIds?: string[], concurrency?: number) => void }) {
  const [targetType, setTargetType] = useState<"newsletter" | "group" | "groups">("groups");
  const [recipientMode, setRecipientMode] = useState<"saved" | "external">("saved");
  const [externalRecipients, setExternalRecipients] = useState("");
  const [externalConsent, setExternalConsent] = useState(false);
  const [externalHeadless, setExternalHeadless] = useState(true);
  const [newsletterId, setNewsletterId] = useState(newsletters[0]?.id ?? "");
  const [newsletterGroupId, setNewsletterGroupId] = useState(groups[0]?.id ?? "");
  const [newsletterGroupIds, setNewsletterGroupIds] = useState<string[]>(groups.slice(0, 1).map((group) => group.id));
  const [accountScope, setAccountScope] = useState<"one" | "all">("all");
  const [accountGroupId, setAccountGroupId] = useState(accountGroups[0]?.id ?? "");
  const [concurrency, setConcurrency] = useState("4");
  const [autoConfirm, setAutoConfirm] = useState(true);
  const [authenticatedOnly, setAuthenticatedOnly] = useState(presetAccounts.length > 0);
  const [randomizeNewsletters, setRandomizeNewsletters] = useState(false);
  const [newslettersPerAccount, setNewslettersPerAccount] = useState("3");
  // Bulk subscribe is an execution control, so the safe and expected default
  // is to start the created jobs. Operators can still turn this off when they
  // explicitly want definitions only.
  const [startImmediately, setStartImmediately] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [pending, setPending] = useState(false);

  const isAvailableNewsletter = (newsletter: Newsletter) => ["ACTIVE", "TEST_REQUIRED"].includes(newsletter.status) && !["FAILED", "DISCOVERY_BLOCKED"].includes(newsletter.discovery_status);
  const availableNewsletterCount = (groupId: string) => newsletters.filter((newsletter) => newsletter.group_id === groupId && isAvailableNewsletter(newsletter)).length;
  const selectedNewsletterGroupIds = targetType === "groups" ? newsletterGroupIds : newsletterGroupId ? [newsletterGroupId] : [];
  const selectedReadyCount = targetType === "newsletter"
    ? (newsletters.find((newsletter) => newsletter.id === newsletterId && isAvailableNewsletter(newsletter)) ? 1 : 0)
    : selectedNewsletterGroupIds.reduce((total, groupId) => total + availableNewsletterCount(groupId), 0);
  const canStartImmediately = selectedReadyCount > 0 && (targetType === "newsletter" || selectedNewsletterGroupIds.every((groupId) => availableNewsletterCount(groupId) > 0));

  const toggleNewsletterGroup = (id: string) => setNewsletterGroupIds((current) => current.includes(id) ? current.filter((value) => value !== id) : [...current, id]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const isExternal = recipientMode === "external";
    const selectedAccountGroups = presetAccounts.length
      ? [...new Set(presetAccounts.map((account) => account.group_id))]
      : isExternal ? (accountGroupId ? [accountGroupId] : []) : (accountScope === "all" ? accountGroups.map((group) => group.id) : accountGroupId ? [accountGroupId] : []);
    const selectedNewsletterGroups = targetType === "groups" ? newsletterGroupIds : targetType === "group" && newsletterGroupId ? [newsletterGroupId] : [];
    if (!selectedAccountGroups.length || (targetType === "newsletter" ? !newsletterId : !selectedNewsletterGroups.length)) {
      setError("Choose at least one account group and one newsletter target.");
      return;
    }
    if (presetAccounts.length && authenticatedOnly && !presetAccounts.some((account) => account.login_status === "AUTHENTICATED" && account.profile_status === "SAVED")) {
      setError("None of the selected accounts is authenticated with a saved browser profile.");
      return;
    }
    const recipients = [...new Set(externalRecipients.split(/\r?\n|,|;/).map((value) => value.trim().toLowerCase()).filter(Boolean))];
    if (isExternal && accountScope !== "one") {
      setError("Choose one permission-boundary account group for external recipients.");
      return;
    }
    if (isExternal && (!externalConsent || !recipients.length || recipients.some((value) => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) || recipients.length > 300)) {
      setError("Enter 1–300 valid addresses and confirm that you own or have permission to subscribe them.");
      return;
    }
    const concurrencyLimit = Number(concurrency);
    if (!Number.isInteger(concurrencyLimit) || concurrencyLimit < 1 || concurrencyLimit > 10) {
      setError("Newsletter concurrency must be between 1 and 10 sessions.");
      return;
    }
    const randomCount = Number(newslettersPerAccount);
    if (randomizeNewsletters && (targetType === "newsletter" || !Number.isInteger(randomCount) || randomCount < 1 || randomCount > 50)) {
      setError(targetType === "newsletter" ? "Random selection requires a newsletter group target." : "Newsletters per account must be between 1 and 50.");
      return;
    }
    if (startImmediately && !canStartImmediately) {
      setNotice(targetType === "newsletter"
        ? "Nothing was created. The selected newsletter is disabled or blocked for execution."
        : "Nothing was created. The selected groups contain no available newsletters. Disabled or discovery-blocked records must be fixed before execution.");
      setError("");
      return;
    }
    const shouldStartImmediately = startImmediately;
    setPending(true);
    setError("");
    setNotice("");
    // A selected-groups run is one target pool.  Creating one job per group
    // would apply "newsletters per account" once per group and silently
    // exceed the value chosen in this dialog.
    const targetCount = 1;
    const totalJobs = selectedAccountGroups.length * targetCount;
    const createdJobs: string[] = [];
    const startedJobIds: string[] = [];
    const bulkRunId = typeof crypto !== "undefined" && typeof crypto.randomUUID === "function" ? crypto.randomUUID() : `bulk-${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
    try {
      for (const accountGroup of selectedAccountGroups) {
        const targets = targetType === "newsletter"
          ? [{ newsletter_id: newsletterId }]
          : [{ newsletter_group_id: selectedNewsletterGroups[0] }];
        for (const target of targets) {
          const scopedAccountIds = presetAccounts.length ? presetAccounts.filter((account) => account.group_id === accountGroup).map((account) => account.id) : undefined;
          const job = await newsletterRequests.createJob({ account_group_id: accountGroup, concurrency_limit: concurrencyLimit, auto_confirm: isExternal ? false : autoConfirm, retry_policy_json: { bulk_run_id: bulkRunId, bulk_concurrency_limit: concurrencyLimit, ...(scopedAccountIds ? { account_ids: scopedAccountIds } : {}), ...(!isExternal && authenticatedOnly ? { authenticated_only: true } : {}), ...(targetType !== "newsletter" ? { newsletter_group_ids: selectedNewsletterGroups } : {}), ...(randomizeNewsletters ? { randomize_newsletters: true, random_newsletter_count: randomCount } : {}), ...(isExternal ? { recipient_mode: "EXTERNAL", external_recipients: recipients, consent_confirmed: true, headless_browser: externalHeadless } : {}) }, ...target });
          createdJobs.push(job.id);
        }
      }
      if (shouldStartImmediately) {
        for (const jobId of createdJobs) {
          await newsletterRequests.startJob(jobId);
          startedJobIds.push(jobId);
        }
      }
      onSaved(shouldStartImmediately
        ? `Started ${createdJobs.length} newsletter jobs across ${selectedAccountGroups.length} account group${selectedAccountGroups.length === 1 ? "" : "s"}.`
        : `Created ${createdJobs.length} newsletter job${createdJobs.length === 1 ? "" : "s"} for ${selectedAccountGroups.length} account group${selectedAccountGroups.length === 1 ? "" : "s"}.`, shouldStartImmediately ? createdJobs : [], concurrencyLimit);
    } catch (requestError) {
      const detail = `${(requestError as Error).message}. ${createdJobs.length} of ${totalJobs} job${totalJobs === 1 ? "" : "s"} were created; ${startedJobIds.length} started. Review Bulk subscribe.`;
      if (createdJobs.length) {
        // Do not leave the modal open while the backend is already running a
        // partial request. Persist the indicator and Stop controls from the
        // jobs returned so an error on a later group cannot hide the run.
        onSaved(detail, startedJobIds, concurrencyLimit);
      } else {
        setError(detail);
      }
    } finally {
      setPending(false);
    }
  };

  return <ModalShell title={presetAccounts.length ? `Subscribe ${presetAccounts.length} selected account${presetAccounts.length === 1 ? "" : "s"}` : "Start a grouped newsletter subscription run"} description={presetAccounts.length ? "Choose newsletter targets and run settings. Only the selected accounts are in scope." : "Starts the selected subscriptions immediately. The internal run record is used only for progress, account locks, retries, and audit history."} onClose={onClose} wide>
    <form className="newsletter-form" onSubmit={submit} noValidate>
      {error && <FormError message={error} />}
      {notice && <div className="security-callout" role="status"><ShieldCheck size={16} /><div><strong>Run not started</strong><span>{notice}</span></div></div>}
      {!presetAccounts.length && <div className="field"><label htmlFor="job-recipient-mode">Recipient type</label><select id="job-recipient-mode" value={recipientMode} onChange={(event) => { const next = event.target.value as "saved" | "external"; setRecipientMode(next); if (next === "external") setAccountScope("one"); }}><option value="saved">Saved Gmail accounts</option><option value="external">External email recipients</option></select><small>External mode uses a fresh browser with no saved Gmail session and cannot confirm through a mailbox.</small></div>}
      {recipientMode === "external" && <><div className="field"><label htmlFor="job-external-recipients">External email addresses</label><textarea className="resize-none" id="job-external-recipients" rows={5} value={externalRecipients} onChange={(event) => setExternalRecipients(event.target.value)} placeholder="one-address@example.com\nanother-address@example.net" /><small>Only use addresses you own or have explicit permission to subscribe. One per line, comma, or semicolon.</small></div><label className="newsletter-checkbox"><input type="checkbox" checked={externalConsent} onChange={(event) => setExternalConsent(event.target.checked)} /><span><strong>Permission confirmed</strong><small>I confirm these recipients have authorized this newsletter subscription.</small></span></label></>}
      {recipientMode === "external" && <label className="newsletter-checkbox"><input type="checkbox" checked={externalHeadless} onChange={(event) => setExternalHeadless(event.target.checked)} /><span><strong>Use headless browser</strong><small>Recommended: each address runs in a fresh temporary browser context without a saved Gmail session. Turn this off only when you need to watch the page while troubleshooting.</small></span></label>}
      <div className="form-grid">
        <div className="field"><label htmlFor="job-target-type">Newsletter target</label><select id="job-target-type" value={targetType} onChange={(event) => setTargetType(event.target.value as "newsletter" | "group" | "groups")}><option value="groups">Selected newsletter groups</option><option value="group">One newsletter group</option><option value="newsletter">One newsletter</option></select><small>Each selected newsletter group contributes all of its eligible newsletter records.</small></div>
        {!presetAccounts.length && <div className="field"><label htmlFor="job-account-scope">{recipientMode === "external" ? "Permission boundary" : "Gmail account scope"}</label><select id="job-account-scope" value={accountScope} disabled={recipientMode === "external"} onChange={(event) => setAccountScope(event.target.value as "one" | "all")}><option value="all">All account groups</option><option value="one">One account group</option></select><small>{recipientMode === "external" ? "Select one group below; it controls employee access only and is not used as a source of Gmail accounts." : "“All” means every account group visible to your employee session."}</small></div>}
      </div>
      {targetType === "newsletter" && <div className="field"><label htmlFor="job-newsletter">Newsletter</label><select id="job-newsletter" value={newsletterId} onChange={(event) => setNewsletterId(event.target.value)}>{newsletters.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.group_name} · {isAvailableNewsletter(item) ? "available" : "blocked"}</option>)}</select><small>{selectedReadyCount ? "This newsletter is included in the run. A saved profile is preferred; otherwise the live-form resolver is used." : "This newsletter is blocked, disabled, or failed discovery."}</small></div>}
      {targetType === "group" && <div className="field"><label htmlFor="job-newsletter-group">Newsletter group</label><select id="job-newsletter-group" value={newsletterGroupId} onChange={(event) => setNewsletterGroupId(event.target.value)}>{groups.map((item) => <option key={item.id} value={item.id}>{item.name} · {availableNewsletterCount(item.id)} available of {item.newsletter_count}</option>)}</select><small>{selectedReadyCount} newsletter{selectedReadyCount === 1 ? "" : "s"} will be included in this run.</small></div>}
      {targetType === "groups" && <div className="field"><label>Newsletter groups <span className="required">Select one or more</span></label><div className="newsletter-identity-generate">{groups.map((group) => <label className="newsletter-checkbox" key={group.id}><input type="checkbox" checked={newsletterGroupIds.includes(group.id)} onChange={() => toggleNewsletterGroup(group.id)} /><span><strong>{group.name}</strong><small>{availableNewsletterCount(group.id)} available of {group.newsletter_count} records · {group.status}</small></span></label>)}</div><small>{selectedReadyCount} newsletter{selectedReadyCount === 1 ? "" : "s"} will be included across the selected groups. Disabled or discovery-blocked records are excluded.</small></div>}
      {presetAccounts.length > 0 && <div className="field"><label>Selected accounts</label><div className="newsletter-selected-account-list">{presetAccounts.map((account) => <span key={account.id} className="newsletter-selected-account"><strong>{account.email}</strong><small>{account.group_name} · {labelStatus(account.login_status)} · profile {labelStatus(account.profile_status)}</small></span>)}</div></div>}
      {(accountScope === "one" || recipientMode === "external") && !presetAccounts.length && <div className="field"><label htmlFor="job-account-group">{recipientMode === "external" ? "Permission-boundary account group" : "Target Gmail account group"}</label><select id="job-account-group" value={accountGroupId} onChange={(event) => setAccountGroupId(event.target.value)}>{accountGroups.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.account_count} accounts</option>)}</select></div>}
      <div className="form-grid"><div className="field"><label htmlFor="job-concurrency">Concurrent {recipientMode === "external" ? "recipient browsers" : "accounts"} for this run</label><input id="job-concurrency" type="number" min="1" max="10" value={concurrency} onChange={(event) => setConcurrency(event.target.value)} /><small>{recipientMode === "saved" ? "Up to 10 distinct accounts across workspace newsletter runs. Each account keeps one browser session until its task ends." : "Up to 10 isolated recipient browsers across workspace newsletter runs."}</small></div>{recipientMode === "saved" && <label className="newsletter-checkbox"><input type="checkbox" checked={autoConfirm} onChange={(event) => setAutoConfirm(event.target.checked)} /><span><strong>Auto confirm requested</strong><small>Search Gmail after submitted signups when confirmation is required.</small></span></label>}</div>
      {recipientMode === "saved" && <label className="newsletter-checkbox"><input type="checkbox" checked={authenticatedOnly} onChange={(event) => setAuthenticatedOnly(event.target.checked)} /><span><strong>Authenticated accounts only</strong><small>Only accounts with AUTHENTICATED login status and a saved browser profile receive tasks. The backend applies this filter when it materializes the job.</small></span></label>}
      <label className="newsletter-checkbox"><input type="checkbox" checked={randomizeNewsletters} disabled={targetType === "newsletter"} onChange={(event) => setRandomizeNewsletters(event.target.checked)} /><span><strong>Randomize newsletters per account</strong><small>Each account receives its own stable random sample from the eligible newsletters across all selected groups. The same newsletter is never duplicated for that account in one job.</small></span></label>
      {randomizeNewsletters && targetType !== "newsletter" && <div className="field"><label htmlFor="job-newsletters-per-account">Newsletters per account</label><input id="job-newsletters-per-account" type="number" min="1" max="50" value={newslettersPerAccount} onChange={(event) => setNewslettersPerAccount(event.target.value)} /><small>If the selected groups contain fewer eligible newsletters, all available eligible newsletters are used.</small></div>}
      <label className="newsletter-checkbox"><input type="checkbox" checked={startImmediately} onChange={(event) => setStartImmediately(event.target.checked)} /><span><strong>Start subscriptions immediately</strong><small>{canStartImmediately ? "All available newsletters in the selected groups will be included." : "Select a group with available newsletters, or turn this off to save a definition."} {recipientMode === "external" ? "Each address gets its own isolated browser and ends as submitted, waiting confirmation, or manual review." : "A saved profile is preferred; sites that cannot be resolved remain manual review."}</small></span></label>
      <div className="security-callout"><ShieldCheck size={16} /><div><strong>Controlled expansion</strong><span>{selectedReadyCount} newsletter{selectedReadyCount === 1 ? " is" : "s are"} included across the selected target{selectedReadyCount === 1 ? "" : "s"}. This creates {accountScope === "all" ? accountGroups.length : 1} account-group scope{accountScope === "all" && accountGroups.length === 1 ? "" : "s"} × {targetCountForJob(targetType, newsletterGroupIds.length)} newsletter target{targetCountForJob(targetType, newsletterGroupIds.length) === 1 ? "" : "s"}{randomizeNewsletters && targetType !== "newsletter" ? `, with up to ${newslettersPerAccount} selected per account` : ""}. Disabled and discovery-blocked records are excluded.</span></div></div>
      <ModalFooter onClose={onClose} pending={pending} disabled={startImmediately && !canStartImmediately} label={startImmediately ? (canStartImmediately ? "Start subscriptions now" : "No available newsletters") : "Create job definition"} />
    </form>
  </ModalShell>;
}

function targetCountForJob(targetType: "newsletter" | "group" | "groups", _selectedGroupCount: number): number { return targetType === "newsletter" ? 1 : 1; }

function NewsletterDetail({ newsletter, health, onClose, onEdit, onProfiles, onDiscover, onRediscover, onStartLearning, canManage }: { newsletter: Newsletter; health: NewsletterHealth | null; onClose: () => void; onEdit: () => void; onProfiles: () => void; onDiscover: () => void; onRediscover: () => void; onStartLearning: () => void; canManage: boolean }) { return <ModalShell title={newsletter.name} description="Registry detail" onClose={onClose}><div className="newsletter-detail-view"><div className="newsletter-detail-status"><div className="newsletter-status-stack"><StatusBadge value={newsletter.registry_status} />{newsletter.registry_status !== newsletter.status && <small className="table-subtext">Operational: {labelStatus(newsletter.status)}</small>}{newsletter.failure_reason && <small className="table-subtext">{newsletter.failure_reason}</small>}</div><span>Discovery: {labelStatus(newsletter.discovery_status)}</span></div><dl className="newsletter-facts"><Fact label="Group" value={newsletter.group_name} /><Fact label="Website" value={newsletter.website_url || "Not configured"} /><Fact label="Signup URL" value={newsletter.signup_url || "Not configured"} /><Fact label="Confirmation" value={newsletter.confirmation_required ? "Required" : "Not required"} /><Fact label="Active profile" value={newsletter.active_profile_version ? `Version ${newsletter.active_profile_version}` : "Not configured"} /><Fact label="Last discovered" value={formatDateTime(newsletter.last_discovered_at)} /><Fact label="Last verified" value={formatDateTime(newsletter.last_verified_at)} /><Fact label="Versions" value={`${newsletter.profile_versions}`} /><Fact label="Signup score" value={newsletter.signup_score == null ? "No outcome yet" : `${newsletter.signup_score}% from ${newsletter.scored_count} account outcomes (${newsletter.confirmed_count} confirmed, ${newsletter.review_count} needing review)`} /></dl>{health && <div className={`newsletter-health-card ${health.bulk_blocked ? "attention" : "healthy"}`}><div><span className="detail-label">Automation health</span><strong>{health.bulk_blocked ? labelStatus(health.newsletter_status) : "Healthy"}</strong><small>{health.failure_count} failures · {health.manual_review_count} manual reviews in the last {health.window_size} outcomes</small></div><div className="newsletter-health-metrics"><span><b>{health.success_count}</b> success</span><span><b>{health.selector_failure_count}</b> selector issues</span></div></div>}<div className="newsletter-detail-section"><span className="detail-label">Matching metadata</span><div className="newsletter-detail-tags"><span>{newsletter.allowed_confirmation_domains.length} allowed domain{newsletter.allowed_confirmation_domains.length === 1 ? "" : "s"}</span><span>{newsletter.sender_patterns.length} sender pattern{newsletter.sender_patterns.length === 1 ? "" : "s"}</span><span>{newsletter.subject_patterns.length} subject pattern{newsletter.subject_patterns.length === 1 ? "" : "s"}</span></div></div></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close</button><button type="button" className="button primary" onClick={onStartLearning} disabled={!newsletter.signup_url && !newsletter.website_url} title={!newsletter.signup_url && !newsletter.website_url ? "Configure a signup URL or website URL first" : "Opens the signup dialog. If no form profile is saved yet, the dialog inspects the public signup page first."}><ArrowRight size={15} /> Start signup</button><button type="button" className="button secondary" onClick={onProfiles}><Waypoints size={15} /> View profiles</button>{canManage && <button type="button" className="button secondary" onClick={onDiscover} title="Optional website inspection; it does not gate signup"><Search size={15} /> Optional discovery</button>}{canManage && health && ["TEST_REQUIRED", "AUTOMATION_BROKEN"].includes(health.newsletter_status) && <button type="button" className="button primary" onClick={onRediscover}><RefreshCw size={15} /> Rediscover</button>}<button type="button" className="button primary" onClick={onEdit}><Pencil size={15} /> Edit newsletter</button></div></ModalShell>; }

function SignupTestDialog({ profile, newsletter, accounts, agentReady, agentReadinessMessage, onOpenSettings, profileLoading, discovering, canManage, pending, error, result, onClose, onStart, onDiscover }: { profile: NewsletterProfile | null; newsletter?: Newsletter; accounts: NewsletterAccountOption[]; agentReady: boolean; agentReadinessMessage: string; onOpenSettings: () => void; profileLoading: boolean; discovering: boolean; canManage: boolean; pending: boolean; error: string; result: NewsletterSignupTest | null; onClose: () => void; onStart: (accountId: string, testIdentity: Record<string, string>, saveIdentity: boolean, signupPassword?: string) => void; onDiscover: () => void }) {
  const [accountId, setAccountId] = useState(accounts[0]?.id ?? "");
  const fields = useMemo(() => {
    const raw = profile?.configuration?.fields;
    if (!Array.isArray(raw)) return [];
    const seen = new Set<string>();
    return raw.filter((item): item is Record<string, unknown> => {
      if (!item || typeof item !== "object") return false;
      const record = item as Record<string, unknown>;
      const key = String(record.selector || "").trim();
      const fieldKey = String(record.field_key || "").trim().toLowerCase();
      if (!key || !fieldKey || ["email", "consent", "terms", "signup_password"].includes(fieldKey) || seen.has(key)) return false;
      seen.add(key); return true;
    });
  }, [profile?.configuration]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [identityLoading, setIdentityLoading] = useState(false);
  const [generatingIdentity, setGeneratingIdentity] = useState(false);
  const [generateError, setGenerateError] = useState("");
  const [saveIdentity, setSaveIdentity] = useState(true);
  const [publisherPassword, setPublisherPassword] = useState("");
  const [showPublisherPassword, setShowPublisherPassword] = useState(false);
  useEffect(() => { setPublisherPassword(""); setShowPublisherPassword(false); }, [accountId]);
  useEffect(() => { if (result) { setPublisherPassword(""); setShowPublisherPassword(false); } }, [result]);
  const [generateTestValues, setGenerateTestValues] = useState(true);
  const applyGeneratedIdentity = async () => {
    if (!accountId || generatingIdentity) return;
    setGeneratingIdentity(true);
    setGenerateError("");
    try {
      // The backend derives one stable, non-secret profile from the account id,
      // so the same account always reads as the same person across runs.
      const generated = await newsletterRequests.generateAccountIdentity(accountId, { commit: saveIdentity });
      setValues((current) => {
        const next = { ...current };
        fields.forEach((field) => {
          const value = generated.values[String(field.field_key || "").trim().toLowerCase()];
          if (!value) return;
          const options = Array.isArray(field.options) ? field.options.map(String).filter(Boolean) : [];
          // A select only accepts option text it actually offers, so a value
          // outside the list is dropped rather than submitted and rejected.
          if (String(field.field_type || "").toLowerCase() === "select" && options.length && !options.includes(value)) return;
          next[`selector:${String(field.selector)}`] = value;
        });
        return next;
      });
    } catch (requestError) {
      setGenerateError((requestError as Error).message);
    } finally {
      setGeneratingIdentity(false);
    }
  };
  const syntheticValue = (field: Record<string, unknown>, options: string[]) => {
    const key = String(field.field_key || "").toLowerCase();
    const label = String(field.label || "").toLowerCase();
    const hint = `${key} ${label}`;
    if (String(field.field_type || "").toLowerCase() === "select" && options.length) {
      const placeholder = options.findIndex((option) => /choose|select|please|type|option|country \*/i.test(option));
      return options[placeholder >= 0 ? Math.min(placeholder + 1, options.length - 1) : 0] || "Test";
    }
    if (key.includes("first") || hint.includes("first name")) return "Amina";
    if (key.includes("last") || hint.includes("last name")) return "Morgan";
    if (key.includes("full") || hint.includes("your name") || key === "name") return "Amina Morgan";
    if (key.includes("address") || key.includes("street")) return "100 Market Street";
    if (key.includes("city") || key.includes("town")) return "Denver";
    if (key.includes("state") || key.includes("region") || key.includes("province")) return "Colorado";
    if (key.includes("postal") || key.includes("zip")) return "80202";
    if (key.includes("phone") || key.includes("mobile") || key.includes("tel")) return "3035550147";
    if (key.includes("company") || hint.includes("organization")) return "Northstar Test";
    if (key.includes("job") || hint.includes("title")) return "Operations Manager";
    return "Newsletter Test";
  };
  useEffect(() => {
    if (!accountId) return;
    let cancelled = false;
    setIdentityLoading(true);
    void newsletterRequests.getAccountIdentity(accountId).then((identity) => {
      if (!cancelled) {
        setValues(() => {
          const next = { ...identity.values };
          if (generateTestValues) {
            fields.forEach((field) => {
              const selector = String(field.selector);
              const inputKey = `selector:${selector}`;
              if (next[inputKey]?.trim() || ["consent", "terms"].includes(String(field.field_key).toLowerCase())) return;
              const options = Array.isArray(field.options) ? field.options.map(String).filter(Boolean) : [];
              next[inputKey] = syntheticValue(field, options);
            });
          }
          return next;
        });
      }
    }).catch(() => { /* an empty identity is valid for a new account */ }).finally(() => { if (!cancelled) setIdentityLoading(false); });
    return () => { cancelled = true; };
  }, [accountId, fields, generateTestValues]);
  const account = accounts.find((item) => item.id === accountId);
  const missingRequired = fields.filter((field) => field.required === true && !values[`selector:${String(field.selector)}`]?.trim());
  const canStart = Boolean(accountId) && !profileLoading && !discovering && !identityLoading && !pending && !result && missingRequired.length === 0;
  const signupDescription = profile ? `${newsletter?.name ?? profile.newsletter_id} · profile v${profile.version}. This opens one saved account profile and submits one public signup form.` : `${newsletter?.name ?? "Newsletter"} · no saved signup form. There is nothing to submit until a form profile exists or the bounded agent takes the form live.`;
  return <ModalShell title={profile ? "Test one newsletter signup" : "Learn this website and subscribe"} description={signupDescription} onClose={onClose} wide>
    <div className="newsletter-form">
      {error && <FormError message={error} />}
      {profileLoading && <div className="newsletter-discovery-progress" role="status"><span className="spinner" /><div><strong>Loading saved field mappings…</strong><span>The test will not start until every discovered field can be reviewed.</span></div></div>}
      {!profile && <div className="newsletter-profile-setup" role="status">{discovering ? <div className="newsletter-profile-setup-head"><span className="spinner" /><div><strong>Inspecting the public signup page…</strong><span>Reading the page once and saving the fields it finds as a candidate profile.</span></div></div> : <><div className="newsletter-profile-setup-head"><AlertCircle size={17} /><div><strong>{newsletter?.name ?? "This newsletter"} has no saved signup form yet</strong><span>A saved profile records which field takes the account email and which control submits the form. Nothing is saved for this newsletter, but a run does not need one: this page is read as the run types.</span></div></div><div className="newsletter-profile-setup-route"><div className="newsletter-profile-setup-route-top"><span className="detail-label">RECOMMENDED</span><span className="newsletter-control-policy">NO SETUP NEEDED</span></div><strong>Start now and read the live page</strong><span>Finds the email field and the submit control on the live page, types the approved account email, and fills the remaining fields from the account&apos;s synthetic profile. An approved publisher password can be supplied separately when the site requires one; verification and payment fields remain blocked. A verified success saves a replay skill, so the next run for this newsletter is faster.</span></div><div className="newsletter-profile-setup-route"><div className="newsletter-profile-setup-route-top"><span className="detail-label">OPTIONAL</span><span className="newsletter-control-policy">BEST FOR REPEAT RUNS</span></div><strong>Inspect the public signup page</strong><span>Reads the page once, finds the email field and the submit control, and saves them as a candidate profile you review right here. Worth doing when you will subscribe many accounts to this newsletter.{canManage ? "" : " Inspecting a public page needs newsletter management rights, so an administrator has to run this step."}</span><button type="button" className="button secondary small" onClick={onDiscover} disabled={pending || !newsletter || !canManage} title={!canManage ? "Inspecting a public page requires newsletter management rights" : undefined}><Search size={14} /> Inspect the signup page</button></div><div className={`newsletter-profile-setup-route${agentReady ? "" : " blocked"}`}><div className="newsletter-profile-setup-route-top"><span className="detail-label">ALTERNATIVE</span><span className={`newsletter-control-state ${agentReady ? "ready" : "attention"}`}>{agentReady ? "READY" : "NOT READY"}</span></div><strong>Let the bounded AI agent resolve the form live</strong><span>{agentReady ? "The agent is configured, so you can start without a profile: it resolves the live form, and a verified success saves a replay skill for later runs." : `${agentReadinessMessage} The agent is the only route that needs this setup; starting now above does not.`}</span>{!agentReady && <button type="button" className="text-button" onClick={onOpenSettings}>Open Settings <ArrowRight size={13} /></button>}</div></>}</div>}
      {!result && <>
        <div className="field"><label htmlFor="newsletter-test-account">Saved Gmail account <span className="required">Required</span></label><select id="newsletter-test-account" value={accountId} onChange={(event) => setAccountId(event.target.value)}>{accounts.length ? accounts.map((item) => <option key={item.id} value={item.id}>{item.email} · {item.group_name}</option>) : <option value="">No accounts available</option>}</select><small>The backend verifies that this account has an authenticated saved browser profile and that you can operate its group.</small></div>
        <div className="field"><label htmlFor="newsletter-publisher-password">Publisher signup password</label><div className="newsletter-password-control"><input id="newsletter-publisher-password" type={showPublisherPassword ? "text" : "password"} autoComplete="new-password" value={publisherPassword} onChange={(event) => setPublisherPassword(event.target.value)} maxLength={256} placeholder="Leave blank to use the saved password" /><button type="button" className="button secondary small" onClick={() => setShowPublisherPassword((current) => !current)} aria-label={showPublisherPassword ? "Hide publisher password" : "Show publisher password"} aria-pressed={showPublisherPassword}>{showPublisherPassword ? "Hide" : "Show"}</button></div><small>Stored encrypted for this account and newsletter. It is never the Gmail account password; leave blank if this site does not ask for one.</small></div>
        {fields.length > 0 && <><div className="form-divider">Approved signup values</div><p className="table-subtext">Saved values for this account are loaded automatically. Blank public fields are filled with clearly marked test details for this validation run.</p><div className="newsletter-identity-generate"><button type="button" className="button secondary small" onClick={() => void applyGeneratedIdentity()} disabled={!accountId || generatingIdentity || identityLoading}>{generatingIdentity ? <><span className="spinner" /> Generating…</> : <><Wand2 size={14} /> Generate details</>}</button><small>Creates a synthetic name, address, and contact profile derived from this account{accountId ? "" : " (choose an account first)"}. Saved values and your edits always win, and no email, password, or verification field is ever generated.</small></div>{generateError && <FormError message={generateError} />}<label className="newsletter-checkbox"><input type="checkbox" checked={generateTestValues} onChange={(event) => { const enabled = event.target.checked; setGenerateTestValues(enabled); if (enabled) setValues((current) => { const next = { ...current }; fields.forEach((field) => { const selector = String(field.selector); const inputKey = `selector:${selector}`; if (next[inputKey]?.trim() || ["consent", "terms"].includes(String(field.field_key).toLowerCase())) return; const options = Array.isArray(field.options) ? field.options.map(String).filter(Boolean) : []; next[inputKey] = syntheticValue(field, options); }); return next; }); }} /><span><strong>Fill blank fields with test details</strong><small>Uses non-secret validation data such as Amina Morgan and Northstar Test. Turn this off to require approved saved values.</small></span></label><div className="form-grid">{fields.map((field) => { const selector = String(field.selector); const fieldKey = String(field.field_key || selector); const options = Array.isArray(field.options) ? field.options.map(String).filter(Boolean) : []; const inputKey = `selector:${selector}`; return <div className="field" key={selector}><label htmlFor={`newsletter-value-${selector}`}>{String(field.label || fieldKey)} {field.required === true && <span className="required">Required</span>}</label>{String(field.field_type || "").toLowerCase() === "select" && options.length ? <select id={`newsletter-value-${selector}`} value={values[inputKey] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [inputKey]: event.target.value }))}><option value="">Choose a value</option>{options.map((option) => <option key={option} value={option}>{option}</option>)}</select> : <input id={`newsletter-value-${selector}`} value={values[inputKey] ?? ""} onChange={(event) => setValues((current) => ({ ...current, [inputKey]: event.target.value }))} placeholder={fieldKey.replaceAll("_", " ")} />}</div>; })}</div><label className="newsletter-checkbox"><input type="checkbox" checked={saveIdentity} onChange={(event) => setSaveIdentity(event.target.checked)} /><span><strong>Save these approved details for this account</strong><small>Stored encrypted and reused for later newsletter tests. Login credentials and secrets are never included.</small></span></label>{missingRequired.length > 0 && <div className="form-error" role="alert"><XCircle size={16} /><span>Complete the required public form fields before starting: {missingRequired.map((field) => String(field.label || field.field_key)).join(", ")}.</span></div>}</>}
        <div className="security-callout"><ShieldCheck size={16} /><div><strong>{profile ? "Signup and confirmation are chained safely" : agentReady ? "Bounded agent learning is protected" : "The live page supplies the fields"}</strong><span>{profile ? "The saved account profile submits one public signup. If confirmation is required, the signup browser closes first and the same account profile then searches Gmail and opens only an allowlisted HTTPS confirmation link." : agentReady ? "The agent may resolve only validated live controls. The app supplies approved account data, blocks unsafe or ambiguous actions, verifies factual success, and saves only redacted replay facts after success." : "With no saved form profile, the app reads the live form: it finds the email field and the submit control itself, types the approved account email, and fills the remaining fields from the account's synthetic profile. Publisher passwords are used only when you provide one for this account and website. Verification and payment controls are never filled by generation, and a required field it cannot fill honestly is reported by name instead of guessed."}</span></div></div>
      </>}
      {result && <div className={`newsletter-run-mode ${result.signup_mode === "ADAPTIVE_LEARNING" ? "adaptive" : result.signup_mode === "LIVE_FORM" ? "live" : "profile"}`} role="status"><strong>{result.signup_mode === "ADAPTIVE_LEARNING" ? "Agent-first learning run" : result.signup_mode === "LIVE_FORM" ? "Live-form run" : "Deterministic profile run"}</strong><span>{result.agent_status === "INVOKED" ? "The bounded agent selected validated live controls." : result.agent_status === "SKILL_REPLAYED" ? "A saved website skill was replayed; the agent was not needed." : result.agent_status === "NOT_USED" ? "The bounded agent was not used. Check Newsletter Agent settings and the selected signup mode." : result.agent_status === "NO_DECISION" ? "The bounded agent returned no safe decision; review is required." : result.agent_status === "READY" ? result.agent_detail || "The Newsletter Agent is ready, but it has not selected browser controls." : result.agent_status === "UNKNOWN" ? result.agent_detail || "No Newsletter Agent audit event has been recorded yet." : result.agent_detail || "Agent status is being reported by the session."}</span></div>}
      {result && <ExecutionTelemetry result={result} />}
      {result && <div className="newsletter-discovery-progress" role="status"><div><strong>{["IN_PROGRESS", "SUBMITTED", "WAITING_CONFIRMATION"].includes(result.result) && result.confirmation_required && result.auto_confirm ? "Signup submitted; Gmail confirmation running…" : result.result === "IN_PROGRESS" ? "Signup test running…" : `Result: ${labelStatus(result.result)}`}</strong><span>{result.session?.status_detail || result.session?.failure_reason || (result.result === "CONFIRMED" ? "The signup succeeded and Gmail confirmation was verified." : result.result === "WAITING_CONFIRMATION" ? "The signup succeeded; the isolated Gmail session is searching for the confirmation message." : result.result === "SUBMITTED" ? "The signup succeeded and no automatic Gmail confirmation is required." : result.result === "SKIPPED_ALREADY_SUBSCRIBED" ? "This account is already confirmed; no duplicate signup was started." : result.result === "SKIPPED_ALREADY_IN_PROGRESS" ? "This subscription is already submitted or awaiting confirmation; no duplicate signup was started." : result.result === "IN_PROGRESS" ? "Waiting for the browser session to report a factual result." : "Review the subscription record and session timeline." )}</span></div></div>}
      <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>{result?.result === "IN_PROGRESS" ? "Close and keep watching" : "Close"}</button>{!result && <button type="button" className="button primary" onClick={() => onStart(accountId, values, saveIdentity, publisherPassword || undefined)} disabled={!account || !canStart} title={!account ? "Choose a saved Gmail account first" : undefined}>{identityLoading ? "Loading details…" : profile ? "Start signup test" : agentReady ? "Start with the AI agent" : "Start signup"}</button>}</div>
    </div>
  </ModalShell>;
}

function ExecutionTelemetry({ result }: { result: NewsletterSignupTest }) {
  const mode = result.execution_mode || (result.signup_mode === "ADAPTIVE_LEARNING" ? "AGENT_LEARNING" : result.signup_mode === "LIVE_FORM" ? "LIVE_FORM" : "DETERMINISTIC");
  const modeCopy: Record<string, string> = {
    SKILL_REPLAY: "Saved website skill replayed",
    DETERMINISTIC: "Validated profile or DOM path",
    AGENT_LEARNING: "Bounded agent learning path",
    LIVE_FORM: "Live form resolved while typing",
    MANUAL: "Manual review boundary",
  };
  // The live route reads the page as it types, so it has no prerequisite to
  // name. Saying "started from saved profile configuration" for it would claim
  // a profile that does not exist.
  const signupCopy = result.signup_mode === "ADAPTIVE_LEARNING"
    ? "Started without a discovery prerequisite"
    : result.signup_mode === "LIVE_FORM"
      ? "Started with no saved profile; the live page supplied the fields"
      : "Started from saved profile configuration";
  const agentCopy: Record<string, string> = {
    INVOKED: "Agent selected validated controls",
    SKILL_REPLAYED: "Agent was not needed",
    NOT_USED: "No provider call was allowed",
    NO_DECISION: "No safe plan was returned",
    READY: "Agent was ready",
    UNKNOWN: "No agent event recorded",
  };
  return <section className={`newsletter-execution-telemetry ${mode.toLowerCase()}`} aria-label="Signup execution telemetry">
    <div className="newsletter-execution-heading"><div><span className="detail-label"><Activity size={12} /> RUN TELEMETRY</span><strong>Why this run took this path</strong></div><span className="newsletter-execution-mode">{labelStatus(mode)}</span></div>
    <div className="newsletter-execution-grid"><div><span>Execution mode</span><strong>{labelStatus(mode)}</strong><small>{modeCopy[mode] || "Bounded execution path"}</small></div><div><span>Signup mode</span><strong>{labelStatus(result.signup_mode)}</strong><small>{signupCopy}</small></div><div><span>Agent status</span><strong>{labelStatus(result.agent_status)}</strong><small>{agentCopy[result.agent_status] || "Status reported by the session"}</small></div><div><span>Outcome</span><strong>{labelStatus(result.result)}</strong><small>{result.confirmation_required ? "Gmail confirmation policy applied" : "No Gmail confirmation required"}</small></div></div>
    {result.agent_detail && <div className="newsletter-execution-detail"><ShieldCheck size={14} /><span>{result.agent_detail}</span></div>}
  </section>;
}

function ConfirmationTestDialog({ subscription, profile, newsletter, pending, error, result, onClose, onStart }: { subscription: NewsletterSubscription; profile: NewsletterProfile; newsletter?: Newsletter; pending: boolean; error: string; result: NewsletterConfirmationTest | null; onClose: () => void; onStart: () => void }) {
  const statusMessage = result?.result === "CONFIRMED" ? "The confirmation page displayed success evidence." : result?.result === "WAITING_CONFIRMATION" || result?.result === "IN_PROGRESS" ? "The saved Gmail profile is searching for the expected message and checking the allowlisted link." : result?.session?.status_detail || result?.session?.failure_reason || "Review the subscription and session timeline.";
  return <ModalShell title="Confirm one newsletter subscription" description={`${newsletter?.name ?? subscription.newsletter_name} · ${subscription.account_email} · profile v${profile.version}`} onClose={onClose} wide><div className="newsletter-form">{error && <FormError message={error} />}{!result && <div className="security-callout"><ShieldCheck size={16} /><div><strong>Safe confirmation boundary</strong><span>Only Gmail search, HTTPS links, and the newsletter's allowlisted domains are used. Unknown redirects stop for manual review.</span></div></div>}{result && <div className="newsletter-discovery-progress" role="status"><div><strong>{result.result === "IN_PROGRESS" || result.result === "WAITING_CONFIRMATION" ? "Confirmation test running…" : `Result: ${labelStatus(result.result)}`}</strong><span>{statusMessage}</span></div></div>}<div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>{pending ? "Keep watching" : "Close"}</button>{!result && <button type="button" className="button primary" onClick={onStart} disabled={pending}>{pending ? "Starting…" : "Start confirmation test"}</button>}</div></div></ModalShell>;
}

function DiscoveryDialog({ newsletter, result, pending, error, canManage, onClose, onRetry, onCreateProfile }: { newsletter: Newsletter; result: NewsletterDiscoveryResult | null; pending: boolean; error: string; canManage: boolean; onClose: () => void; onRetry: () => void; onCreateProfile: (candidate: NewsletterDiscoveryResult["candidates"][number]) => void }) {
  return <ModalShell title={`Discover ${newsletter.name}`} description="Inspect public pages only. No Gmail session, credential, cookie, signup, or confirmation action is performed." onClose={onClose} wide>
    <div className="newsletter-discovery-body">
      {pending && <div className="newsletter-discovery-progress" role="status"><span className="spinner" /><div><strong>Discovery running…</strong><span>Checking the configured page, likely signup paths, and relevant same-origin links.</span></div></div>}
      {error && <div className="form-error" role="alert"><XCircle size={16} /><div><strong>Discovery could not finish</strong><span>{error}</span></div><button type="button" className="text-button" onClick={onRetry}>Retry</button></div>}
      {result && !pending && <>
        <div className="newsletter-discovery-summary"><div><span className="detail-label">Public page reachability</span><strong>{result.website_reachable ? "Reachable" : "Not confirmed"}</strong></div><div><span className="detail-label">URLs inspected</span><strong>{result.inspected_url_count}</strong></div><div><span className="detail-label">Candidates found</span><strong>{result.candidates.length}</strong></div>{result.stealth_fetch_count ? <div><span className="detail-label">Browser re-reads</span><strong>{result.stealth_fetch_count}</strong></div> : null}</div>
        {result.warnings.length > 0 && <div className="newsletter-discovery-warning"><AlertCircle size={15} /><span>{result.warnings.join(" · ")}</span></div>}
        {result.candidates.length ? <div className="newsletter-discovery-candidates">{result.candidates.map((candidate, index) => { const fields = candidate.fields ?? []; const unresolved = fields.filter((field) => field.required === true && !["email", "consent", "terms"].includes(field.field_key)); return <article className="newsletter-discovery-card" key={`${candidate.url}-${index}`}><div className="newsletter-discovery-card-head"><div><span className="newsletter-rank">{index === 0 ? "RECOMMENDED" : `CANDIDATE ${index + 1}`}</span><strong>{candidate.url}</strong><small>{labelStatus(candidate.source)} · {candidate.page_reachable ? "reachable" : "not confirmed"}{candidate.fetch_mode === "stealth" ? " · read in a real browser" : ""}</small></div><span className="newsletter-confidence">{Math.round(candidate.confidence * 100)}%</span></div><div className="newsletter-discovery-checks"><span className={candidate.email_selector ? "ok" : "muted"}><Check size={13} /> Email {candidate.email_selector ? "detected" : "not found"}</span><span className={candidate.submit_selector ? "ok" : "muted"}><Check size={13} /> Submit {candidate.submit_selector ? "detected" : "not found"}</span><span className={candidate.consent_selector ? "warning" : "muted"}><ShieldCheck size={13} /> Consent {candidate.consent_selector ? "detected" : "not required"}</span><span className={unresolved.length ? "warning" : "ok"}><ShieldCheck size={13} /> {unresolved.length ? `${unresolved.length} required mapping${unresolved.length === 1 ? "" : "s"} need approval` : "No unresolved required mappings"}</span></div><div className="newsletter-discovery-selectors"><div><span>Email selector</span><code>{candidate.email_selector || "Not detected"}</code></div><div><span>Submit selector</span><code>{candidate.submit_selector || "Not detected"}</code></div></div>{fields.length > 0 && <div className="newsletter-discovery-fields"><span className="detail-label">Detected form fields</span><div className="newsletter-field-list">{fields.map((field) => <div className="newsletter-field-row" key={`${field.selector}-${field.field_key}`}><strong>{field.label || field.field_key}</strong><span>{field.field_key} · {field.field_type}</span><span>{field.required === true ? "Required" : field.required === false ? "Optional" : "Unknown"}</span><code>{field.selector}</code></div>)}</div></div>}<div className="newsletter-discovery-evidence">{candidate.evidence.map((item) => <span key={item}>{item}</span>)}</div><div className="newsletter-discovery-card-footer"><span>Candidate only · validation required before activation</span>{canManage && <button type="button" className="button primary small" disabled={!candidate.email_selector} onClick={() => onCreateProfile(candidate)}><Plus size={14} /> Create candidate profile</button>}</div></article>; })}</div> : <EmptyState icon={<Search size={20} />} title="No signup form candidate found" copy="The page was reachable, but no bounded email-and-submit candidate was detected. Review the public page or provide a more specific signup URL." />}
      </>}
    </div>
    <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Close</button></div>
  </ModalShell>;
}

function ProfileDetail({ profile, newsletter, canManage, onClose, onAction, onSaveEntryStep }: { profile: NewsletterProfile; newsletter?: Newsletter; canManage: boolean; onClose: () => void; onAction: (profile: NewsletterProfile, action: "promote" | "reject" | "rollback") => void; onSaveEntryStep: (profile: NewsletterProfile, entrySelector: string) => Promise<void> }) { const groups = new Map<string, [string, unknown][]>(); Object.entries(profile.configuration ?? {}).forEach(([key, value]) => { const prefix = key.split(/[._-]/)[0] || "Configuration"; groups.set(prefix, [...(groups.get(prefix) ?? []), [key, value]]); }); const [entrySelector, setEntrySelector] = useState(String((profile.configuration ?? {}).entry_selector ?? "")); const [entryError, setEntryError] = useState(""); const [entryPending, setEntryPending] = useState(false); const submitEntryStep = async (event: FormEvent) => { event.preventDefault(); setEntryPending(true); setEntryError(""); try { await onSaveEntryStep(profile, entrySelector.trim()); } catch (requestError) { setEntryError((requestError as Error).message); } finally { setEntryPending(false); } }; return <ModalShell title={`Profile v${profile.version}`} description={`${newsletter?.name ?? profile.newsletter_id} · reviewed configuration`} onClose={onClose}><div className="profile-detail-head"><StatusBadge value={profile.status} /><span>{Math.round(profile.confidence * 100)}% confidence</span></div>{groups.size ? <div className="profile-config-groups">{Array.from(groups.entries()).map(([group, values]) => <section key={group}><span className="detail-label">{labelStatus(group)}</span>{values.map(([key, value]) => <div className="profile-config-row" key={key}><strong>{key}</strong><span>{formatConfigurationValue(value)}</span></div>)}</section>)}</div> : <div className="newsletter-empty compact"><FileCog size={20} /><strong>Not configured</strong><span>This profile has no structured configuration values.</span></div>}{canManage && <form id="profile-entry-form" className="newsletter-form" onSubmit={submitEntryStep} noValidate><div className="form-divider">Entry step</div>{entryError && <FormError message={entryError} />}<div className="field"><label htmlFor="profile-entry-selector">Selector for the step that reveals the email field</label><input id="profile-entry-selector" value={entrySelector} onChange={(event) => setEntrySelector(event.target.value)} placeholder=".newsletter-card:first-child" maxLength={300} /><small>Some signup pages show a grid of newsletter cards and only render the email field after one is chosen. Enter the selector of the control to click once, then the app fills the form it reveals. Leave empty when the email field is already on the landing page.</small></div><div className="security-callout"><ShieldCheck size={16} /><div><strong>Reviewed, versioned step</strong><span>A selector is configuration, not a credential. Saving creates a new candidate version to test; the version you are viewing is kept as it was tested.</span></div></div></form>}<div className="profile-detail-timeline"><Fact label="Discovered" value={formatDateTime(profile.discovered_at)} /><Fact label="Tested" value={formatDateTime(profile.tested_at)} /><Fact label="Promoted" value={formatDateTime(profile.promoted_at)} /><Fact label="Last success" value={formatDateTime(profile.last_success_at)} /><Fact label="Last failure" value={formatDateTime(profile.last_failure_at)} /></div><div className="modal-footer"><button type="button" className="button secondary" onClick={onClose}>Close</button>{canManage && <button type="submit" form="profile-entry-form" className="button primary" disabled={entryPending}>{entryPending ? "Saving…" : "Save entry step"}</button>}{["DISCOVERED", "CANDIDATE", "TEST_FAILED"].includes(profile.status) && <button type="button" className="button secondary" onClick={() => onAction(profile, "reject")}>Reject</button>}{profile.status === "TEST_PASSED" && <button type="button" className="button primary" onClick={() => onAction(profile, "promote")}>Promote profile</button>}{["SUPERSEDED", "TEST_PASSED"].includes(profile.status) && <button type="button" className="button secondary" onClick={() => onAction(profile, "rollback")}><RotateCcw size={15} /> Rollback</button>}</div></ModalShell>; }

function formatConfigurationValue(value: unknown): string { if (value === null || value === undefined || value === "") return "Not configured"; if (typeof value === "string") return value; try { return JSON.stringify(value); } catch { return "Configured"; } }

function Fact({ label, value }: { label: string; value: string }) { return <div><dt>{label}</dt><dd title={value}>{value}</dd></div>; }
function ModalShell({ title, description, onClose, children, wide = false }: { title: string; description: string; onClose: () => void; children: React.ReactNode; wide?: boolean }) { return <div className="modal-backdrop newsletter-backdrop" role="presentation"><section className={`modal newsletter-modal ${wide ? "newsletter-modal-wide" : ""}`} role="dialog" aria-modal="true" aria-labelledby="newsletter-modal-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> NEWSLETTER WORKSPACE</div><h2 id="newsletter-modal-title">{title}</h2><p>{description}</p></div><button type="button" className="icon-button" aria-label="Close dialog" onClick={onClose}><X size={18} /></button></div>{children}</section></div>; }
function ModalFooter({ onClose, pending, disabled = false, label }: { onClose: () => void; pending: boolean; disabled?: boolean; label: string }) { return <div className="modal-footer"><button type="button" className="button secondary" onClick={onClose} disabled={pending}>Cancel</button><button type="submit" className="button primary" disabled={pending || disabled}>{pending ? "Saving…" : label}</button></div>; }
function FormError({ message }: { message: string }) { return <div className="form-error" role="alert"><XCircle size={16} /><span>{message}</span></div>; }
function ConfirmDialog({ state, onCancel }: { state: ConfirmState; onCancel: () => void }) { return <div className="modal-backdrop newsletter-backdrop" role="presentation"><section className="modal newsletter-confirm" role="alertdialog" aria-modal="true" aria-labelledby="newsletter-confirm-title"><div className="modal-header"><div><div className="eyebrow"><span className="eyebrow-line" /> CONFIRM CHANGE</div><h2 id="newsletter-confirm-title">{state.title}</h2><p>{state.description}</p></div><button type="button" className="icon-button" aria-label="Close confirmation" onClick={onCancel}><X size={18} /></button></div><div className="modal-footer"><button type="button" className="button secondary" autoFocus onClick={onCancel}>Cancel</button><button type="button" className="button primary" onClick={state.onConfirm}>{state.action}</button></div></section></div>; }
