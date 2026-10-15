import { describe, expect, it } from "vitest";
import { newlyFinishedNewsletterJobIds } from "./newsletterJobResults";

describe("newsletter bulk result notifications", () => {
  it("queues only jobs that have moved from an active state to a terminal state", () => {
    const jobs = [
      { id: "completed", status: "COMPLETED" },
      { id: "cancelled", status: "CANCELLED" },
      { id: "still-running", status: "RUNNING" },
      { id: "already-finished", status: "FAILED" },
      { id: "draft", status: "DRAFT" },
    ];
    const previous = new Map([
      ["completed", "RUNNING"],
      ["cancelled", "PAUSED"],
      ["still-running", "RUNNING"],
      ["already-finished", "FAILED"],
    ]);

    expect(newlyFinishedNewsletterJobIds(jobs, previous)).toEqual(["completed", "cancelled"]);
  });
});
