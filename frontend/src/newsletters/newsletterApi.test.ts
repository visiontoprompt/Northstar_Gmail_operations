import { afterEach, describe, expect, it, vi } from "vitest";
import { newsletterRequests } from "./newsletterApi";

const response = (body: unknown, status = 200) => ({
  ok: status >= 200 && status < 300,
  status,
  headers: { get: () => "application/json" },
  text: async () => JSON.stringify(body),
  json: async () => body,
});

describe("newsletter API contract", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("loads registry groups with the workspace session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response([{ id: "group_1", name: "Technology", status: "ACTIVE", description: null, newsletter_count: 0, created_at: null, updated_at: null }]));
    vi.stubGlobal("fetch", fetchMock);

    await expect(newsletterRequests.listGroups()).resolves.toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/newsletter-groups", expect.objectContaining({ credentials: "include" }));
  });

  it("surfaces a clear forbidden error for protected registry mutations", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ detail: "Admin access required." }, 403)));

    await expect(newsletterRequests.createGroup({ name: "Technology", description: null })).rejects.toThrow("Admin access required.");
  });

  it("starts one sequential discovery request for every configured website", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ requested_count: 2, completed_count: 2, skipped_count: 0, discovered_count: 1, blocked_count: 1, failed_count: 0, items: [] }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(newsletterRequests.discoverAllNewsletters()).resolves.toMatchObject({ requested_count: 2, blocked_count: 1 });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/newsletters/discover-all", expect.objectContaining({ method: "POST", credentials: "include" }));
  });

  it("keeps the execution mode and agent status from an adaptive signup result", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({
      session: null,
      newsletter_id: "newsletter_1",
      profile_id: "",
      subscription_id: "subscription_1",
      result: "IN_PROGRESS",
      signup_mode: "ADAPTIVE_LEARNING",
      execution_mode: "AGENT_LEARNING",
      agent_status: "INVOKED",
      agent_detail: "Main selected validated controls.",
      confirmation_required: false,
      auto_confirm: true,
      confirmation_session_id: null,
    }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(newsletterRequests.startSignupTest("newsletter_1", { account_id: "account_1", test_identity: {}, auto_confirm: true })).resolves.toMatchObject({ execution_mode: "AGENT_LEARNING", agent_status: "INVOKED" });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/newsletters/newsletter_1/signup-test", expect.objectContaining({ method: "POST" }));
  });

  it("accepts OpenAI-only readiness from workspace settings", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ newsletter_agent_enabled: true, newsletter_agent_live_execution_enabled: true, newsletter_agent_learning_mode: true, newsletter_agent_shadow_mode: false, openrouter_api_key_configured: false, openai_api_key_configured: true, newsletter_agent_chain: [{ priority: 1, name: "Main", enabled: true, provider: "openai", model: "gpt-4o-mini", min_confidence: 0.75, max_calls: 1, timeout_seconds: 20, vision_enabled: false }] })));

    await expect(newsletterRequests.getNewsletterAgentSettings()).resolves.toMatchObject({ openrouter_api_key_configured: false, openai_api_key_configured: true });
  });

  it("previews generated signup details without committing them by default", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ account_id: "account_1", values: { first_name: "Amina", last_name: "Walker" }, generated_keys: ["first_name", "last_name"], committed: false, updated_at: null }));
    vi.stubGlobal("fetch", fetchMock);

    await expect(newsletterRequests.generateAccountIdentity("account_1")).resolves.toMatchObject({ committed: false, generated_keys: ["first_name", "last_name"] });
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/accounts/account_1/newsletter-identity/generate", expect.objectContaining({ method: "POST", body: "{}", credentials: "include" }));
  });

  it("commits generated details only when the caller asks for it", async () => {
    const fetchMock = vi.fn().mockResolvedValue(response({ account_id: "account_1", values: {}, generated_keys: [], committed: true, updated_at: null }));
    vi.stubGlobal("fetch", fetchMock);

    await newsletterRequests.generateAccountIdentity("account_1", { commit: true, overrides: { city: "Portland" } });

    expect(fetchMock).toHaveBeenCalledWith("/api/v1/accounts/account_1/newsletter-identity/generate", expect.objectContaining({ body: JSON.stringify({ commit: true, overrides: { city: "Portland" } }) }));
  });

  it("reports how a discovery page was read", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response({ newsletter_id: "newsletter_1", started_at: null, completed_at: null, website_reachable: true, discovery_blocked: false, blocked_reasons: [], inspected_url_count: 2, stealth_fetch_count: 1, recommended_candidate_url: null, candidates: [{ url: "https://news.example.com/subscribe", source: "scrapling", confidence: 0.8, page_reachable: true, http_status: 200, fetch_mode: "stealth", email_selector: "#email", submit_selector: "button", consent_selector: null, success_signals: [], failure_signals: [], confirmation_domain_hints: [], evidence: [], fields: [], configuration: {} }], warnings: [] })));

    await expect(newsletterRequests.discoverNewsletter("newsletter_1")).resolves.toMatchObject({ stealth_fetch_count: 1, candidates: [expect.objectContaining({ fetch_mode: "stealth" })] });
  });
});
