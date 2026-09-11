# 08 — Security / Reliability Findings（索引）

详证：agents/D/findings.md（D-3, D-9, D-13, D-14 + verified-clean highlights）、
agents/B/findings.md（B-3, B-7, B-8, B-14, B-19）。

无 P0：无 RCE/认证绕过/破坏性路径。安全态势：auth（scrypt+版本化 token）、
owner scope（31 路由模块全扫描）、SSRF（DataFabricSecurity pin）、path traversal、
injection（零 text(f)/shell=True/pickle）、secrets 红acted —— 复核干净。

残留（均 P2/P3）：D-3 SSRF 哨兵死代码（防线1 失效靠 2/3 层兜底）、D-9 static
admin 通道无 ver 校验（登出后 30min 窗口）、D-13 HMAC 签名 URL 与 JWT 同密钥无
域分离、D-14 无 session 上传不可见不可删、B-3 并发推理缓冲竞争（可靠性）、
B-7 GDAL 句柄泄漏、B-8 多副本 registry 漂移、B-19 run LRU 可驱逐在飞 run。
