import type {
  AuditEvent,
  Newsletter,
  NewsletterGroup,
  NewsletterDiscoveryResult,
  NewsletterBulkDiscoveryResult,
  NewsletterHealth,
  NewsletterJob,
  NewsletterJobCreate,
  NewsletterProfile,
  NewsletterSkill,
  NewsletterSubscription,
  NewsletterSignupTest,
  NewsletterConfirmationTest,
  NewsletterAccountIdentity,
  NewsletterIdentityGenerate,
  NewsletterAgentSettings,
  NewsletterJobAccountTarget,
} from "./newsletterTypes";

export const newsletterApi = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    credentials: "include",
    headers: { "Content-Type": "application/json", ...(options?.headers ?? {}) },
    ...options,
  });
  const body = await response.text();
  let payload: unknown = undefined;
  if (body.trim()) {
    try {
      payload = JSON.parse(body);
    } catch {
      if (!response.ok) throw new Error("The server returned an invalid response.");
    }
  }
  if (!response.ok) {
    const detail = payload && typeof payload === "object" && "detail" in payload ? (payload as { detail?: unknown }).detail : undefined;
    throw new Error(typeof detail === "string" ? detail : response.status === 403 ? "You do not have permission to change this newsletter registry." : "The request could not be completed.");
  }
  return payload as T;
};

