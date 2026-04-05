import type { Metadata } from "next"
import { Inter, JetBrains_Mono } from "next/font/google"
import "./globals.css"

const inter = Inter({ subsets: ["latin"] })
const jetbrainsMono = JetBrains_Mono({ subsets: ["latin"] })

export const metadata: Metadata = {
  title: "WebGIS AI Agent - 智能地理空间分析系统",
  description: "基于大语言模型的 WebGIS 智能数据分析与制图系统",
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="zh-CN" className="dark">
      <body className={`${inter.className} ${jetbrainsMono.className}`}>{children}</body>
    </html>
  )
}