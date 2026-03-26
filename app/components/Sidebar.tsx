"use client";
import Link from "next/link";
import styles from "./Sidebar.module.css";

interface NavItem {
  label: string;
  href: string;
  icon?: string;
}

const navItems: NavItem[] = [
  { label: "地图浏览", href: "/" },
  { label: "图层管理", href: "/layers" },
  { label: "空间分析", href: "/analysis" },
];

export default function Sidebar() {
  return (
    <aside className={styles.sidebar}>
      <div className={styles.logo}>
        <span className={styles.logoIcon}>🗺</span>
        <span className={styles.logoText}>WebGIS AI</span>
      </div>
      <nav className={styles.nav}>
        {navItems.map((item) => (
          <Link key={item.href} href={item.href} className={styles.navItem}>
            <span className={styles.navLabel}>{item.label}</span>
          </Link>
        ))}
      </nav>
    </aside>
  );
}