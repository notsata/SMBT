// Builds the single-file snapshot page (mobile artifact): node snapshot/build.mjs <out_dir>
// Requires `npm run build` first (reuses its compiled Tailwind CSS) and the exported data in <out_dir>/data.
import { build } from "esbuild";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const out = process.argv[2];
if (!out) throw new Error("usage: node snapshot/build.mjs <out_dir>");

const res = await build({
  entryPoints: ["snapshot/main.tsx"],
  bundle: true,
  minify: true,
  format: "iife",
  target: "es2020",
  write: false,
  jsx: "automatic",
  legalComments: "none",
  alias: { "next/link": "./snapshot/router.tsx", "next/navigation": "./snapshot/router.tsx" },
  define: { "process.env.NODE_ENV": '"production"', "process.env.NEXT_PUBLIC_DISABLE_SW": '"1"' },
  logOverride: { "unsupported-directive": "silent" },
});
const js = res.outputFiles[0].text.replaceAll("</script", "<\\/script");

const cssDir = "out/_next/static/chunks";
const css = readdirSync(cssDir).filter((f) => f.endsWith(".css")).map((f) => readFileSync(join(cssDir, f), "utf8")).join("\n");

// Artifact contract: dark-first tokens on bare :root, light under the media query and [data-theme="light"],
// safe-area aware sticky header and side gutters.
const DARK = "--c-bg:#090c11;--c-panel:#10151c;--c-panel2:#161c25;--c-line:#232c38;--c-fg:#e6edf3;--c-muted:#8894a5;--c-accent:#4c9aff;--c-up:#26a69a;--c-down:#ef5350;--c-warn:#f0b429";
const LIGHT = "--c-bg:#f4f6f9;--c-panel:#ffffff;--c-panel2:#eef1f5;--c-line:#d9dfe7;--c-fg:#0f1720;--c-muted:#5a6778;--c-accent:#1f6feb;--c-up:#0b8a7c;--c-down:#d23f3a;--c-warn:#a8690c";
const overrides = `
:root{${DARK};color-scheme:dark}
@media (prefers-color-scheme: light){:root:not([data-theme="dark"]){${LIGHT};color-scheme:light}}
:root[data-theme="light"]{${LIGHT};color-scheme:light}
body{background:var(--c-bg);color:var(--c-fg)}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:31;background:var(--c-bg)}
#root header.sticky{top:env(safe-area-inset-top,0px);padding-top:0}
#root header.sticky>div,#root main{padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px))}
#root nav[aria-label="Main mobile"]{padding-left:env(safe-area-inset-left,0px);padding-right:env(safe-area-inset-right,0px)}
#boot{padding:2rem max(16px,env(safe-area-inset-left,0px));font:14px system-ui,sans-serif;color:var(--c-muted)}
@media (prefers-reduced-motion: reduce){*{animation:none!important;transition:none!important}}
`;

const html = `<title>SMBT Market Terminal</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
${css}
${overrides}
</style>
<div id="root"><p id="boot">Loading SMBT…</p></div>
<script>
${js}
</script>
`;
writeFileSync(join(out, "index.html"), html);
console.log(`index.html ${(html.length / 1024).toFixed(0)} KB (js ${(js.length / 1024).toFixed(0)} KB, css ${(css.length / 1024).toFixed(0)} KB)`);
