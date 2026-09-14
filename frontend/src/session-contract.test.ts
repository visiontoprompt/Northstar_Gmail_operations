import { describe, expect, it } from "vitest";
import { isOpenSessionState } from "./session-contract";

describe("session hand-off contract", () => {
  it("keeps profile actions available only for open sessions", () => {
    expect(isOpenSessionState("LOGIN_REQUIRED")).toBe(true);
    expect(isOpenSessionState("RUNNING")).toBe(true);
    expect(isOpenSessionState("NEEDS_ATTENTION")).toBe(true);
    expect(isOpenSessionState("CLOSED")).toBe(false);
  });
});
