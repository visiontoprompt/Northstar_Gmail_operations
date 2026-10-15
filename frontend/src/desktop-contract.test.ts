import { describe, expect, it } from "vitest";
import { canRunDesktopOperation, countDesktopAccounts, filterDesktopAccounts, searchDesktopAccounts, selectedLiveSessions } from "./desktop-contract";

const accounts = [
  { id: "a1", email: "anna@example.com", login_status: "AUTHENTICATED" },
  { id: "a2", email: "ben@example.com", login_status: "VERIFICATION_REQUIRED" },
  { id: "a3", email: "cat@example.com", login_status: "NOT_STARTED" },
];

describe("Browser Desktop account scope", () => {
  it("filters authenticated, verification, never-logged-in, and live-browser accounts", () => {
    const live = new Set(["a2"]);
    expect(filterDesktopAccounts(accounts, "AUTHENTICATED", live).map((account) => account.id)).toEqual(["a1"]);
    expect(filterDesktopAccounts(accounts, "VERIFICATION_REQUIRED", live).map((account) => account.id)).toEqual(["a2"]);
    expect(filterDesktopAccounts(accounts, "NEVER_LOGGED_IN", live).map((account) => account.id)).toEqual(["a3"]);
    expect(filterDesktopAccounts(accounts, "BROWSER_OPEN", live).map((account) => account.id)).toEqual(["a2"]);
  });

  it("counts status filters only within the searched account scope", () => {
    const searched = searchDesktopAccounts(accounts, "EXAMPLE.COM");
    const counts = countDesktopAccounts(searched, new Set(["a1", "a2"]));
    expect(counts).toEqual({ ALL: 3, AUTHENTICATED: 1, VERIFICATION_REQUIRED: 1, NEVER_LOGGED_IN: 1, BROWSER_OPEN: 2 });
    expect(searchDesktopAccounts(accounts, "ben").map((account) => account.id)).toEqual(["a2"]);
  });

  it("requires every non-login operation target to be authenticated", () => {
    expect(canRunDesktopOperation("login", accounts)).toBe(true);
    expect(canRunDesktopOperation("addons", [accounts[0]])).toBe(true);
    expect(canRunDesktopOperation("addons", [accounts[0], accounts[1]])).toBe(false);
    expect(canRunDesktopOperation("mail", [])).toBe(false);
  });

  it("closes only one live browser per selected account inside the current visible scope", () => {
    const sessions = [
      { id: "s1", account_id: "a1", browser_alive: true },
      { id: "s1-old", account_id: "a1", browser_alive: true },
      { id: "s2", account_id: "a2", browser_alive: false },
      { id: "s3", account_id: "a3", browser_alive: true },
    ];
    expect(selectedLiveSessions(sessions, new Set(["a1", "a2", "a3"]), new Set(["a1", "a2"]))).toEqual([sessions[0]]);
  });
});
