"use client";

// Session-only watchlist: lives in this tab's memory and disappears on refresh (no storage, no server copy).
import { useSyncExternalStore } from "react";

let list: string[] = [];
const subs = new Set<() => void>();
const emit = () => subs.forEach((f) => f());

export const watchlist = {
  add(sym: string) {
    sym = sym.trim().toUpperCase();
    if (sym && !list.includes(sym)) {
      list = [...list, sym].slice(-30);
      emit();
    }
  },
  remove(sym: string) {
    list = list.filter((s) => s !== sym);
    emit();
  },
};

export function useWatchlist(): string[] {
  return useSyncExternalStore(
    (cb) => {
      subs.add(cb);
      return () => subs.delete(cb);
    },
    () => list,
    () => list,
  );
}
