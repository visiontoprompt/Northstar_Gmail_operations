export const PROXY_TEST_CONCURRENCY = 4;

/** Run independent proxy checks with a small fixed number of requests in flight. */
export async function runProxyTestQueue<T extends { proxy_id: string }>(
  proxyIds: string[],
  testOne: (proxyId: string) => Promise<T>,
  onStart: (proxyId: string) => void,
  onResult: (result: T) => void,
  onError: (proxyId: string, error: Error) => void,
  concurrency = PROXY_TEST_CONCURRENCY,
): Promise<Error | null> {
  if (!Number.isInteger(concurrency) || concurrency < 1) throw new Error("Proxy test concurrency must be a positive integer.");
  let next = 0;
  let failure: Error | null = null;
  const worker = async () => {
    while (!failure && next < proxyIds.length) {
      const proxyId = proxyIds[next++];
      onStart(proxyId);
      try {
        const result = await testOne(proxyId);
        if (result.proxy_id !== proxyId) throw new Error("The proxy test returned a result for a different endpoint.");
        onResult(result);
      } catch (cause) {
        const error = cause instanceof Error ? cause : new Error("The proxy check could not be completed.");
        failure ??= error;
        onError(proxyId, error);
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, proxyIds.length) }, worker));
  return failure;
}
