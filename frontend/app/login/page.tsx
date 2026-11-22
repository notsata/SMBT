"use client";

import { useState } from "react";
import { ErrorBox, Panel } from "@/components/ui";
import { api } from "@/lib/api";

export default function LoginPage() {
  const [token, setToken] = useState("");
  const [err, setErr] = useState<Error | null>(null);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErr(null);
    try {
      await api("/auth/login", { method: "POST", json: { token } });
      const next = new URLSearchParams(location.search).get("next");
      location.href = next && next.startsWith("/") && !next.startsWith("//") ? next : "/";
    } catch (e) {
      setErr(e as Error);
    }
  };
  return (
    <div className="mx-auto mt-16 max-w-sm">
      <Panel title="Sign in">
        <form onSubmit={submit} className="space-y-3">
          <label className="block text-sm">
            Access token
            <input type="password" autoComplete="current-password" value={token} onChange={(e) => setToken(e.target.value)} className="mt-1 w-full rounded-lg border border-line bg-panel px-3 py-2" />
          </label>
          <button className="w-full rounded-lg bg-accent py-2 text-sm font-medium text-white">Sign in</button>
          {err && <ErrorBox error={err} />}
          <p className="text-xs text-muted">The token is the APP_ACCESS_TOKEN configured on your server. Each device stays signed in for 90 days.</p>
        </form>
      </Panel>
    </div>
  );
}
