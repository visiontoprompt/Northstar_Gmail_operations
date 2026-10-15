export type NewsletterGroup = {
  id: string;
  organization_id: string;
  name: string;
  description: string | null;
  status: string;
  newsletter_count: number;
  created_at: string;
  updated_at: string;
};

export type Newsletter = {
  id: string;
  organization_id: string;
  group_id: string;
  group_name: string;
  name: string;
  website_url: string | null;
  signup_url: string | null;
  status: string;
  registry_status: string;
  failure_reason: string | null;
  failure_at: string | null;
  confirmation_required: boolean;
  allowed_confirmation_domains: string[];
  sender_patterns: string[];
  subject_patterns: string[];
  discovery_status: string;
  last_discovered_at: string | null;
  last_verified_at: string | null;
  profile_versions: number;
  active_profile_version: number | null;
  signup_score: number | null;
  subscribed_count: number;
  confirmed_count: number;
  review_count: number;
  scored_count: number;
  created_at: string;
  updated_at: string;
};

export type NewsletterProfile = {
  id: string;
  organization_id: string;
  newsletter_id: string;
  version: number;
  status: string;
  confidence: number;
  configuration_keys: string[];
  configuration?: Record<string, unknown>;
  discovered_at: string | null;
  tested_at: string | null;
  promoted_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  created_at: string;
  updated_at: string;
};

export type NewsletterSkill = {
  newsletter_id: string;
  status: string;
  version: number | null;
  confidence: number | null;
  website_host: string | null;
  action_count: number;
  use_count: number;
  success_count: number;
  failure_count: number;
  last_used_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
};

export type NewsletterAgentChainEntry = {
  priority: number;
  name: string;
  enabled: boolean;
  provider: "openrouter" | "openai";
  model: string;
  min_confidence: number;
  max_calls: number;
  timeout_seconds: number;
  vision_enabled: boolean;
  health?: string;
  consecutive_failures?: number;
  last_latency_ms?: number;
  last_error?: string;
};

export type NewsletterAgentSettings = {
  newsletter_agent_enabled: boolean;
  newsletter_agent_live_execution_enabled: boolean;
  newsletter_agent_learning_mode: boolean;
  newsletter_agent_shadow_mode: boolean;
  openrouter_api_key_configured: boolean;
  openai_api_key_configured: boolean;
  provider_keys?: Partial<Record<NewsletterAgentChainEntry["provider"], boolean>>;
  newsletter_agent_chain: NewsletterAgentChainEntry[];
};

export type NewsletterExecutionMode = "SKILL_REPLAY" | "DETERMINISTIC" | "AGENT_LEARNING" | "LIVE_FORM" | "MANUAL";
export type NewsletterAgentStatus = "UNKNOWN" | "NOT_USED" | "READY" | "INVOKED" | "SKILL_REPLAYED" | "NO_DECISION";

export type NewsletterField = {
  field_key: string;
  field_type: string;
  selector: string;
  label: string;
  placeholder: string;
  autocomplete: string;
  required: boolean | null;
  visible: boolean;
  enabled: boolean;
  confidence: number;
  source: string;
  options?: string[];
};

export type NewsletterDiscoveryCandidate = {
  url: string;
  source: string;
  confidence: number;
  page_reachable: boolean;
  http_status: number | null;
  /** "normal" for the plain fetcher, "stealth" when a challenge forced a browser-backed re-read. */
  fetch_mode?: "normal" | "stealth";
  email_selector: string | null;
  submit_selector: string | null;
  consent_selector: string | null;
  success_signals: string[];
  failure_signals: string[];
  confirmation_domain_hints: string[];
  evidence: string[];
  fields: NewsletterField[];
  configuration: Record<string, unknown>;
};

export type NewsletterDiscoveryResult = {
  newsletter_id: string;
  started_at: string;
  completed_at: string;
  website_reachable: boolean;
  discovery_blocked: boolean;
  blocked_reasons: string[];
  inspected_url_count: number;
  /** Number of pages that needed a browser-backed re-read. */
  stealth_fetch_count?: number;
  recommended_candidate_url: string | null;
  candidates: NewsletterDiscoveryCandidate[];
  warnings: string[];
};

export type NewsletterBulkDiscoveryItem = {
  newsletter_id: string;
  name: string;
  website_url: string | null;
  discovery_status: string;
  registry_status: string;
  failure_reason: string | null;
  inspected_url_count: number;
  candidate_count: number;
};

export type NewsletterBulkDiscoveryResult = {
  started_at: string;
  completed_at: string;
  requested_count: number;
  completed_count: number;
  skipped_count: number;
  discovered_count: number;
  blocked_count: number;
  failed_count: number;
  items: NewsletterBulkDiscoveryItem[];
};

