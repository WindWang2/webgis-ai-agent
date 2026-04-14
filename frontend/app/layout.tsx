import type { Metadata } from "next"
import { Inter, JetBrains_Mono } from "next/font/google"
import { ClientProviders } from "@/components/providers/client-providers"
import "./globals.css"

const inter = Inter({
  subsets: ["latin"],
  weight: ["300", "400", "500", "600", "700"],
  variable: "--font-inter",
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains",
})

export const metadata: Metadata = {
  title: "WebGIS AI Agent — Spatial Intelligence HUD",
  description: "智能地理空间分析系统 — Deep Space HUD 沉浸式空间智能平台",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" className="dark">
      <head>
        {/* Orbitron for HUD display font (loaded via Google Fonts external) */}
        <link
          href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans antialiased`}>
        <ClientProviders>{children}</ClientProviders>
      </body>
    </html>
  )
}