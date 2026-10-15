export const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const isMultipart = options?.body instanceof FormData;
  const response = await fetch(path, { credentials: "include", headers: { ...(isMultipart ? {} : { "Content-Type": "application/json" }), ...(options?.headers ?? {}) }, ...options });
  const body = await response.text();
  let payload: Record<string, unknown> = {};
  if (body.trim()) {
    try {
      payload = JSON.parse(body) as Record<string, unknown>;
    } catch {
      if (!response.ok) throw new Error(`The server returned an unreadable error response (HTTP ${response.status}). Check the API service logs for the underlying error.`);
    }
  }
  if (!response.ok) {
    throw new Error(typeof payload.detail === "string" ? payload.detail : "The request could not be completed.");
  }
  return (body.trim() ? payload : undefined) as T;
};
