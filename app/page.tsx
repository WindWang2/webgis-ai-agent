"use client";
import dynamic from "next/dynamic";
import Sidebar from "./components/Sidebar";
import Header from "./components/Header";

const MapView = dynamic(
  () => import("./components/MapView"),
  { ssr: false }
);

export default function Home() {
  return (
    <>
      <Sidebar />
      <Header />
      <main style={{ marginLeft: "220px", marginTop: "56px", width: "calc(100vw - 220px)", height: "calc(100vh - 56px)" }}>
        <MapView />
      </main>
    </>
  );
}