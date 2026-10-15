import { describe, expect, it } from "vitest";
import { PROXY_TEST_CONCURRENCY, runProxyTestQueue } from "./proxy-test";

describe("live proxy bulk testing", () => {
  it("reports each result before the remaining checks finish and bounds concurrency", async () => {
    const pending = new Map<string, (result: { proxy_id: string }) => void>();
    const started: string[] = [];
    const completed: string[] = [];
    const ids = Array.from({ length: PROXY_TEST_CONCURRENCY + 2 }, (_, index) => `proxy_${index + 1}`);
    const run = runProxyTestQueue(ids, (id) => new Promise<{ proxy_id: string }>((resolve) => pending.set(id, resolve)), (id) => started.push(id), (result) => completed.push(result.proxy_id), () => { throw new Error("Unexpected failure"); });

    expect(started).toEqual(ids.slice(0, PROXY_TEST_CONCURRENCY));
    pending.get("proxy_2")?.({ proxy_id: "proxy_2" });
    await Promise.resolve();
    expect(completed).toEqual(["proxy_2"]);
    expect(started).toContain(ids[PROXY_TEST_CONCURRENCY]);
    for (const id of ids.filter((item) => item !== "proxy_2")) {
      while (!pending.has(id)) await Promise.resolve();
      pending.get(id)?.({ proxy_id: id });
      await Promise.resolve();
    }
    expect(await run).toBeNull();
    expect(completed).toHaveLength(ids.length);
  });

  it("stops starting new checks after a transport failure while retaining in-flight results", async () => {
    let finishFirst: ((result: { proxy_id: string }) => void) | undefined;
    const started: string[] = [];
    const completed: string[] = [];
    const failed: string[] = [];
    const run = runProxyTestQueue(["first", "second", "third"], (id) => id === "first" ? new Promise<{ proxy_id: string }>((resolve) => { finishFirst = resolve; }) : Promise.reject(new Error("Network down")), (id) => started.push(id), (result) => completed.push(result.proxy_id), (id) => failed.push(id), 2);

    await Promise.resolve();
    expect(started).toEqual(["first", "second"]);
    finishFirst?.({ proxy_id: "first" });
    expect((await run)?.message).toBe("Network down");
    expect(completed).toEqual(["first"]);
    expect(failed).toEqual(["second"]);
    expect(started).not.toContain("third");
  });

  it("rejects invalid concurrency", async () => {
    await expect(runProxyTestQueue([], async (id) => ({ proxy_id: id }), () => {}, () => {}, () => {}, 0)).rejects.toThrow("positive integer");
  });
});
