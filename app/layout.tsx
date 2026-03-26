import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "WebGIS AI Agent",
  description: "地理信息系统人工智能代理",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <head>
        <link
          href="https://unpkg.com/maplibre-gl@5.0.1/dist/maplibre-gl.css"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen m-0 p-0 overflow-hidden">
        {children}
      </body>
    </html>
  );
}