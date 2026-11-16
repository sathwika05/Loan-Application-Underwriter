import { useCallback, useEffect, useState } from "react";

/** Loads data from a stable async function; pass a useCallback-wrapped loader. */
export function useLoad<T>(loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const [loadedAt, setLoadedAt] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loader()
      .then((d) => {
        if (!cancelled) {
          setData(d);
          setError(null);
          setLoadedAt(new Date());
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [loader, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, error, loading, reload, loadedAt };
}
