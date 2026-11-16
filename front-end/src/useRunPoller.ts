import { useCallback, useEffect, useState } from "react";
import { api, ApiError, type Run } from "./api";

export const POLL_ATTEMPTS = 30;
export const POLL_INTERVAL_MS = 1000;

export type PollState =
  | { phase: "idle" }
  | { phase: "polling"; run: Run | null; attempt: number }
  | { phase: "done"; run: Run }
  | { phase: "timeout"; run: Run | null }
  | { phase: "error"; error: ApiError };

/** Polls GET /api/runs/{id} a fixed number of times; exposes retry() after a timeout. */
export function useRunPoller(runId: string | null) {
  const [state, setState] = useState<PollState>({ phase: "idle" });
  const [generation, setGeneration] = useState(0);

  useEffect(() => {
    if (!runId) {
      setState({ phase: "idle" });
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let last: Run | null = null;

    const tick = async (attempt: number) => {
      if (cancelled) return;
      if (attempt > POLL_ATTEMPTS) {
        setState({ phase: "timeout", run: last });
        return;
      }
      try {
        const run = await api.getRun(runId);
        if (cancelled) return;
        last = run;
        if (run.status === "running") {
          setState({ phase: "polling", run, attempt });
          timer = setTimeout(() => void tick(attempt + 1), POLL_INTERVAL_MS);
        } else {
          setState({ phase: "done", run });
        }
      } catch (e) {
        if (cancelled) return;
        setState({ phase: "error", error: e instanceof ApiError ? e : new ApiError(String(e), 0, null) });
      }
    };

    setState({ phase: "polling", run: null, attempt: 0 });
    void tick(1);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, generation]);

  const retry = useCallback(() => setGeneration((g) => g + 1), []);
  return { state, retry };
}
