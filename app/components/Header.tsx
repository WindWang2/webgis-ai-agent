"use client";
import styles from "./Header.module.css";
export default function Header() {
  return (
    <header className={styles.header}>
      <div className={styles.title}>地理信息系统智能代理</div>
      <div className={styles.actions}>
        <button className={styles.btn}>设置</button>
        <button className={styles.btnPrimary}>AI 对话</button>
      </div>
    </header>
  );
}