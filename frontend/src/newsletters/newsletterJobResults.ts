import type { NewsletterJob } from "./newsletterTypes";

const ACTIVE_JOB_STATES = new Set(["RUNNING", "PAUSED"]);
const TERMINAL_JOB_STATES = new Set(["COMPLETED", "CANCELLED", "FAILED"]);

export function newlyFinishedNewsletterJobIds(
  jobs: Pick<NewsletterJob, "id" | "status">[],
  previousStatuses: ReadonlyMap<string, string>,
): string[] {
  return jobs
    .filter((job) => TERMINAL_JOB_STATES.has(job.status) && ACTIVE_JOB_STATES.has(previousStatuses.get(job.id) ?? ""))
    .map((job) => job.id);
}
