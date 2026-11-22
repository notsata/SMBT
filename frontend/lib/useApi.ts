"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "./api";

// In-memory cache so tab switches render instantly; the service worker adds offline persistence.
const cache = new Map<string, unknown>();

export function useApi<T>(path: string | null) {
  const [data, setData] = useState<T | undefined>(() => (path ? (cache.get(path) as T | undefined) : undefined));
  const [error, setError] = useState<Error | null>(null);
  const [loading, setLoading] = useState(Boolean(path));
  const current = useRef(path);

  const load = useCallback(async () => {
    if (!path) return;
    current.current = path;
    setLoading(true);
    setError(null);
    try {
      const d = await api<T>(path);
      cache.set(path, d);
      if (current.current === path) setData(d);
    } catch (e) {
      if (current.current === path) setError(e as Error);
    } finally {
      if (current.current === path) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    setData(path ? (cache.get(path) as T | undefined) : undefined);
    void load();
  }, [path, load]);

  return { data, error, loading, reload: load };
}
