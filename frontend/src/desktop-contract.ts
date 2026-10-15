export type DesktopAccountFilter = "ALL" | "AUTHENTICATED" | "VERIFICATION_REQUIRED" | "NEVER_LOGGED_IN" | "BROWSER_OPEN";

export type DesktopAccountSummary = {
  id: string;
  email: string;
  login_status: string;
};

export type DesktopAccountCounts = Record<DesktopAccountFilter, number>;

export type DesktopLiveSession = { id: string; account_id: string; browser_alive?: boolean };

/** Return at most one live browser per selected account inside the visible group/filter scope. */
export function selectedLiveSessions<T extends DesktopLiveSession>(
  sessions: readonly T[],
  selectedAccountIds: ReadonlySet<string>,
  eligibleAccountIds: ReadonlySet<string>,
): T[] {
  const seenAccounts = new Set<string>();
  return sessions.filter((session) => {
    if (!session.browser_alive || !selectedAccountIds.has(session.account_id) || !eligibleAccountIds.has(session.account_id) || seenAccounts.has(session.account_id)) return false;
    seenAccounts.add(session.account_id);
    return true;
  });
}

export function searchDesktopAccounts<T extends DesktopAccountSummary>(accounts: T[], query: string): T[] {
  const normalized = query.trim().toLocaleLowerCase();
  return normalized ? accounts.filter((account) => account.email.toLocaleLowerCase().includes(normalized)) : accounts;
}

export function countDesktopAccounts<T extends DesktopAccountSummary>(accounts: T[], liveAccountIds: ReadonlySet<string>): DesktopAccountCounts {
  return {
    ALL: accounts.length,
    AUTHENTICATED: accounts.filter((account) => account.login_status === "AUTHENTICATED").length,
    VERIFICATION_REQUIRED: accounts.filter((account) => account.login_status === "VERIFICATION_REQUIRED").length,
    NEVER_LOGGED_IN: accounts.filter((account) => ["NOT_STARTED", "NEVER_LOGGED_IN", "REQUIRED"].includes(account.login_status)).length,
    BROWSER_OPEN: accounts.filter((account) => liveAccountIds.has(account.id)).length,
  };
}

export function filterDesktopAccounts<T extends DesktopAccountSummary>(accounts: T[], filter: DesktopAccountFilter, liveAccountIds: ReadonlySet<string>): T[] {
  if (filter === "AUTHENTICATED") return accounts.filter((account) => account.login_status === "AUTHENTICATED");
  if (filter === "VERIFICATION_REQUIRED") return accounts.filter((account) => account.login_status === "VERIFICATION_REQUIRED");
  if (filter === "NEVER_LOGGED_IN") return accounts.filter((account) => ["NOT_STARTED", "NEVER_LOGGED_IN", "REQUIRED"].includes(account.login_status));
  if (filter === "BROWSER_OPEN") return accounts.filter((account) => liveAccountIds.has(account.id));
  return accounts;
}

export function canRunDesktopOperation<T extends DesktopAccountSummary>(operation: string, accounts: T[]): boolean {
  return operation === "login" || (accounts.length > 0 && accounts.every((account) => account.login_status === "AUTHENTICATED"));
}
