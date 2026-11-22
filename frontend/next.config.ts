import type { NextConfig } from "next";

const isDev = process.env.NODE_ENV !== "production";

// Production: static export served by FastAPI (same origin, no Node server needed).
// Development: `next dev` proxies /api to the local backend.
const config: NextConfig = {
  output: isDev ? undefined : "export",
  trailingSlash: true,
  images: { unoptimized: true },
  ...(isDev && {
    async rewrites() {
      return [{ source: "/api/:path*", destination: `${process.env.API_URL ?? "http://127.0.0.1:8000"}/api/:path*` }];
    },
  }),
};

export default config;
