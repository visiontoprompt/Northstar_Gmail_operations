export const ACTIVE_SESSION_STATES = ["STARTING", "LOGIN_REQUIRED", "RUNNING", "NEEDS_ATTENTION"] as const;

export const isOpenSessionState = (state: string) => ACTIVE_SESSION_STATES.includes(state as (typeof ACTIVE_SESSION_STATES)[number]);

export const sessionStateLabel = (state: string) => state.toLowerCase().replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
