import type { Metadata } from "next"
import { DM_Sans, JetBrains_Mono } from "next/font/google"
import { ClientProviders } from "@/components/providers/client-providers"
import { PERSIST_KEY } from "@/lib/store/useHudStore"
import "./globals.css"

const dmSans = DM_Sans({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600"],
  variable: "--font-dm-sans",
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains",
})

export const metadata: Metadata = {
  title: "GeoAgent — All is Agent",
  description: "智能地理空间分析系统 — 地图即感知，图层即记忆，分析即行动",
}

/**
 * Applies the persisted theme + accent before the first paint.
 *
 * The React path (`app/page.tsx`) only sets the `dark` class from an effect that
 * runs after hydration, so without this a dark-mode user got a full light frame
 * on every load. Reads the same `geoagent-settings` key the zustand `persist`
 * middleware writes, and fails silently — a bad/absent value just leaves the
 * light default in place.
 */
const themeBootstrap = `(function(){try{
var s=localStorage.getItem(${JSON.stringify(PERSIST_KEY)});
if(!s)return;
var v=(JSON.parse(s)||{}).state||{};
var r=document.documentElement;
if(v.theme==='dark'){r.classList.add('dark');r.setAttribute('data-theme','dark');}
else{r.setAttribute('data-theme','light');}
if(typeof v.accentColor==='string')r.style.setProperty('--agent-accent-raw',v.accentColor);
}catch(e){}})();`

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" data-theme="light">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className={`${dmSans.variable} ${jetbrainsMono.variable} font-sans antialiased`}>
        <ClientProviders>{children}</ClientProviders>
      </body>
    </html>
  )
}
