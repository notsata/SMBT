import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Shell } from "@/components/Shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "SMBT — Market Turning Point Research",
  description: "Quantitative market top & bottom research: regime, scores, breadth, sentiment, macro, validated backtests and alerts.",
  manifest: "/manifest.webmanifest",
  applicationName: "SMBT",
  appleWebApp: { capable: true, title: "SMBT", statusBarStyle: "black-translucent" },
  icons: { icon: "/icons/icon-192.png", apple: "/icons/apple-touch-icon.png" },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: dark)", color: "#090c11" },
    { media: "(prefers-color-scheme: light)", color: "#f4f6f9" },
  ],
};

// Runs before paint so there is no light/dark flash. Default is dark.
const themeInit = `try{var t=localStorage.getItem('smbt-theme')||'dark';var d=t==='dark'||(t==='system'&&matchMedia('(prefers-color-scheme: dark)').matches);document.documentElement.classList.toggle('dark',d)}catch(e){document.documentElement.classList.add('dark')}`;

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInit }} />
      </head>
      <body className="font-sans antialiased">
        <Shell>{children}</Shell>
      </body>
    </html>
  );
}
