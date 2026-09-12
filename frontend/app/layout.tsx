import type { Metadata } from "next"
import { cookies } from "next/headers"
import { DM_Sans, JetBrains_Mono } from "next/font/google"
import { ClientProviders } from "@/components/providers/client-providers"
// RSC 边界注意：layout 是服务端组件，这里绝不能从 "use client" 模块
// （useHudStore）取值 —— 该 import 在 SSR 输出里会静默变成 undefined，
// no-flash 脚本曾因此变成 localStorage.getItem(undefined)（journey 5 捕获）。
import { PERSIST_KEY } from "@/lib/store/persist-key"
import { DEFAULT_LOCALE, LOCALE_COOKIE, LOCALE_GLOBAL_VAR, normalizeLocale } from "@/lib/i18n/config"
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
 * Applies the persisted theme + accent + locale before the first paint.
 *
 * The React path (`app/page.tsx` + `I18nProvider`) only applies these from
 * effects that run after hydration, so without this a dark-mode user got a
 * full light frame on every load, and a non-default locale would flash the
 * wrong language before the store rehydrates. Reads the same `geoagent-settings`
 * key the zustand `persist` middleware writes, and fails silently — a bad/absent
 * value just leaves the light/zh defaults in place.
 *
 * Locale resolution order here MUST match the server render: the cookie is the
 * single SSR-visible source (layout reads it via `cookies()` below), so the
 * inline script writes it into `window.__GEOAGENT_LOCALE__` for the language
 * store's first (hydration-matching) render. The localStorage copy is only
 * adopted post-mount by `rehydrateLocale()` — never pre-paint — so a stale
 * localStorage value can't cause a hydration text mismatch.
 */
const themeBootstrap = `(function(){try{
var s=localStorage.getItem(${JSON.stringify(PERSIST_KEY)});
var r=document.documentElement;
if(s){var v=(JSON.parse(s)||{}).state||{};
if(v.theme==='dark'){r.classList.add('dark');r.setAttribute('data-theme','dark');}
else{r.setAttribute('data-theme','light');}
if(typeof v.accentColor==='string'&&/^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$|^rgba?\\([0-9,.\\s]+\\)$/i.test(v.accentColor))r.style.setProperty('--agent-accent-raw',v.accentColor);}
}catch(e){}
try{
var m=document.cookie.match(/(?:^|;\\s*)${LOCALE_COOKIE}=([^;]+)/);
var l=m?decodeURIComponent(m[1]):'${DEFAULT_LOCALE}';
window.${LOCALE_GLOBAL_VAR}=l;
r.setAttribute('lang',l);
}catch(e){}})();`

export default async function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  // SSR 语言取 cookie（setLocale 每次切换都会写回）：服务端首帧即正确语言，
  // 客户端 store 首帧读 window.__GEOAGENT_LOCALE__（pre-paint 从同一 cookie
  // 写入）—— 两侧一致，无 hydration mismatch、无语言闪白。
  const cookieStore = await cookies()
  const locale = normalizeLocale(cookieStore.get(LOCALE_COOKIE)?.value)

  return (
    <html lang={locale} data-theme="light">
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeBootstrap }} />
      </head>
      <body className={`${dmSans.variable} ${jetbrainsMono.variable} font-sans antialiased`}>
        <ClientProviders>{children}</ClientProviders>
      </body>
    </html>
  )
}