export type NewsletterHealth = {
  newsletter_id: string;
  status: string;
  newsletter_status: string;
  active_profile_version: number | null;
  window_size: number;
  success_count: number;
  failure_count: number;
  manual_review_count: number;
  selector_failure_count: number;
  test_required_threshold: number;
  automation_broken_threshold: number;
  bulk_blocked: boolean;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_manual_review_at: string | null;
  last_detail: string | null;
};

export type NewsletterSubscription = {
  id: string;
  account_id: string;
  account_email: string;
  account_group_id: string;
  account_group_name: string;
  newsletter_id: string;
  newsletter_name: string;
  newsletter_group_id: string;
  newsletter_group_name: string;
  state: string;
  submitted_at: string | null;
  confirmed_at: string | null;
  last_checked_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type NewsletterAccountOption = {
  id: string;
  email: string;
  group_id: string;
  group_name: string;
  profile_status?: string;
};

export type NewsletterAccountIdentity = {
  account_id: string;
  values: Record<string, string>;
  updated_at: string | null;
};

export type NewsletterIdentityGenerate = {
  account_id: string;
  values: Record<string, string>;
  /** Which keys came from the generator rather than from saved or override values. */
  generated_keys: string[];
  committed: boolean;
  updated_at: string | null;
};

export type NewsletterSignupTest = {
  session: { id: string; state: string; operation_status?: string; status_detail?: string | null; failure_reason: string | null; account_email: string } | null;
  newsletter_id: string;
  profile_id: string;
  subscription_id: string;
  result: "IN_PROGRESS" | "SUBMITTED" | "WAITING_CONFIRMATION" | "CONFIRMED" | "FAILED" | "MANUAL_REVIEW" | "SKIPPED" | "SKIPPED_ALREADY_SUBSCRIBED" | "SKIPPED_ALREADY_IN_PROGRESS";
  signup_mode: "PROFILE" | "ADAPTIVE_LEARNING" | "LIVE_FORM";
  execution_mode: NewsletterExecutionMode;
  agent_status: NewsletterAgentStatus;
  agent_detail: string | null;
  confirmation_required: boolean;
  auto_confirm: boolean;
  recipient_mode: "SAVED_ACCOUNTS" | "EXTERNAL";
  external_recipient_count: number;
  confirmation_session_id: string | null;
};

export type NewsletterConfirmationTest = {
  session: { id: string; state: string; operation_status?: string; status_detail?: string | null; failure_reason: string | null; account_email: string };
  newsletter_id: string;
  profile_id: string;
  subscription_id: string;
  result: "IN_PROGRESS" | "WAITING_CONFIRMATION" | "CONFIRMED" | "FAILED" | "MANUAL_REVIEW" | "SKIPPED";
};

export type NewsletterJob = {
  id: string;
  organization_id: string;
  newsletter_id: string | null;
  newsletter_name: string | null;
  newsletter_group_id: string | null;
  newsletter_group_name: string | null;
  account_group_id: string;
  account_group_name: string;
  status: string;
  concurrency_limit: number;
  retry_policy_json: Record<string, unknown>;
  randomize_newsletters: boolean;
  random_newsletter_count: number | null;
  auto_confirm: boolean;
  recipient_mode: "SAVED_ACCOUNTS" | "EXTERNAL";
  external_recipient_count: number;
  headless_browser: boolean;
  created_by: string;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  updated_at: string;
  total_tasks: number;
  task_counts: Record<string, number>;
  tasks: NewsletterTask[];
};

export type NewsletterTask = {
  id: string;
  account_id: string;
  account_email: string;
  newsletter_id: string;
  newsletter_name: string;
  state: string;
  attempts: number;
  profile_version: number | null;
  worker_id: string | null;
  browser_session_id: string | null;
  heartbeat_at: string | null;
  last_error: string | null;
  created_at: string;
  updated_at: string;
};

export type AuditEvent = {
  id: string;
  action: string;
  target_type: string;
  target_id: string;
  result: string;
  metadata: Record<string, unknown>;
  created_at: string;
};

export type AccountGroupOption = {
  id: string;
  name: string;
  account_count: number;
};

export type NewsletterJobCreate = {
  newsletter_id?: string;
  newsletter_group_id?: string;
  account_group_id: string;
  concurrency_limit: number;
  retry_policy_json: Record<string, number | boolean | string | string[]>;
  auto_confirm: boolean;
};

export type NewsletterPageProps = {
  accountGroups: AccountGroupOption[];
  accounts: NewsletterAccountOption[];
  canManage: boolean;
  canOperate: boolean;
  refreshKey: number;
  onNotify: (tone: "success" | "error" | "info", message: string) => void;
  onOpenSettings: () => void;
  initialAccountIds?: string[];
  onClearInitialAccountIds?: () => void;
};

export type NewsletterJobAccountTarget = {
  id: string;
  email: string;
  group_id: string;
  group_name: string;
  login_status: string;
  profile_status: string;
};