export const newsletterRequests = {
  listGroups: () => newsletterApi<NewsletterGroup[]>("/api/v1/newsletter-groups"),
  seedDirectory: () => newsletterApi<{ requested_count: number; created_count: number; existing_count: number; preserved_aliases: { existing: string; canonical: string }[]; groups: NewsletterGroup[] }>("/api/v1/newsletter-groups/seed-directory", { method: "POST" }),
  importCatalog: () => newsletterApi<{ requested_count: number; created_count: number; existing_count: number; skipped_count: number; created_groups: number; groups: NewsletterGroup[] }>("/api/v1/newsletters/import-catalog", { method: "POST" }),
  createGroup: (payload: { name: string; description: string | null }) => newsletterApi<NewsletterGroup>("/api/v1/newsletter-groups", { method: "POST", body: JSON.stringify(payload) }),
  updateGroup: (id: string, payload: { name?: string; description?: string | null; status?: string }) => newsletterApi<NewsletterGroup>(`/api/v1/newsletter-groups/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  setGroupStatus: (id: string, status: "enable" | "disable") => newsletterApi<NewsletterGroup>(`/api/v1/newsletter-groups/${id}/${status}`, { method: "POST" }),
  listNewsletters: () => newsletterApi<Newsletter[]>("/api/v1/newsletters"),
  createNewsletter: (payload: Record<string, unknown>) => newsletterApi<Newsletter>("/api/v1/newsletters", { method: "POST", body: JSON.stringify(payload) }),
  updateNewsletter: (id: string, payload: Record<string, unknown>) => newsletterApi<Newsletter>(`/api/v1/newsletters/${id}`, { method: "PATCH", body: JSON.stringify(payload) }),
  setNewsletterStatus: (id: string, status: "enable" | "disable") => newsletterApi<Newsletter>(`/api/v1/newsletters/${id}/${status}`, { method: "POST" }),
  discoverNewsletter: (id: string) => newsletterApi<NewsletterDiscoveryResult>(`/api/v1/newsletters/${id}/discover`, { method: "POST" }),
  discoverAllNewsletters: () => newsletterApi<NewsletterBulkDiscoveryResult>("/api/v1/newsletters/discover-all", { method: "POST" }),
  getNewsletterHealth: (id: string) => newsletterApi<NewsletterHealth>(`/api/v1/newsletters/${id}/health`),
  getNewsletterSkill: (id: string) => newsletterApi<NewsletterSkill>(`/api/v1/newsletters/${id}/skill`),
  listNewsletterSkills: () => newsletterApi<NewsletterSkill[]>("/api/v1/newsletter-skills"),
  getNewsletterAgentSettings: () => newsletterApi<NewsletterAgentSettings>("/api/v1/settings"),
  rediscoverNewsletter: (id: string) => newsletterApi<{ newsletter_id: string; discovery: NewsletterDiscoveryResult; health: NewsletterHealth; candidate_profile: NewsletterProfile | null }>(`/api/v1/newsletters/${id}/rediscover`, { method: "POST" }),
  createProfile: (payload: { newsletter_id: string; status: "CANDIDATE"; confidence: number; configuration: Record<string, unknown> }) => newsletterApi<NewsletterProfile>("/api/v1/newsletter-profiles", { method: "POST", body: JSON.stringify(payload) }),
  listProfiles: (newsletterId?: string) => newsletterApi<NewsletterProfile[]>(newsletterId ? `/api/v1/newsletter-profiles?newsletter_id=${encodeURIComponent(newsletterId)}` : "/api/v1/newsletter-profiles"),
  getProfile: (id: string) => newsletterApi<NewsletterProfile>(`/api/v1/newsletter-profiles/${id}`),
  updateProfile: (id: string, configuration: Record<string, unknown>) => newsletterApi<NewsletterProfile>(`/api/v1/newsletter-profiles/${id}`, { method: "PATCH", body: JSON.stringify({ configuration }) }),
  promoteProfile: (id: string) => newsletterApi<NewsletterProfile>(`/api/v1/newsletter-profiles/${id}/promote`, { method: "POST" }),
  rejectProfile: (id: string) => newsletterApi<NewsletterProfile>(`/api/v1/newsletter-profiles/${id}/reject`, { method: "POST" }),
  rollbackProfile: (id: string) => newsletterApi<NewsletterProfile>(`/api/v1/newsletter-profiles/${id}/rollback`, { method: "POST" }),
  startSignupTest: (newsletterId: string, payload: { account_id: string; profile_id?: string; test_identity: Record<string, string>; signup_password?: string; auto_confirm: boolean }) => newsletterApi<NewsletterSignupTest>(`/api/v1/newsletters/${newsletterId}/signup-test`, { method: "POST", body: JSON.stringify(payload) }),
  getSignupTest: (sessionId: string) => newsletterApi<NewsletterSignupTest>(`/api/v1/newsletter-signup-tests/${sessionId}`),
  getAccountIdentity: (accountId: string) => newsletterApi<NewsletterAccountIdentity>(`/api/v1/accounts/${accountId}/newsletter-identity`),
  saveAccountIdentity: (accountId: string, values: Record<string, string>) => newsletterApi<NewsletterAccountIdentity>(`/api/v1/accounts/${accountId}/newsletter-identity`, { method: "PUT", body: JSON.stringify({ values }) }),
  generateAccountIdentity: (accountId: string, payload: { commit?: boolean; overrides?: Record<string, string> } = {}) => newsletterApi<NewsletterIdentityGenerate>(`/api/v1/accounts/${accountId}/newsletter-identity/generate`, { method: "POST", body: JSON.stringify(payload) }),
  startConfirmationTest: (newsletterId: string, payload: { account_id: string; profile_id: string; subscription_id: string }) => newsletterApi<NewsletterConfirmationTest>(`/api/v1/newsletters/${newsletterId}/confirm-test`, { method: "POST", body: JSON.stringify(payload) }),
  getConfirmationTest: (sessionId: string) => newsletterApi<NewsletterConfirmationTest>(`/api/v1/newsletter-confirmation-tests/${sessionId}`),
  listSubscriptions: () => newsletterApi<{ items: NewsletterSubscription[]; total: number }>("/api/v1/newsletter-subscriptions?limit=500"),
  listJobs: () => newsletterApi<{ items: NewsletterJob[]; total: number }>("/api/v1/newsletter-jobs?limit=200"),
  getJobAccountTargets: (ids: string[]) => newsletterApi<NewsletterJobAccountTarget[]>("/api/v1/newsletter-jobs/account-targets", { method: "POST", body: JSON.stringify({ account_ids: ids }) }),
  getJob: (id: string) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}`),
  deleteJob: (id: string) => newsletterApi<void>(`/api/v1/newsletter-jobs/${id}`, { method: "DELETE" }),
  deleteJobs: (ids: string[]) => newsletterApi<{ deleted_count: number; deleted_ids: string[] }>("/api/v1/newsletter-jobs", { method: "DELETE", body: JSON.stringify({ job_ids: ids }) }),
  createJob: (payload: NewsletterJobCreate) => newsletterApi<NewsletterJob>("/api/v1/newsletter-jobs", { method: "POST", body: JSON.stringify(payload) }),
  startJob: (id: string) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/start`, { method: "POST" }),
  dispatchJob: (id: string) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/dispatch`, { method: "POST" }),
  pauseJob: (id: string) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/pause`, { method: "POST" }),
  resumeJob: (id: string) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/resume`, { method: "POST" }),
  cancelJob: (id: string) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/cancel`, { method: "POST" }),
  retryFailed: (id: string, taskIds: string[] = []) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/retry-failed`, { method: "POST", body: JSON.stringify({ task_ids: taskIds }) }),
  skipSelected: (id: string, taskIds: string[]) => newsletterApi<NewsletterJob>(`/api/v1/newsletter-jobs/${id}/skip-selected`, { method: "POST", body: JSON.stringify({ task_ids: taskIds }) }),
  listActivity: () => newsletterApi<AuditEvent[]>("/api/v1/audit-events?limit=100"),
};
