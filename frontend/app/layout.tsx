import type { Metadata } from "next"
import { JetBrains_Mono } from "next/font/google"
import { Crimson_Pro } from "next/font/google"
import { MapActionProvider } from "@/lib/contexts/map-action-context"
import "./globals.css"

// 使用 Crimson Pro 作为主要衬线字体，JetBrains Mono 作为代码字体
const crimsonPro = Crimson_Pro({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  style: ["normal", "italic"],
  variable: "--font-crimson",
})

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains",
})

export const metadata: Metadata = {
  title: "WebGIS AI Agent - 探索者日志",
  description: "智能地理空间分析系统 - 基于大语言模型的空间数据分析与制图",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" className="dark">
      <body className={`${crimsonPro.variable} ${jetbrainsMono.variable} font-serif`}>
        <MapActionProvider>{children}</MapActionProvider>
      </body>
    </html>
  )
}