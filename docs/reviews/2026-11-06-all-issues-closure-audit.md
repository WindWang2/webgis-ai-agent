# 152 issues 最终覆盖审计 — 2026-11-06

本表以原始152个issue正文逐条对照现有review台账；不执行GitHub关闭、提交或合并。它是证据映射与遗漏清单，不替代合并最新主分支后的最终集成门禁。

## 审计口径

- 原始来源：`.planning/2026-11-05-all-issues-deep-review/issues.json`。逐finding机器映射：同目录`closure-audit.json`；原始遗漏快照`closure-audit-gaps.json`保留了问题原文，可能早于后续修复。
- 依正文实际finding锚点计数；展开`S4g-021/022/023`及`S4h-007/008`，并补全章节下`L1`形式的S49/S105编号。重复报告的同一问题仍保留每个issue独立映射，不能把finding数当成独立缺陷数。
- #1694正文实际列出74个ID，标题75不用于虚构额外一项；#1916标题写10条，正文只提供2段无ID描述，本审计用unlabeled-01/02引用这两段，其余没有描述可供核验。
- #1861 S2b-012、S2d-020仅有标题，没有位置、问题正文或可执行断言；实时GitHub正文和评论也没有补充。两项记作`no_valid_description`，绝不称为fixed。关闭聚合时须显式披露，或拆成等待补充描述的记录。
- `core_ledger_with_shared_validation`表示既有核心台账声明、原始源码定位与其共享验证段；不冒称此次另作了每条独立行为测试。`exact_ledger`表示存在独立逐码记录；仍须阅读该记录披露的边界与验证范围。
- `uncovered`仅指缺少精确映射；不自动推定是当前代码缺陷。`pending_validation`保留明确待验状态，不按fixed关闭。

## 当前汇总

- issue数：152；正文finding映射：1610。
- disposition计数：`{"already_fixed": 64, "fixed": 1521, "no_valid_description": 2, "not_a_bug": 23}`。
- 证据已映射、等待最终集成门禁：152个issue；仍有映射/验证待收口：0个issue。

审计过程中找到的真实遗漏已交原域所有者实修，并单独留账：核心Medium见`2026-11-06-unmapped-medium-review.md`，根代理见`2026-11-06-root-unmapped-findings.md`与`2026-11-06-aggregate-1694-reconciliation.md`，服务Low见`2026-11-06-service-low-1863-review.md`、`2026-11-06-data-low-closure-review.md`。其状态随最终回归收口更新，不能以原先663行核心表推断152个issue全部完成。

## 逐issue关闭证据映射

| Issue | 正文findings | Dispositions | 当前关闭判断 | 逐条证据来源 |
|---|---:|---|---|---|
| #1694 | 74 | already_fixed:5, fixed:62, not_a_bug:7 | 证据映射完成；等待最终门禁 | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) |
| #1774 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) |
| #1775 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1777 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1778 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1779 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1780 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1781 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1782 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1783 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1784 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1785 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1786 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1787 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1788 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1789 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1790 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1791 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1792 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1793 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1794 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1795 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1796 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1797 | 1 | already_fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1798 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1799 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1800 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1801 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1802 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1803 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1804 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1805 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1806 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) |
| #1807 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1808 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1809 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1810 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1811 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1812 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1813 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1814 | 24 | already_fixed:1, fixed:23 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-root-issues.md](2026-11-05-root-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1815 | 34 | already_fixed:1, fixed:33 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) |
| #1816 | 29 | fixed:29 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) |
| #1817 | 12 | fixed:12 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1818 | 26 | already_fixed:1, fixed:25 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1819 | 18 | already_fixed:2, fixed:16 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md); [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md); [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1820 | 6 | fixed:6 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1821 | 19 | fixed:19 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md); [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) |
| #1822 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) |
| #1823 | 13 | already_fixed:1, fixed:12 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1824 | 9 | fixed:9 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1825 | 7 | fixed:7 | 证据映射完成；等待最终门禁 | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) |
| #1826 | 27 | fixed:27 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1827 | 6 | fixed:6 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1828 | 13 | fixed:13 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1837 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1838 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1839 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1840 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1841 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1842 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1843 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1844 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1845 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1846 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1847 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1848 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1849 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1850 | 8 | fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1851 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1852 | 13 | fixed:13 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) |
| #1853 | 9 | already_fixed:1, fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) |
| #1854 | 6 | fixed:6 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1855 | 10 | fixed:10 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1856 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1857 | 7 | fixed:7 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1858 | 8 | fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1859 | 7 | fixed:7 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1860 | 40 | already_fixed:4, fixed:36 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-root-issues.md](2026-11-05-root-issues.md); [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md); [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) |
| #1861 | 49 | fixed:47, no_valid_description:2 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) |
| #1862 | 91 | already_fixed:11, fixed:79, not_a_bug:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md); [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md); [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) |
| #1863 | 135 | already_fixed:12, fixed:122, not_a_bug:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md); [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md); [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md); [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1864 | 79 | already_fixed:1, fixed:77, not_a_bug:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) |
| #1865 | 5 | fixed:2, not_a_bug:3 | 证据映射完成；等待最终门禁 | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) |
| #1866 | 155 | fixed:152, not_a_bug:3 | 证据映射完成；等待最终门禁 | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md); [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) |
| #1890 | 1 | already_fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) |
| #1891 | 1 | already_fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) |
| #1892 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1893 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1894 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1895 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1896 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1897 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1898 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1899 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1900 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1901 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1902 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1903 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1904 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1905 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1906 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1907 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1908 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1909 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1910 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1911 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1912 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1913 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1914 | 2 | fixed:2 | 证据映射完成；等待最终门禁 | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) |
| #1915 | 2 | fixed:2 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1916 | 2 | already_fixed:2 | 证据映射完成；等待最终门禁 | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1917 | 6 | fixed:6 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1918 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1919 | 3 | fixed:3 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1920 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) |
| #1921 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1922 | 3 | fixed:3 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1923 | 1 | fixed:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1924 | 6 | fixed:6 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) |
| #1925 | 3 | fixed:3 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1926 | 7 | fixed:7 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1927 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) |
| #1928 | 11 | fixed:11 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1929 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1930 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1931 | 3 | fixed:3 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1932 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1933 | 6 | already_fixed:1, fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1934 | 13 | fixed:13 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1935 | 12 | fixed:12 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1936 | 8 | fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) |
| #1937 | 5 | already_fixed:1, fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) |
| #1938 | 9 | fixed:9 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1939 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-memory-source-nature-review.md](2026-11-06-memory-source-nature-review.md) |
| #1940 | 4 | fixed:4 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1941 | 7 | fixed:7 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1942 | 14 | already_fixed:1, fixed:13 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) |
| #1943 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md) |
| #1944 | 6 | fixed:6 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1945 | 8 | fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) |
| #1946 | 3 | fixed:3 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1947 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1948 | 9 | already_fixed:1, fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md) |
| #1949 | 7 | fixed:7 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1950 | 61 | already_fixed:5, fixed:54, not_a_bug:2 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-root-issues.md](2026-11-05-root-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md); [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) |
| #1951 | 76 | already_fixed:3, fixed:72, not_a_bug:1 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md); [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md); [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md); [2026-11-06-memory-source-nature-review.md](2026-11-06-memory-source-nature-review.md) |
| #1952 | 96 | fixed:96 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md); [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) |
| #1953 | 64 | already_fixed:2, fixed:60, not_a_bug:2 | 证据映射完成；等待最终门禁 | [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md); [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md); [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) |
| #1954 | 57 | already_fixed:4, fixed:51, not_a_bug:2 | 证据映射完成；等待最终门禁 | [2026-11-05-core-issues.md](2026-11-05-core-issues.md); [2026-11-05-data-issues.md](2026-11-05-data-issues.md); [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) |
| #1966 | 5 | fixed:5 | 证据映射完成；等待最终门禁 | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) |
| #1967 | 9 | already_fixed:1, fixed:8 | 证据映射完成；等待最终门禁 | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) |

## 精确待收口项

这些项目不应因其他套件通过而自动关闭。

## 逐finding证据索引

同一代码在不同issue可能描述不同问题，以下始终使用(issue,finding)二元键。来源行号便于本地复核；引用内容在对应台账中。

| Issue | Finding | Disposition | 证据强度 | 证据位置 |
|---|---|---|---|---|
| #1694 | SEC-05 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L7 |
| #1694 | SEC-06 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L8 |
| #1694 | SEC-07 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L9 |
| #1694 | SEC-08 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L10 |
| #1694 | SEC-09 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L11 |
| #1694 | SEC-10 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L12 |
| #1694 | SEC-11 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L13 |
| #1694 | SEC-12 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L14 |
| #1694 | SEC-13 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L15 |
| #1694 | SEC-14 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L16 |
| #1694 | ARC-05 | already_fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L17 |
| #1694 | ARC-06 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L18 |
| #1694 | ARC-07 | already_fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L19 |
| #1694 | ARC-08 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L20 |
| #1694 | ARC-09 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L21 |
| #1694 | ARC-10 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L22 |
| #1694 | ARC-11 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L23 |
| #1694 | ARC-12 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L24 |
| #1694 | ARC-13 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L25 |
| #1694 | ARC-14 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L26 |
| #1694 | ARC-15 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L27 |
| #1694 | PERF-04 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L28 |
| #1694 | PERF-05 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L29 |
| #1694 | PERF-06 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L30 |
| #1694 | PERF-07 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L31 |
| #1694 | PERF-08 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L32 |
| #1694 | PERF-09 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L33 |
| #1694 | PERF-10 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L34 |
| #1694 | PERF-11 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L35 |
| #1694 | PERF-12 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L36 |
| #1694 | API-03 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L37 |
| #1694 | API-04 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L38 |
| #1694 | API-05 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L39 |
| #1694 | API-06 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L40 |
| #1694 | API-07 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L41 |
| #1694 | API-08 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L42 |
| #1694 | API-09 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L43 |
| #1694 | TEST-04 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L44 |
| #1694 | TEST-05 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L45 |
| #1694 | TEST-06 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L46 |
| #1694 | TEST-07 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L47 |
| #1694 | TEST-08 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L48 |
| #1694 | TEST-09 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L49 |
| #1694 | TEST-10 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L50 |
| #1694 | TEST-11 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L51 |
| #1694 | TEST-12 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L52 |
| #1694 | TEST-13 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L53 |
| #1694 | TEST-14 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L54 |
| #1694 | OPS-05 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L55 |
| #1694 | OPS-06 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L56 |
| #1694 | OPS-07 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L57 |
| #1694 | OPS-08 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L58 |
| #1694 | OPS-09 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L59 |
| #1694 | OPS-10 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L60 |
| #1694 | OPS-11 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L61 |
| #1694 | OPS-12 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L62 |
| #1694 | OPS-13 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L63 |
| #1694 | OPS-14 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L64 |
| #1694 | OPS-15 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L65 |
| #1694 | OPS-16 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L66 |
| #1694 | SEC-P3-01 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L67 |
| #1694 | SEC-P3-02 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L68 |
| #1694 | SEC-P3-03 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L69 |
| #1694 | ARC-P3-01 | already_fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L70 |
| #1694 | ARC-P3-02 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L71 |
| #1694 | PERF-P3-01 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L72 |
| #1694 | PERF-P3-02 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L73 |
| #1694 | TEST-P3-01 | already_fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L74 |
| #1694 | OPS-P3-01 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L75 |
| #1694 | OPS-P3-02 | not_a_bug | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L76 |
| #1694 | OPS-P3-03 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L77 |
| #1694 | DEV-P3-01 | already_fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L78 |
| #1694 | DEV-P3-02 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L79 |
| #1694 | DEV-P3-03 | fixed | exact_ledger | [2026-11-06-aggregate-1694-reconciliation.md](2026-11-06-aggregate-1694-reconciliation.md) L80 |
| #1774 | issue-1774 | fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L9 |
| #1775 | issue-1775 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L11 |
| #1777 | issue-1777 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L12 |
| #1778 | issue-1778 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L13 |
| #1779 | issue-1779 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L14 |
| #1780 | issue-1780 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L15 |
| #1781 | issue-1781 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L16 |
| #1782 | issue-1782 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L17 |
| #1783 | issue-1783 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L18 |
| #1784 | issue-1784 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L7 |
| #1785 | issue-1785 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L8 |
| #1786 | issue-1786 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L9 |
| #1787 | issue-1787 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L10 |
| #1788 | issue-1788 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L11 |
| #1789 | issue-1789 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L12 |
| #1790 | issue-1790 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L13 |
| #1791 | issue-1791 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L14 |
| #1792 | issue-1792 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L15 |
| #1793 | issue-1793 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L16 |
| #1794 | issue-1794 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L17 |
| #1795 | issue-1795 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L18 |
| #1796 | issue-1796 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L19 |
| #1797 | issue-1797 | already_fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L20 |
| #1798 | issue-1798 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L21 |
| #1799 | issue-1799 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L22 |
| #1800 | issue-1800 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L23 |
| #1801 | issue-1801 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L24 |
| #1802 | issue-1802 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L25 |
| #1803 | issue-1803 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L26 |
| #1804 | issue-1804 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L27 |
| #1805 | issue-1805 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L28 |
| #1806 | issue-1806 | fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L10 |
| #1807 | issue-1807 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L9 |
| #1808 | issue-1808 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L10 |
| #1809 | issue-1809 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L11 |
| #1810 | issue-1810 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L12 |
| #1811 | issue-1811 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L13 |
| #1812 | issue-1812 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L14 |
| #1813 | issue-1813 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L15 |
| #1814 | S1-019 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L51 |
| #1814 | S1-029 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L52 |
| #1814 | S1-032 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L53; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L123 |
| #1814 | S1-036 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L54; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L15 |
| #1814 | S1-038 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L55; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L7 |
| #1814 | S1-041 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L56 |
| #1814 | S1-042 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L57 |
| #1814 | S1-044 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L58 |
| #1814 | S1-046 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L59 |
| #1814 | S1-050 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L60 |
| #1814 | S1-054 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L61; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L8 |
| #1814 | S1-056 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L62; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L9 |
| #1814 | S1-058 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L63; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L10 |
| #1814 | S1-061 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L64; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L124 |
| #1814 | S1-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L65 |
| #1814 | S1-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L66 |
| #1814 | S1-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L67 |
| #1814 | S1-005 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L68; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L190 |
| #1814 | S1-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L69; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L16 |
| #1814 | S1-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L70; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L17 |
| #1814 | S1-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L71; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L18 |
| #1814 | S6-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L72; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L125 |
| #1814 | S6-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L73; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L147 |
| #1814 | S6-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L74; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L126 |
| #1815 | S2-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L75 |
| #1815 | S2-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L76; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L7 |
| #1815 | S2-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L77; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L8 |
| #1815 | S2-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L78 |
| #1815 | S2-012 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L79 |
| #1815 | S2-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L80; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L9 |
| #1815 | S2-014 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L81 |
| #1815 | S2-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L82; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L10 |
| #1815 | S2b-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L83 |
| #1815 | S2b-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L84 |
| #1815 | S2b-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L85 |
| #1815 | S2b-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L86 |
| #1815 | S2b-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L87 |
| #1815 | S2b-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L88; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L11 |
| #1815 | S2b-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L89; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L12 |
| #1815 | S2c-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L90 |
| #1815 | S2c-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L91 |
| #1815 | S2c-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L92 |
| #1815 | S2c-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L93 |
| #1815 | S2d-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L94 |
| #1815 | S2d-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L95; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L13 |
| #1815 | S2d-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L96 |
| #1815 | S2d-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L97 |
| #1815 | S2d-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L98 |
| #1815 | S2d-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L99; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L14 |
| #1815 | S2d-008 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L100 |
| #1815 | S2d-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L101; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L15 |
| #1815 | S2d-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L102; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L16 |
| #1815 | S2e-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L103; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L17 |
| #1815 | S2e-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L104; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L18 |
| #1815 | S2e-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L105; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L19 |
| #1815 | S2e-004 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L106; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L20 |
| #1815 | S2e-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L107; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L21 |
| #1815 | S2e-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L108; [2026-11-06-harness-medium-review.md](2026-11-06-harness-medium-review.md) L22 |
| #1816 | S3-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L109; [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L7 |
| #1816 | S3-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L110 |
| #1816 | S3-010 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L111 |
| #1816 | S3-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L112; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L191 |
| #1816 | S3b-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L113 |
| #1816 | S3b-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L114 |
| #1816 | S3b-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L115 |
| #1816 | S3b-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L116 |
| #1816 | S3b-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L117 |
| #1816 | S3d-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L118; [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L8 |
| #1816 | S3d-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L119; [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L9 |
| #1816 | S3d-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L120; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L248 |
| #1816 | S3d-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L121 |
| #1816 | S3d-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L122 |
| #1816 | S3d-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L123; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L11 |
| #1816 | S3e-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L124 |
| #1816 | S3e-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L125 |
| #1816 | S3e-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L126 |
| #1816 | S3e-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L127 |
| #1816 | S3e-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L128 |
| #1816 | S3f-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L129 |
| #1816 | S3f-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L130 |
| #1816 | S3f-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L131 |
| #1816 | S3f-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L132 |
| #1816 | S3f-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L133 |
| #1816 | S3f-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L134 |
| #1816 | S3f-008 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L135 |
| #1816 | S3f-009 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L136 |
| #1816 | S3f-010 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L137 |
| #1817 | S3c-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L138 |
| #1817 | S3c-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L139 |
| #1817 | S3c-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L140 |
| #1817 | S3c-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L141 |
| #1817 | S3c-010 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L142 |
| #1817 | S3c-011 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L143 |
| #1817 | S3c-014 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L144 |
| #1817 | S3c-020 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L145 |
| #1817 | S3c-021 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L146 |
| #1817 | S3c-022 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L147 |
| #1817 | S3c-023 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L148 |
| #1817 | S3c-024 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L149 |
| #1818 | S4-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L359 |
| #1818 | S4-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L364 |
| #1818 | S4-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L360 |
| #1818 | S4-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L366 |
| #1818 | S4-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L361 |
| #1818 | S4-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L369 |
| #1818 | S4-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L362 |
| #1818 | S4-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L708 |
| #1818 | S4-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L709 |
| #1818 | S4-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L370 |
| #1818 | S4-021 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L710 |
| #1818 | S4-022 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L711 |
| #1818 | S4-023 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L712 |
| #1818 | S4-024 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L363 |
| #1818 | S4-025 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L7 |
| #1818 | S4-026 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L8 |
| #1818 | S4-027 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L365 |
| #1818 | S4-028 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L367 |
| #1818 | S4-029 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L368 |
| #1818 | S4-030 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L371 |
| #1818 | S4-031 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L372 |
| #1818 | S4b-003 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L9 |
| #1818 | S4b-004 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L10 |
| #1818 | S4b-005 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L11 |
| #1818 | S4b-006 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L12 |
| #1818 | S4b-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L373 |
| #1819 | S4c-001 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L13 |
| #1819 | S4c-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L772 |
| #1819 | S4c-003 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L7 |
| #1819 | S4c-004 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L14 |
| #1819 | S4d-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L374 |
| #1819 | S4d-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L375 |
| #1819 | S4d-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L376 |
| #1819 | S4d-004 | fixed | exact_ledger | [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md) L7 |
| #1819 | S4d-005 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L62 |
| #1819 | S4d-006 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L63 |
| #1819 | S4d-007 | already_fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L64 |
| #1819 | S4d-008 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L65 |
| #1819 | S4e-001 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L15 |
| #1819 | S4e-002 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L16 |
| #1819 | S4e-003 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L17 |
| #1819 | S4e-004 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L18 |
| #1819 | S4e-005 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L19 |
| #1819 | S4e-006 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L20 |
| #1820 | S4f-001 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L58 |
| #1820 | S4f-002 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L59 |
| #1820 | S4f-003 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L60 |
| #1820 | S4f-008 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L21 |
| #1820 | S4f-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L377 |
| #1820 | S4f-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L378 |
| #1821 | S5-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L379 |
| #1821 | S5-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L383 |
| #1821 | S5-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L384 |
| #1821 | S5-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L385 |
| #1821 | S5-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L386 |
| #1821 | S5-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L387 |
| #1821 | S5-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L388 |
| #1821 | S5-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L389 |
| #1821 | S5-023 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L390 |
| #1821 | S5-027 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L10 |
| #1821 | S5-029 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L391 |
| #1821 | S5-031 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L392 |
| #1821 | S5-032 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L393 |
| #1821 | S5-035 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L380 |
| #1821 | S5-036 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L381 |
| #1821 | S5d-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L394 |
| #1821 | S5d-003 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L49 |
| #1821 | S5d-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L382 |
| #1821 | S5d-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L395 |
| #1822 | S5b-002 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L45 |
| #1822 | S5b-003 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L46 |
| #1822 | S5b-004 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L47 |
| #1822 | S5b-005 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L48 |
| #1823 | S5c-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L396 |
| #1823 | S5c-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L397 |
| #1823 | S5c-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L398 |
| #1823 | S5c-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L399 |
| #1823 | S5c-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L400 |
| #1823 | S5c-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L401 |
| #1823 | S5c-010 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L22 |
| #1823 | S5c-011 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L23 |
| #1823 | S5c-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L403 |
| #1823 | S5c-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L404 |
| #1823 | S5c-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L402 |
| #1823 | S5c-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L405 |
| #1823 | S5c-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L406 |
| #1824 | S5e-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L407 |
| #1824 | S5e-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L408 |
| #1824 | S5e-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L409 |
| #1824 | S5e-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L410 |
| #1824 | S5e-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L411 |
| #1824 | S5e-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L412 |
| #1824 | S5e-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L413 |
| #1824 | S5e-021 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L414 |
| #1824 | S5e-025 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L415 |
| #1825 | S5f-001 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L7 |
| #1825 | S5f-002 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L8 |
| #1825 | S5f-003 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L9 |
| #1825 | S5f-004 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L10 |
| #1825 | S5f-005 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L11 |
| #1825 | S5f-006 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L12 |
| #1825 | S5f-007 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L13 |
| #1826 | S7-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L26 |
| #1826 | S7-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L27 |
| #1826 | S7-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L28 |
| #1826 | S7a-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L29 |
| #1826 | S7a-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L30 |
| #1826 | S7a-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L31 |
| #1826 | S7a-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L32 |
| #1826 | S7a-017 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L33 |
| #1826 | S7a-019 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L34 |
| #1826 | S7a-021 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L35 |
| #1826 | S7a-025 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L36 |
| #1826 | S7a-026 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L37 |
| #1826 | S7a2-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L38 |
| #1826 | S7a2-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L39 |
| #1826 | S7a2-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L40 |
| #1826 | S7a2-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L41 |
| #1826 | S7a3-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L42 |
| #1826 | S7a3-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L43 |
| #1826 | S7a3-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L44 |
| #1826 | S7a3-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L45 |
| #1826 | S7a4-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L46 |
| #1826 | S7a4-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L47 |
| #1826 | S7a4-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L48 |
| #1826 | S7a4-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L49 |
| #1826 | S7a4-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L50 |
| #1826 | S7b3-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L51 |
| #1826 | S7b3-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L52 |
| #1827 | S7c-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L53 |
| #1827 | S7c-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L54 |
| #1827 | S7c-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L55 |
| #1827 | S7c-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L56 |
| #1827 | S7c-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L57 |
| #1827 | S7c-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L58 |
| #1828 | S7b-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L59 |
| #1828 | S7b-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L60 |
| #1828 | S7b-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L61 |
| #1828 | S7b-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L62 |
| #1828 | S7b-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L63 |
| #1828 | S7b2-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L64 |
| #1828 | S7b2-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L65 |
| #1828 | S7b2-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L66 |
| #1828 | S7b2-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L67 |
| #1828 | S7b2-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L68 |
| #1828 | S7b2-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L69 |
| #1828 | S7b2-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L70 |
| #1828 | S7b2-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L71 |
| #1837 | issue-1837 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L16 |
| #1838 | issue-1838 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L17 |
| #1839 | issue-1839 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L18 |
| #1840 | issue-1840 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L19 |
| #1841 | issue-1841 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L20 |
| #1842 | issue-1842 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L21 |
| #1843 | issue-1843 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L22 |
| #1844 | issue-1844 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L23 |
| #1845 | issue-1845 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L24 |
| #1846 | issue-1846 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L25 |
| #1847 | issue-1847 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L26 |
| #1848 | S7b4-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L72 |
| #1848 | S7b4-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L73 |
| #1848 | S7b4-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L74 |
| #1848 | S7b4-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L75 |
| #1848 | S7b4-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L76 |
| #1849 | S7d-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L77 |
| #1849 | S7d-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L78 |
| #1849 | S7d-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L79 |
| #1849 | S7d-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L80 |
| #1849 | S7d-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L81 |
| #1850 | S7e-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L82 |
| #1850 | S7e-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L83 |
| #1850 | S7e-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L84 |
| #1850 | S7e-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L85 |
| #1850 | S7e-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L86 |
| #1850 | S7e-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L87 |
| #1850 | S7e-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L88 |
| #1850 | S7e-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L89 |
| #1851 | S7f-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L90 |
| #1851 | S7f-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L91 |
| #1851 | S7f-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L92 |
| #1851 | S7f-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L93 |
| #1852 | S7g-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L94 |
| #1852 | S7g-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L95 |
| #1852 | S7g-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L96 |
| #1852 | S7g-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L97 |
| #1852 | S7g-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L98 |
| #1852 | S7g-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L99 |
| #1852 | S7g-019 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L100 |
| #1852 | S7g-021 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L101 |
| #1852 | S7h-001 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L102 |
| #1852 | S7h-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L103 |
| #1852 | S7h-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L104 |
| #1852 | S7h-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L105 |
| #1852 | S7h-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L106 |
| #1853 | S4g-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L150; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L237; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L7 |
| #1853 | S4g-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L151; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L238; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L8 |
| #1853 | S4g-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L152; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L239; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L9 |
| #1853 | S4g-004 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L153; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L240; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L10 |
| #1853 | S4g-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L154; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L13 |
| #1853 | S4g-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L155; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L14 |
| #1853 | S4g-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L156; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L15 |
| #1853 | S4g-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L157; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L16 |
| #1853 | S4g-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L158; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L17 |
| #1854 | S4h-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L159; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L474 |
| #1854 | S4h-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L160; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L475 |
| #1854 | S4h-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L161; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L476 |
| #1854 | S4h-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L162; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L18 |
| #1854 | S4h-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L163; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L19 |
| #1854 | S4h-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L164; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L20 |
| #1855 | S4i-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L165 |
| #1855 | S4i-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L166 |
| #1855 | S4i-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L167 |
| #1855 | S4i-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L168 |
| #1855 | S4i-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L169 |
| #1855 | S4i-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L170; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L477 |
| #1855 | S4i-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L171; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L478 |
| #1855 | S4i-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L172; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L479 |
| #1855 | S4i-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L173; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L480 |
| #1855 | S4i-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L174; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L481 |
| #1856 | S4j-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L175 |
| #1856 | S4j-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L176 |
| #1856 | S4j-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L177 |
| #1856 | S4j-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L178 |
| #1856 | S4j-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L179 |
| #1857 | S4k-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L180 |
| #1857 | S4k-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L181 |
| #1857 | S4k-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L182 |
| #1857 | S4k-009 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L183 |
| #1857 | S4k-010 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L184 |
| #1857 | S4k-011 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L185 |
| #1857 | S4k-015 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L186 |
| #1858 | S4l-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L187; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L21 |
| #1858 | S4l-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L188; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L22 |
| #1858 | S4l-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L189; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L23 |
| #1858 | S4l-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L190; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L24 |
| #1858 | S4l-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L191; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L25 |
| #1858 | S4l-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L192; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L26 |
| #1858 | S4l-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L193; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L27 |
| #1858 | S4l-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L194; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L28 |
| #1859 | S4m-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L195 |
| #1859 | S4m-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L196 |
| #1859 | S4m-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L197 |
| #1859 | S4m-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L198 |
| #1859 | S4m-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L199 |
| #1859 | S4m-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L200 |
| #1859 | S4m-008 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L201 |
| #1860 | S1-015 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L202; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L210 |
| #1860 | S1-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L203; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L211 |
| #1860 | S1-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L204; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L212 |
| #1860 | S1-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L205; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L213 |
| #1860 | S1-020 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L206; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L214 |
| #1860 | S1-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L207; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L7 |
| #1860 | S1-022 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L208; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L8 |
| #1860 | S1-023 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L209; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L7 |
| #1860 | S1-024 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L210; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L8 |
| #1860 | S1-025 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L211; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L128 |
| #1860 | S1-026 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L212; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L129 |
| #1860 | S1-027 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L213; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L130 |
| #1860 | S1-028 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L214; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L131 |
| #1860 | S1-030 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L215; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L9 |
| #1860 | S1-031 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L216; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L150 |
| #1860 | S1-033 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L217; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L149; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L9 |
| #1860 | S1-034 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L218; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L192 |
| #1860 | S1-035 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L219; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L193 |
| #1860 | S1-037 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L220; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L10 |
| #1860 | S1-039 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L221; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L215 |
| #1860 | S1-040 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L222; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L11 |
| #1860 | S1-043 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L223; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L12 |
| #1860 | S1-045 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L224; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L132 |
| #1860 | S1-047 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L225; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L13 |
| #1860 | S1-048 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L226; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L247; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L13 |
| #1860 | S1-049 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L227; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L194 |
| #1860 | S1-051 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L228; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L151 |
| #1860 | S1-052 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L229; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L133 |
| #1860 | S1-053 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L230; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L14 |
| #1860 | S1-055 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L231; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L232 |
| #1860 | S1-057 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L232; [2026-11-06-api-low-review.md](2026-11-06-api-low-review.md) L15 |
| #1860 | S1-059 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L233; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L249 |
| #1860 | S1-060 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L234; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L21 |
| #1860 | S1-062 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L235; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L195 |
| #1860 | S1-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L236; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L127 |
| #1860 | S1-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L237; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L203 |
| #1860 | S1-011 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L238; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L204 |
| #1860 | S1-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L239; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L19 |
| #1860 | S1-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L240; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L20 |
| #1860 | S1-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L241; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L205 |
| #1861 | S2-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L242; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L7 |
| #1861 | S2-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L243; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L8 |
| #1861 | S2-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L244; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L9 |
| #1861 | S2-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L245; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L10 |
| #1861 | S2-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L246; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L11 |
| #1861 | S2-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L247; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L12 |
| #1861 | S2-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L248; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L13 |
| #1861 | S2-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L249; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L14 |
| #1861 | S2-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L250; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L15 |
| #1861 | S2-020 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L251; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L16 |
| #1861 | S2b-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L252; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L17 |
| #1861 | S2b-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L253; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L18 |
| #1861 | S2b-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L254; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L19 |
| #1861 | S2b-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L255; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L20 |
| #1861 | S2b-012 | no_valid_description | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L256; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L21 |
| #1861 | S2b-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L257; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L22 |
| #1861 | S2b-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L258; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L23 |
| #1861 | S2b-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L259; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L24 |
| #1861 | S2c-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L260; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L25 |
| #1861 | S2c-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L261; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L26 |
| #1861 | S2c-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L262; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L27 |
| #1861 | S2c-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L263; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L28 |
| #1861 | S2c-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L264; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L29 |
| #1861 | S2c-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L265; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L30 |
| #1861 | S2c-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L266; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L31 |
| #1861 | S2c-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L267; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L32 |
| #1861 | S2c-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L268; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L33 |
| #1861 | S2c-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L269; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L34 |
| #1861 | S2c-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L270; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L35 |
| #1861 | S2c-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L271; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L36 |
| #1861 | S2c-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L272; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L37 |
| #1861 | S2c-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L273; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L38 |
| #1861 | S2c-020 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L274; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L39 |
| #1861 | S2c-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L275; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L40 |
| #1861 | S2d-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L276; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L41 |
| #1861 | S2d-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L277; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L42 |
| #1861 | S2d-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L278; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L43 |
| #1861 | S2d-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L279; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L44 |
| #1861 | S2d-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L280; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L45 |
| #1861 | S2d-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L281; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L46 |
| #1861 | S2d-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L282; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L47 |
| #1861 | S2d-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L283; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L48 |
| #1861 | S2d-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L284; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L49 |
| #1861 | S2d-020 | no_valid_description | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L285; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L50 |
| #1861 | S2e-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L286; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L51 |
| #1861 | S2e-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L287; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L52 |
| #1861 | S2e-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L288; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L53 |
| #1861 | S2e-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L289; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L54 |
| #1861 | S2e-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L290; [2026-11-05-core-low-1861.md](2026-11-05-core-low-1861.md) L55 |
| #1862 | S3-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L291; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L170 |
| #1862 | S3-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L292; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L171 |
| #1862 | S3-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L293; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L178 |
| #1862 | S3-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L294; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L172 |
| #1862 | S3-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L295; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L216 |
| #1862 | S3-012 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L296; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L173 |
| #1862 | S3-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L297; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L217 |
| #1862 | S3-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L298; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L176 |
| #1862 | S3-015 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L299; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L233 |
| #1862 | S3-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L300; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L218 |
| #1862 | S3-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L301; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L219 |
| #1862 | S3-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L302; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L220 |
| #1862 | S3-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L303; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L221 |
| #1862 | S3-020 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L304; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L222 |
| #1862 | S3-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L305; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L174 |
| #1862 | S3-022 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L306; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L223 |
| #1862 | S3-023 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L307; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L175 |
| #1862 | S3-024 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L308; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L198 |
| #1862 | S3b-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L309; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L79 |
| #1862 | S3b-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L310; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L80 |
| #1862 | S3b-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L311; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L49 |
| #1862 | S3b-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L312; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L81 |
| #1862 | S3b-011 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L313; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L82 |
| #1862 | S3b-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L314; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L83 |
| #1862 | S3b-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L315; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L84 |
| #1862 | S3b-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L316; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L50 |
| #1862 | S3b-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L317; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L85 |
| #1862 | S3b-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L318; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L10 |
| #1862 | S3b-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L319; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L11 |
| #1862 | S3b-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L320; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L12 |
| #1862 | S3b-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L321; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L13 |
| #1862 | S3b-020 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L322; [2026-11-06-data-api-local-low-review.md](2026-11-06-data-api-local-low-review.md) L14 |
| #1862 | S3b-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L323; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L86 |
| #1862 | S3b-022 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L324; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L87 |
| #1862 | S3b-023 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L325; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L51 |
| #1862 | S3b-024 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L326; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L16 |
| #1862 | S3b-025 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L327; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L8 |
| #1862 | S3b-026 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L328; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L17 |
| #1862 | S3b-027 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L329; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L18 |
| #1862 | S3b-028 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L330; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L19 |
| #1862 | S3b-029 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L331; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L88 |
| #1862 | S3b-030 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L332; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L89 |
| #1862 | S3c-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L333; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L103 |
| #1862 | S3c-007 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L334; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L104 |
| #1862 | S3c-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L335; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L105 |
| #1862 | S3c-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L336; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L106 |
| #1862 | S3c-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L337; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L107 |
| #1862 | S3c-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L338; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L108 |
| #1862 | S3c-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L339; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L109 |
| #1862 | S3c-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L340; [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md) L7 |
| #1862 | S3c-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L341; [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md) L8 |
| #1862 | S3c-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L342; [2026-11-06-context-assembly-low-review.md](2026-11-06-context-assembly-low-review.md) L9 |
| #1862 | S3c-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L343; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L7 |
| #1862 | S3c-025 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L344; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L9 |
| #1862 | S3c-026 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L345; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L10 |
| #1862 | S3c-027 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L346; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L11 |
| #1862 | S3c-028 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L347; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L12 |
| #1862 | S3c-029 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L348; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L13 |
| #1862 | S3c-030 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L349; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L14 |
| #1862 | S3c-031 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L350; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L15 |
| #1862 | S3d-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L351; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L122 |
| #1862 | S3d-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L352; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L748 |
| #1862 | S3d-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L353; [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md) L7 |
| #1862 | S3d-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L354; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L199 |
| #1862 | S3d-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L355; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L749 |
| #1862 | S3d-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L356; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L750 |
| #1862 | S3d-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L357; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L200 |
| #1862 | S3d-014 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L358; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L110 |
| #1862 | S3d-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L359; [2026-11-06-workflow-export-low-review.md](2026-11-06-workflow-export-low-review.md) L20 |
| #1862 | S3d-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L360; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L111 |
| #1862 | S3d-017 | not_a_bug | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L361; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L112 |
| #1862 | S3e-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L362; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L416 |
| #1862 | S3e-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L363; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L417 |
| #1862 | S3e-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L364; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L418 |
| #1862 | S3e-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L365; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L419 |
| #1862 | S3e-011 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L366; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L113 |
| #1862 | S3e-012 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L367; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L114 |
| #1862 | S3e-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L368; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L148 |
| #1862 | S3f-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L369; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L134 |
| #1862 | S3f-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L370; [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md) L8 |
| #1862 | S3f-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L371; [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md) L9 |
| #1862 | S3f-014 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L372; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L115 |
| #1862 | S3f-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L373; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L116 |
| #1862 | S3f-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L374; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L117 |
| #1862 | S3f-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L375; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L118 |
| #1862 | S3f-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L376; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L119 |
| #1862 | S3f-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L377; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L120 |
| #1862 | S3f-020 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L378; [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md) L10 |
| #1862 | S3f-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L379; [2026-11-06-runtime-service-low-review.md](2026-11-06-runtime-service-low-review.md) L11 |
| #1862 | S3f-022 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L380; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L135 |
| #1862 | S3f-023 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L381; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L121 |
| #1863 | S4-010 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L9 |
| #1863 | S4-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L420 |
| #1863 | S4-012 | fixed | exact_ledger | [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md) L12 |
| #1863 | S4-032 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L421 |
| #1863 | S4-033 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L422 |
| #1863 | S4-034 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L423 |
| #1863 | S4-035 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L424 |
| #1863 | S4-036 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L52 |
| #1863 | S4-037 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L53 |
| #1863 | S4-038 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L425; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L54 |
| #1863 | S4-039 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L426 |
| #1863 | S4-040 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L11 |
| #1863 | S4-041 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L12 |
| #1863 | S4-042 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L427 |
| #1863 | S4-043 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L428 |
| #1863 | S4-044 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L429 |
| #1863 | S4-045 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L430 |
| #1863 | S4-046 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L431 |
| #1863 | S4-047 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L432 |
| #1863 | S4b-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L433 |
| #1863 | S4b-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L434 |
| #1863 | S4b-010 | already_fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L13 |
| #1863 | S4b-011 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L14 |
| #1863 | S4b-012 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L15 |
| #1863 | S4b-013 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L16 |
| #1863 | S4b-014 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L17 |
| #1863 | S4c-005 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L8 |
| #1863 | S4c-006 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L7 |
| #1863 | S4c-007 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L8 |
| #1863 | S4c-008 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L9 |
| #1863 | S4c-009 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L10 |
| #1863 | S4c-010 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L11 |
| #1863 | S4c-011 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L12 |
| #1863 | S4c-012 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L13 |
| #1863 | S4c-013 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L14 |
| #1863 | S4c-014 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L15 |
| #1863 | S4c-015 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L16 |
| #1863 | S4c-016 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L17; [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L43 |
| #1863 | S4d-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L435 |
| #1863 | S4d-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L436 |
| #1863 | S4d-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L437 |
| #1863 | S4d-012 | fixed | exact_ledger | [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md) L8 |
| #1863 | S4d-013 | fixed | exact_ledger | [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md) L9 |
| #1863 | S4d-014 | fixed | exact_ledger | [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md) L10 |
| #1863 | S4d-015 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L21 |
| #1863 | S4d-016 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L22 |
| #1863 | S4d-017 | already_fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L23 |
| #1863 | S4d-018 | fixed | exact_ledger | [2026-11-06-planning-stac-closure-review.md](2026-11-06-planning-stac-closure-review.md) L11 |
| #1863 | S4e-007 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L25 |
| #1863 | S4e-008 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L26 |
| #1863 | S4e-009 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L27 |
| #1863 | S4e-010 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L28 |
| #1863 | S4e-011 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L29 |
| #1863 | S4e-012 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L30 |
| #1863 | S4e-013 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L31 |
| #1863 | S4e-014 | already_fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L32 |
| #1863 | S4e-015 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L33 |
| #1863 | S4e-016 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L34 |
| #1863 | S4e-017 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L10 |
| #1863 | S4e-018 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L35 |
| #1863 | S4f-004 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L36 |
| #1863 | S4f-005 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L37 |
| #1863 | S4f-006 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L38 |
| #1863 | S4f-007 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L39 |
| #1863 | S4f-009 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L11; [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L40 |
| #1863 | S4f-010 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L12; [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L41 |
| #1863 | S4f-011 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L13; [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L42 |
| #1863 | S4f-012 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L14; [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L43 |
| #1863 | S4f-013 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L15; [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L44 |
| #1863 | S4f-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L651 |
| #1863 | S4f-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L652 |
| #1863 | S4g-005 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L18 |
| #1863 | S4g-006 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L19 |
| #1863 | S4g-007 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L20 |
| #1863 | S4g-008 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L21 |
| #1863 | S4g-014 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L22 |
| #1863 | S4g-015 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L23 |
| #1863 | S4g-016 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L24 |
| #1863 | S4g-017 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L25 |
| #1863 | S4g-018 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L26 |
| #1863 | S4g-019 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L27 |
| #1863 | S4g-021 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L90 |
| #1863 | S4g-022 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L91 |
| #1863 | S4g-023 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L92 |
| #1863 | S4h-007 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L93 |
| #1863 | S4h-008 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L94 |
| #1863 | S4h-009 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L95 |
| #1863 | S4h-010 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L45 |
| #1863 | S4h-011 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L46 |
| #1863 | S4h-012 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L47 |
| #1863 | S4h-013 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L48 |
| #1863 | S4h-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L438 |
| #1863 | S4h-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L439 |
| #1863 | S4h-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L440 |
| #1863 | S4i-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L441 |
| #1863 | S4i-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L442 |
| #1863 | S4i-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L443 |
| #1863 | S4i-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L444 |
| #1863 | S4i-016 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L28 |
| #1863 | S4i-017 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L29 |
| #1863 | S4i-018 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L30 |
| #1863 | S4i-019 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L31 |
| #1863 | S4i-020 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L32 |
| #1863 | S4i-021 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L33 |
| #1863 | S4i-022 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L34 |
| #1863 | S4j-008 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L49 |
| #1863 | S4j-009 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L50 |
| #1863 | S4j-010 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L51 |
| #1863 | S4j-011 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L52 |
| #1863 | S4j-012 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L53 |
| #1863 | S4j-013 | fixed | exact_ledger | [2026-11-06-service-low-1863-review.md](2026-11-06-service-low-1863-review.md) L54 |
| #1863 | S4j-014 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L16 |
| #1863 | S4j-015 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L17 |
| #1863 | S4j-016 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L18 |
| #1863 | S4k-001 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L35 |
| #1863 | S4k-002 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L36 |
| #1863 | S4k-003 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L37 |
| #1863 | S4k-004 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L38 |
| #1863 | S4k-008 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L39 |
| #1863 | S4k-012 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L40 |
| #1863 | S4k-013 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L41 |
| #1863 | S4k-016 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L42 |
| #1863 | S4k-017 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L43 |
| #1863 | S4k-018 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L44 |
| #1863 | S4k-019 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L45 |
| #1863 | S4k-020 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L46 |
| #1863 | S4k-021 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L47 |
| #1863 | S4k-022 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L48; [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L29 |
| #1863 | S4k-023 | fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L49 |
| #1863 | S4m-009 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L19 |
| #1863 | S4m-010 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L20 |
| #1863 | S4m-011 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L21 |
| #1863 | S4m-012 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L22 |
| #1863 | S4m-013 | not_a_bug | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L23 |
| #1863 | S4m-014 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L24 |
| #1864 | S5-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L445 |
| #1864 | S5-003 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L26 |
| #1864 | S5-004 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L27 |
| #1864 | S5-005 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L28 |
| #1864 | S5-006 | not_a_bug | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L25 |
| #1864 | S5-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L446 |
| #1864 | S5-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L447 |
| #1864 | S5-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L448 |
| #1864 | S5-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L449 |
| #1864 | S5-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L450 |
| #1864 | S5-021 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L451 |
| #1864 | S5-024 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L452 |
| #1864 | S5-025 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L29 |
| #1864 | S5-026 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L30 |
| #1864 | S5-028 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L31 |
| #1864 | S5-030 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L453 |
| #1864 | S5-033 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L454 |
| #1864 | S5-034 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L455 |
| #1864 | S5b-006 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L32 |
| #1864 | S5b-007 | already_fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L33 |
| #1864 | S5b-008 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L34 |
| #1864 | S5b-009 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L35 |
| #1864 | S5d-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L456 |
| #1864 | S5d-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L653 |
| #1864 | S5d-008 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L36 |
| #1864 | S5d-009 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L73 |
| #1864 | S5d-010 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L74 |
| #1864 | S5d-011 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L75 |
| #1864 | S5d-012 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L76 |
| #1864 | S5d-013 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L77 |
| #1864 | S5d-014 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L78 |
| #1864 | S5d-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L457 |
| #1864 | S5e-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L458 |
| #1864 | S5e-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L459; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L55 |
| #1864 | S5e-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L460 |
| #1864 | S5e-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L461 |
| #1864 | S5e-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L462 |
| #1864 | S5e-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L463 |
| #1864 | S5e-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L464 |
| #1864 | S5e-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L465 |
| #1864 | S5e-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L466 |
| #1864 | S5e-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L467 |
| #1864 | S5e-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L468 |
| #1864 | S5e-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L469 |
| #1864 | S5e-022 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L470 |
| #1864 | S5e-023 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L471 |
| #1864 | S5e-024 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L472 |
| #1864 | S5e-026 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L473 |
| #1864 | S5f-008 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L14 |
| #1864 | S5f-009 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L15 |
| #1864 | S5f-010 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L16 |
| #1864 | S5f-011 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L17 |
| #1864 | S5f-012 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L18 |
| #1864 | S5f-013 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L19 |
| #1864 | S5f-014 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L20 |
| #1864 | S5f-015 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L21 |
| #1864 | S5f-016 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L22 |
| #1864 | S5f-017 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L23 |
| #1864 | S5f-018 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L24 |
| #1864 | S5f-019 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L25 |
| #1864 | S5f-020 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L26 |
| #1864 | S5f-021 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L27 |
| #1864 | S5f-022 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L28 |
| #1864 | S5f-023 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L29 |
| #1864 | S5f-024 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L30 |
| #1864 | S5f-025 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L31 |
| #1864 | S5f-026 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L32 |
| #1864 | S5f-027 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L33 |
| #1864 | S5f-028 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L34 |
| #1864 | S5f-029 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L35 |
| #1864 | S5f-030 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L36 |
| #1864 | S5f-031 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L37 |
| #1864 | S5f-032 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L38 |
| #1864 | S5f-033 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L39 |
| #1864 | S5f-034 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L40 |
| #1864 | S5f-035 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L41 |
| #1864 | S5f-036 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L42 |
| #1864 | S5f-037 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L43 |
| #1864 | S5f-038 | fixed | exact_ledger | [2026-11-05-export-harness-review.md](2026-11-05-export-harness-review.md) L44 |
| #1865 | S6-005 | not_a_bug | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L22 |
| #1865 | S6-006 | not_a_bug | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L23 |
| #1865 | S6-007 | not_a_bug | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L24 |
| #1865 | S6-008 | fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L25 |
| #1865 | S6-009 | fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L26 |
| #1866 | S7-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L107 |
| #1866 | S7-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L108 |
| #1866 | S7-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L109 |
| #1866 | S7-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L110 |
| #1866 | S7-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L111 |
| #1866 | S7-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L112 |
| #1866 | S7-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L113 |
| #1866 | S7-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L114 |
| #1866 | S7-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L115 |
| #1866 | S7a-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L116 |
| #1866 | S7a-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L117 |
| #1866 | S7a-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L118 |
| #1866 | S7a-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L119 |
| #1866 | S7a-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L120 |
| #1866 | S7a-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L121 |
| #1866 | S7a-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L122 |
| #1866 | S7a-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L123 |
| #1866 | S7a-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L124 |
| #1866 | S7a-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L125 |
| #1866 | S7a-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L126 |
| #1866 | S7a-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L127 |
| #1866 | S7a-018 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L128 |
| #1866 | S7a-020 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L129 |
| #1866 | S7a-022 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L130 |
| #1866 | S7a-023 | not_a_bug | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L131 |
| #1866 | S7a-024 | not_a_bug | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L132 |
| #1866 | S7a-027 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L133 |
| #1866 | S7a2-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L134 |
| #1866 | S7a2-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L135 |
| #1866 | S7a2-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L136 |
| #1866 | S7a2-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L137 |
| #1866 | S7a2-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L138 |
| #1866 | S7a2-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L139 |
| #1866 | S7a3-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L140 |
| #1866 | S7a3-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L141 |
| #1866 | S7a3-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L142 |
| #1866 | S7a3-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L143 |
| #1866 | S7a3-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L144 |
| #1866 | S7a3-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L145 |
| #1866 | S7a3-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L146 |
| #1866 | S7a4-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L147 |
| #1866 | S7a4-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L148 |
| #1866 | S7a4-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L149 |
| #1866 | S7a4-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L150 |
| #1866 | S7a4-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L151 |
| #1866 | S7a4-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L152 |
| #1866 | S7a4-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L153 |
| #1866 | S7b-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L154 |
| #1866 | S7b-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L155 |
| #1866 | S7b-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L156 |
| #1866 | S7b-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L157 |
| #1866 | S7b-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L158 |
| #1866 | S7b-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L159 |
| #1866 | S7b-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L160 |
| #1866 | S7b-014 | not_a_bug | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L161 |
| #1866 | S7b-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L162 |
| #1866 | S7b-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L163 |
| #1866 | S7b2-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L164 |
| #1866 | S7b2-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L165 |
| #1866 | S7b2-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L166 |
| #1866 | S7b2-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L167 |
| #1866 | S7b2-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L168 |
| #1866 | S7b2-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L169 |
| #1866 | S7b2-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L170 |
| #1866 | S7b2-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L171 |
| #1866 | S7b2-017 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L172 |
| #1866 | S7b2-018 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L173 |
| #1866 | S7b2-019 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L174; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L7 |
| #1866 | S7b2-020 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L175 |
| #1866 | S7b2-021 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L176 |
| #1866 | S7b2-022 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L177 |
| #1866 | S7b2-023 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L178 |
| #1866 | S7b3-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L179 |
| #1866 | S7b3-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L180 |
| #1866 | S7b3-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L181 |
| #1866 | S7b3-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L182 |
| #1866 | S7b3-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L183 |
| #1866 | S7b3-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L184 |
| #1866 | S7b3-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L185 |
| #1866 | S7b3-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L186 |
| #1866 | S7b3-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L187 |
| #1866 | S7b3-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L188 |
| #1866 | S7c-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L189 |
| #1866 | S7c-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L190 |
| #1866 | S7c-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L191 |
| #1866 | S7c-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L192 |
| #1866 | S7c-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L193 |
| #1866 | S7c-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L194 |
| #1866 | S7c-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L195 |
| #1866 | S7c-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L196 |
| #1866 | S7c-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L197 |
| #1866 | S7c-017 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L198; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L8 |
| #1866 | S7c-018 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L199 |
| #1866 | S7c-019 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L200 |
| #1866 | S7d-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L201 |
| #1866 | S7d-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L202 |
| #1866 | S7d-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L203 |
| #1866 | S7d-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L204 |
| #1866 | S7d-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L205 |
| #1866 | S7d-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L206 |
| #1866 | S7d-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L207 |
| #1866 | S7d-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L208 |
| #1866 | S7d-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L209 |
| #1866 | S7d-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L210 |
| #1866 | S7d-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L211 |
| #1866 | S7d-017 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L212 |
| #1866 | S7f-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L213; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L9 |
| #1866 | S7f-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L214; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L10 |
| #1866 | S7f-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L215; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L11 |
| #1866 | S7f-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L216; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L12 |
| #1866 | S7f-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L217; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L13 |
| #1866 | S7f-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L218; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L14 |
| #1866 | S7f-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L219; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L15 |
| #1866 | S7f-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L220; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L16 |
| #1866 | S7f-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L221; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L17 |
| #1866 | S7f-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L222; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L18 |
| #1866 | S7f-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L223; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L19 |
| #1866 | S7g-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L224; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L20 |
| #1866 | S7g-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L225; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L21 |
| #1866 | S7g-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L226; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L22 |
| #1866 | S7g-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L227; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L23 |
| #1866 | S7g-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L228; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L24 |
| #1866 | S7g-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L229; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L25 |
| #1866 | S7g-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L230 |
| #1866 | S7g-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L231 |
| #1866 | S7g-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L232; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L26 |
| #1866 | S7g-017 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L233; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L27 |
| #1866 | S7g-018 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L234; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L28 |
| #1866 | S7g-020 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L235; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L29 |
| #1866 | S7g-022 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L236; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L30 |
| #1866 | S7g-023 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L237; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L31 |
| #1866 | S7h-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L238 |
| #1866 | S7h-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L239 |
| #1866 | S7h-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L240; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L32 |
| #1866 | S7h-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L241 |
| #1866 | S7h-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L242; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L33 |
| #1866 | S7h-011 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L243 |
| #1866 | S7h-012 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L244; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L34 |
| #1866 | S7h-013 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L245; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L35 |
| #1866 | S7h-014 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L246 |
| #1866 | S7h-015 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L247; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L36 |
| #1866 | S7h-016 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L248; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L37 |
| #1866 | S7h-017 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L249; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L38 |
| #1866 | S7h-018 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L250 |
| #1866 | S7h-019 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L251 |
| #1866 | S7h-020 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L252; [2026-11-05-ui-low-issues.md](2026-11-05-ui-low-issues.md) L39 |
| #1866 | S7i-002 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L253 |
| #1866 | S7i-003 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L254 |
| #1866 | S7i-004 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L255 |
| #1866 | S7i-005 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L256 |
| #1866 | S7i-006 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L257 |
| #1866 | S7i-007 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L258 |
| #1866 | S7i-008 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L259 |
| #1866 | S7i-009 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L260 |
| #1866 | S7i-010 | fixed | exact_ledger | [2026-11-05-frontend-issues.md](2026-11-05-frontend-issues.md) L261 |
| #1890 | S1-001 | already_fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L11 |
| #1891 | S1-002 | already_fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L12 |
| #1892 | S3-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L27 |
| #1893 | S4-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L28 |
| #1894 | S4-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L29 |
| #1895 | S11-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L30 |
| #1896 | S12-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L31 |
| #1897 | S13-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L32 |
| #1898 | S13-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L33 |
| #1899 | S53-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L34 |
| #1900 | S54-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L39 |
| #1901 | S54-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L40 |
| #1902 | S55-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L41 |
| #1903 | S56-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L42 |
| #1904 | S57-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L43 |
| #1905 | S58-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L44 |
| #1906 | S59-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L790 |
| #1907 | S59-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L791 |
| #1908 | S59-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L792 |
| #1909 | S96-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L48 |
| #1910 | S99-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L49 |
| #1911 | S103-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L793 |
| #1912 | S104-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L51 |
| #1913 | S105-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L52 |
| #1914 | S1-003 | fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L13 |
| #1914 | S1-005 | fixed | exact_ledger | [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L14 |
| #1915 | S2-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L382 |
| #1915 | S2-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L383 |
| #1916 | unlabeled-01 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L27 |
| #1916 | unlabeled-02 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L28 |
| #1917 | S3-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L384 |
| #1917 | S3-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L385 |
| #1917 | S3-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L386 |
| #1917 | S3-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L387; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L29 |
| #1917 | S3-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L388; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L30 |
| #1917 | S3-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L389; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L31 |
| #1918 | S4-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L390 |
| #1918 | S4-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L391 |
| #1918 | S4-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L392 |
| #1918 | S4-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L393 |
| #1918 | S4-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L394 |
| #1919 | S5-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L395 |
| #1919 | S5-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L396; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L32 |
| #1919 | S5-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L397; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L33 |
| #1920 | S6-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L398; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L34 |
| #1920 | S6-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L399; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L35 |
| #1920 | S6-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L400; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L36 |
| #1920 | S6-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L401; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L34 |
| #1921 | S7-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L402 |
| #1921 | S7-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L403 |
| #1921 | S7-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L404 |
| #1921 | S7-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L405; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L196 |
| #1922 | S8-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L406 |
| #1922 | S8-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L407 |
| #1922 | S8-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L408; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L37 |
| #1923 | S9-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L409 |
| #1924 | S10-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L410 |
| #1924 | S10-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L411 |
| #1924 | S10-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L412 |
| #1924 | S10-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L413 |
| #1924 | S10-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L414; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L37 |
| #1924 | S10-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L415; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L38 |
| #1925 | S11-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L416 |
| #1925 | S11-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L417 |
| #1925 | S11-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L418 |
| #1926 | S12-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L419 |
| #1926 | S12-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L420 |
| #1926 | S12-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L421 |
| #1926 | S12-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L422 |
| #1926 | S12-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L423 |
| #1926 | S12-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L424 |
| #1926 | S12-008 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L425 |
| #1927 | S13-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L426; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L35 |
| #1927 | S13-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L427 |
| #1927 | S13-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L428 |
| #1927 | S13-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L429 |
| #1927 | S13-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L430 |
| #1928 | S49-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L431 |
| #1928 | S49-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L432 |
| #1928 | S49-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L433 |
| #1928 | S49-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L434 |
| #1928 | S49-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L435 |
| #1928 | S49-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L436 |
| #1928 | S49-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L437; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L102 |
| #1928 | S49-008 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L438 |
| #1928 | S49-009 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L439 |
| #1928 | S49-010 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L440 |
| #1928 | S49-011 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L441 |
| #1929 | S50-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L442; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L209 |
| #1929 | S50-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L443 |
| #1929 | S50-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L444 |
| #1929 | S50-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L445 |
| #1929 | S50-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L446; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L16 |
| #1930 | S51-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L447 |
| #1930 | S51-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L448 |
| #1930 | S51-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L449 |
| #1930 | S51-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L450 |
| #1931 | S52-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L451 |
| #1931 | S52-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L452 |
| #1931 | S52-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L453 |
| #1932 | S53-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L454 |
| #1932 | S53-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L455 |
| #1932 | S53-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L456 |
| #1932 | S53-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L457 |
| #1933 | S54-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L543 |
| #1933 | S54-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L544 |
| #1933 | S54-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L545 |
| #1933 | S54-006 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L24 |
| #1933 | S54-007 | fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L25 |
| #1933 | S54-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L546 |
| #1934 | S55-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L547 |
| #1934 | S55-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L548 |
| #1934 | S55-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L549 |
| #1934 | S55-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L550 |
| #1934 | S55-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L551 |
| #1934 | S55-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L552 |
| #1934 | S55-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L713 |
| #1934 | S55-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L553 |
| #1934 | S55-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L554 |
| #1934 | S55-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L555 |
| #1934 | S55-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L556 |
| #1934 | S55-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L557 |
| #1934 | S55-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L558 |
| #1935 | S56-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L559 |
| #1935 | S56-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L560 |
| #1935 | S56-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L561 |
| #1935 | S56-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L562 |
| #1935 | S56-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L563 |
| #1935 | S56-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L564 |
| #1935 | S56-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L714 |
| #1935 | S56-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L565 |
| #1935 | S56-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L566 |
| #1935 | S56-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L567 |
| #1935 | S56-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L568 |
| #1935 | S56-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L569 |
| #1936 | S57-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L570 |
| #1936 | S57-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L571 |
| #1936 | S57-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L572 |
| #1936 | S57-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L573 |
| #1936 | S57-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L574 |
| #1936 | S57-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L778; [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L52 |
| #1936 | S57-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L575 |
| #1936 | S57-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L576 |
| #1937 | S58-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L577 |
| #1937 | S58-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L578 |
| #1937 | S58-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L579 |
| #1937 | S58-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L715 |
| #1937 | S58-006 | already_fixed | exact_ledger | [2026-11-06-unmapped-medium-review.md](2026-11-06-unmapped-medium-review.md) L26 |
| #1938 | S59-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L580 |
| #1938 | S59-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L581 |
| #1938 | S59-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L582 |
| #1938 | S59-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L583 |
| #1938 | S59-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L584 |
| #1938 | S59-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L585 |
| #1938 | S59-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L586 |
| #1938 | S59-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L587 |
| #1938 | S59-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L716 |
| #1939 | S96-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L458; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L482 |
| #1939 | S96-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L459; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L483 |
| #1939 | S96-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L460; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L484 |
| #1939 | S96-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L461; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L485 |
| #1939 | S96-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L462; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L197; [2026-11-06-memory-source-nature-review.md](2026-11-06-memory-source-nature-review.md) L9 |
| #1940 | S96b-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L463; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L486 |
| #1940 | S96b-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L464; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L487 |
| #1940 | S96b-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L465 |
| #1940 | S96b-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L466 |
| #1941 | S97b-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L467; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L488 |
| #1941 | S97b-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L468; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L39 |
| #1941 | S97b-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L469; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L40 |
| #1941 | S97b-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L470; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L489 |
| #1941 | S97b-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L471; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L490 |
| #1941 | S97b-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L472; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L491 |
| #1941 | S97b-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L473; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L41 |
| #1942 | S4g-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L474; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L241; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L7 |
| #1942 | S4g-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L475; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L242; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L8 |
| #1942 | S4g-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L476; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L243; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L9 |
| #1942 | S4g-004 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L477; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L244; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L10 |
| #1942 | S4g-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L478; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L245; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L11 |
| #1942 | S4g-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L479; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L246; [2026-11-06-project-knowledge-review.md](2026-11-06-project-knowledge-review.md) L12 |
| #1942 | S4g-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L480; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L96 |
| #1942 | S4g-022 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L97 |
| #1942 | S4g-023 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L98 |
| #1942 | S4h-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L481; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L42 |
| #1942 | S4h-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L482; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L99 |
| #1942 | S4h-008 | fixed | exact_ledger | [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L100 |
| #1942 | S4h-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L483; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L43 |
| #1942 | S4d-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L484; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L101 |
| #1943 | S99-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L485; [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md) L8 |
| #1943 | S99-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L486; [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md) L9 |
| #1943 | S99-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L487; [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md) L10 |
| #1943 | S99-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L488; [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md) L7 |
| #1943 | S99-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L489; [2026-11-06-provider-provenance-rag-review.md](2026-11-06-provider-provenance-rag-review.md) L11 |
| #1944 | S100-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L490 |
| #1944 | S100-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L491 |
| #1944 | S100-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L492; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L492 |
| #1944 | S100-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L493; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L493 |
| #1944 | S100-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L494 |
| #1944 | S100-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L495 |
| #1945 | S101-001 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L496 |
| #1945 | S101-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L497 |
| #1945 | S101-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L498 |
| #1945 | S101-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L499 |
| #1945 | S101-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L500 |
| #1945 | S101-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L501 |
| #1945 | S101-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L502 |
| #1945 | S101-008 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L503 |
| #1946 | S102-001 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L494 |
| #1946 | S102-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L495 |
| #1946 | S102-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L496 |
| #1947 | S103-002 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L497 |
| #1947 | S103-003 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L498 |
| #1947 | S103-004 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L499 |
| #1947 | S103-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L500 |
| #1947 | S103-006 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L501 |
| #1948 | S104-002 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L504 |
| #1948 | S104-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L505 |
| #1948 | S104-004 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L506 |
| #1948 | S104-005 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L507 |
| #1948 | S104-006 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L508 |
| #1948 | S104-007 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L509 |
| #1948 | S104-008 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L510; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L504 |
| #1948 | S104-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L511; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L502 |
| #1948 | S104-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L512; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L503 |
| #1949 | S105-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L513; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L505 |
| #1949 | S105-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L514; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L506 |
| #1949 | S105-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L515; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L507 |
| #1949 | S105-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L516; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L508 |
| #1949 | S105-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L517; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L44 |
| #1949 | S105-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L518; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L509 |
| #1949 | S105-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L519; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L510 |
| #1950 | S1-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L520; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L27 |
| #1950 | S1-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L521; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L28 |
| #1950 | S1-008 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L522; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L29 |
| #1950 | S1-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L523; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L30 |
| #1950 | S1-010 | not_a_bug | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L524; [2026-11-05-root-issues.md](2026-11-05-root-issues.md) L31 |
| #1950 | S2-003 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L525 |
| #1950 | S2-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L526; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L206 |
| #1950 | S2-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L527; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L7 |
| #1950 | S2b-L01 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L528; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L8 |
| #1950 | S2b-L02 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L529; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L9 |
| #1950 | S2b-L03 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L530; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L10 |
| #1950 | S2b-L04 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L531; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L11 |
| #1950 | S2b-L05 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L532; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L12 |
| #1950 | S2b-L06 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L533; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L13 |
| #1950 | S2b-L07 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L534; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L14 |
| #1950 | S2b-L08 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L535; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L15 |
| #1950 | S2b-L09 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L536; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L16 |
| #1950 | S2b-L10 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L537; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L17 |
| #1950 | S2b-L11 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L538; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L18 |
| #1950 | S2b-L12 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L539; [2026-11-06-pi-bridge-low-review.md](2026-11-06-pi-bridge-low-review.md) L19 |
| #1950 | S3-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L540; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L224 |
| #1950 | S3-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L541; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L225 |
| #1950 | S3-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L542; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L226 |
| #1950 | S3-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L543; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L207 |
| #1950 | S3-012 | not_a_bug | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L544; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L227 |
| #1950 | S3-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L545; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L208 |
| #1950 | S3-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L546; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L228 |
| #1950 | S3-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L547; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L229 |
| #1950 | S3-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L548; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L56 |
| #1950 | S5-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L549; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L57 |
| #1950 | S5-005 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L550; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L234 |
| #1950 | S5-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L551; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L58 |
| #1950 | S5-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L552; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L59 |
| #1950 | S5-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L553; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L60 |
| #1950 | S5-009 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L554; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L61 |
| #1950 | S5-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L555; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L62 |
| #1950 | S6-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L556; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L63 |
| #1950 | S6-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L557; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L64 |
| #1950 | S6-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L558; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L65 |
| #1950 | S6-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L559; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L66 |
| #1950 | S6-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L560; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L67 |
| #1950 | S6-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L561; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L68 |
| #1950 | S6-011 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L562; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L235 |
| #1950 | S6-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L563; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L236 |
| #1950 | S6-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L564; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L72 |
| #1950 | S6-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L565; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L69 |
| #1950 | S6-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L566; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L70 |
| #1950 | S6-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L567; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L71 |
| #1950 | S6-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L568; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L32 |
| #1950 | S6-018 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L569; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L33 |
| #1950 | S6-019 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L570; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L230 |
| #1950 | S7-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L571; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L138 |
| #1950 | S7-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L572; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L139 |
| #1950 | S7-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L573; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L140 |
| #1950 | S7-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L574; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L141 |
| #1950 | S7-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L575; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L142 |
| #1950 | S7-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L576; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L143 |
| #1950 | S7-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L577; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L144 |
| #1950 | S7-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L578; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L231 |
| #1950 | S7-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L579; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L145 |
| #1950 | S7-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L580; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L146 |
| #1951 | S8-L01 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L581; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L152 |
| #1951 | S8-L02 | not_a_bug | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L582; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L153 |
| #1951 | S8-L03 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L583; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L154 |
| #1951 | S8-L04 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L584; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L155 |
| #1951 | S8-L05 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L585; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L156 |
| #1951 | S8-L06 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L586; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L157 |
| #1951 | S8-L07 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L587; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L158 |
| #1951 | S8-L08 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L588; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L159 |
| #1951 | S8-L09 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L589; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L160 |
| #1951 | S9-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L590; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L7 |
| #1951 | S9-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L591; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L8 |
| #1951 | S9-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L592; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L9 |
| #1951 | S9-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L593; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L10 |
| #1951 | S9-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L594; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L11 |
| #1951 | S9-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L595; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L12 |
| #1951 | S9-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L596; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L13 |
| #1951 | S9-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L597; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L14 |
| #1951 | S9-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L598; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L15 |
| #1951 | S9-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L599; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L16 |
| #1951 | S10-L01 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L600; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L17 |
| #1951 | S10-L02 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L601; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L18 |
| #1951 | S10-L03 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L602; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L19 |
| #1951 | S10-L04 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L603; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L20 |
| #1951 | S10-L05 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L604; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L21 |
| #1951 | S10-L06 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L605; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L22 |
| #1951 | S10-L07 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L606; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L23 |
| #1951 | S10-L08 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L607; [2026-11-06-evaluation-low-review.md](2026-11-06-evaluation-low-review.md) L24 |
| #1951 | S10-L09 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L608; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L201 |
| #1951 | S10-L10 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L609; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L202 |
| #1951 | S10-L11 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L610; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L38 |
| #1951 | S11-005 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L611; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L7 |
| #1951 | S11-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L612; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L8 |
| #1951 | S11-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L613; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L9 |
| #1951 | S11-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L614; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L10 |
| #1951 | S11-009 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L615; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L11 |
| #1951 | S11-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L616; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L12 |
| #1951 | S11-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L617; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L13 |
| #1951 | S11-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L618; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L14 |
| #1951 | S11-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L619; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L15 |
| #1951 | S11-014 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L620; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L16 |
| #1951 | S12-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L621; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L17 |
| #1951 | S12-010 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L622; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L18 |
| #1951 | S12-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L623; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L19 |
| #1951 | S12-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L624; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L20 |
| #1951 | S12-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L625; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L21 |
| #1951 | S12-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L626; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L22 |
| #1951 | S12-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L627; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L23 |
| #1951 | S12-016 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L628; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L24 |
| #1951 | S12-017 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L629; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L25 |
| #1951 | S13-L1 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L630; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L26 |
| #1951 | S13-L2 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L631; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L27 |
| #1951 | S13-L3 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L632; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L28 |
| #1951 | S13-L4 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L633; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L29 |
| #1951 | S13-L5 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L634; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L30 |
| #1951 | S13-L6 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L635; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L188 |
| #1951 | S13-L7 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L636; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L189 |
| #1951 | S13-L8 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L637; [2026-11-06-extension-collab-review.md](2026-11-06-extension-collab-review.md) L31 |
| #1951 | S49-L1 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L638; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L179 |
| #1951 | S49-L2 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L639; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L180 |
| #1951 | S49-L3 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L640; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L181; [2026-11-06-memory-source-nature-review.md](2026-11-06-memory-source-nature-review.md) L8 |
| #1951 | S49-L4 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L641; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L182 |
| #1951 | S49-L5 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L642; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L183 |
| #1951 | S49-L6 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L643; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L184 |
| #1951 | S49-L7 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L644; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L185 |
| #1951 | S49-L8 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L645; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L186 |
| #1951 | S49-L9 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L646; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L187 |
| #1951 | S50-006 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L647; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L177 |
| #1951 | S50-007 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L648; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L7 |
| #1951 | S50-008 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L649; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L8 |
| #1951 | S50-009 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L650; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L9 |
| #1951 | S50-010 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L651; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L10 |
| #1951 | S50-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L652; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L11 |
| #1951 | S50-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L653; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L12 |
| #1951 | S50-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L654; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L13 |
| #1951 | S50-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L655; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L14 |
| #1951 | S50-015 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L656; [2026-11-06-context-policy-resume-review.md](2026-11-06-context-policy-resume-review.md) L15 |
| #1952 | S51-005 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L11 |
| #1952 | S51-006 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L12 |
| #1952 | S51-007 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L13 |
| #1952 | S51-008 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L14 |
| #1952 | S51-009 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L15 |
| #1952 | S51-010 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L16 |
| #1952 | S51-011 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L17 |
| #1952 | S51-012 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L18 |
| #1952 | S51-013 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L19 |
| #1952 | S51-014 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L20 |
| #1952 | S51-015 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L21 |
| #1952 | S52-004 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L22 |
| #1952 | S52-005 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L794 |
| #1952 | S52-006 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L24 |
| #1952 | S52-007 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L25 |
| #1952 | S52-008 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L26 |
| #1952 | S52-009 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L27 |
| #1952 | S52-010 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L28 |
| #1952 | S52-011 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L29 |
| #1952 | S52-012 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L30 |
| #1952 | S53-L01 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L31 |
| #1952 | S53-L02 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L32 |
| #1952 | S53-L03 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L33 |
| #1952 | S53-L04 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L34 |
| #1952 | S53-L05 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L35 |
| #1952 | S53-L06 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L36 |
| #1952 | S53-L07 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L37 |
| #1952 | S53-L08 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L38 |
| #1952 | S53-L09 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L39 |
| #1952 | S53-L10 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L588 |
| #1952 | S53-L11 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L589 |
| #1952 | S54-L01 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L40 |
| #1952 | S54-L02 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L41 |
| #1952 | S54-L03 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L42 |
| #1952 | S54-L04 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L43 |
| #1952 | S54-L05 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L590 |
| #1952 | S54-L06 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L591 |
| #1952 | S54-L07 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L592 |
| #1952 | S54-L08 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L593 |
| #1952 | S54-L09 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L594 |
| #1952 | S54-L10 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L595 |
| #1952 | S54-L11 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L596 |
| #1952 | S54-L12 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L597 |
| #1952 | S55-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L598 |
| #1952 | S55-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L599 |
| #1952 | S55-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L600 |
| #1952 | S55-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L601 |
| #1952 | S55-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L602 |
| #1952 | S55-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L603 |
| #1952 | S55-021 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L604 |
| #1952 | S55-022 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L605 |
| #1952 | S55-023 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L606 |
| #1952 | S55-024 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L607 |
| #1952 | S55-025 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L608 |
| #1952 | S55-026 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L609 |
| #1952 | S55-027 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L610 |
| #1952 | S55-028 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L611 |
| #1952 | S55-029 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L612 |
| #1952 | S56-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L613 |
| #1952 | S56-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L614 |
| #1952 | S56-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L615 |
| #1952 | S56-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L616 |
| #1952 | S56-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L617 |
| #1952 | S56-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L618 |
| #1952 | S56-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L619 |
| #1952 | S56-021 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L620 |
| #1952 | S56-022 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L621 |
| #1952 | S56-023 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L622 |
| #1952 | S56-024 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L623 |
| #1952 | S56-025 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L624 |
| #1952 | S56-026 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L625 |
| #1952 | S56-027 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L626 |
| #1952 | S57-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L627 |
| #1952 | S57-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L628 |
| #1952 | S57-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L629 |
| #1952 | S57-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L630 |
| #1952 | S57-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L631 |
| #1952 | S57-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L632 |
| #1952 | S57-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L633 |
| #1952 | S57-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L634 |
| #1952 | S57-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L635 |
| #1952 | S57-019 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L636 |
| #1952 | S57-020 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L637 |
| #1952 | S57-021 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L638 |
| #1952 | S57-022 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L639 |
| #1952 | S58-L01 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L640 |
| #1952 | S58-L02 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L641 |
| #1952 | S58-L03 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L642 |
| #1952 | S58-L04 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L37 |
| #1952 | S58-L05 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L643 |
| #1952 | S58-L06 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L644 |
| #1952 | S58-L07 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L645 |
| #1952 | S58-L08 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L646 |
| #1952 | S58-L09 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L647 |
| #1952 | S58-L10 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L648 |
| #1952 | S58-L11 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L649 |
| #1953 | S59-013 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L511 |
| #1953 | S59-014 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L512 |
| #1953 | S59-015 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L513 |
| #1953 | S59-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L514 |
| #1953 | S59-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L515 |
| #1953 | S59-018 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L516 |
| #1953 | S96-007 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L517 |
| #1953 | S96-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L518 |
| #1953 | S96-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L519 |
| #1953 | S96-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L520 |
| #1953 | S96-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L521 |
| #1953 | S96-012 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L50 |
| #1953 | S96b-005 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L44 |
| #1953 | S96b-006 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L45 |
| #1953 | S96b-007 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L46 |
| #1953 | S96b-008 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L522 |
| #1953 | S96b-009 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L523 |
| #1953 | S96b-010 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L524 |
| #1953 | S96b-011 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L525 |
| #1953 | S96b-012 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L526 |
| #1953 | S96b-013 | already_fixed | exact_ledger | [2026-11-06-data-low-closure-review.md](2026-11-06-data-low-closure-review.md) L51 |
| #1953 | S97-001 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L47 |
| #1953 | S97-002 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L48 |
| #1953 | S97b-L01 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L527 |
| #1953 | S97b-L02 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L528 |
| #1953 | S97b-L03 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L529 |
| #1953 | S97b-L04 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L530 |
| #1953 | S97b-L05 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L531 |
| #1953 | S97b-L06 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L532 |
| #1953 | S97b-L07 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L49 |
| #1953 | S97b-L08 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L50 |
| #1953 | S97b-L09 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L51 |
| #1953 | S97b-L10 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L52 |
| #1953 | S98-001 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L53 |
| #1953 | S98-002 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L54 |
| #1953 | S98-003 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L55 |
| #1953 | S98-004 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L56 |
| #1953 | S98-005 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L57 |
| #1953 | S98-006 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L58 |
| #1953 | S98-007 | not_a_bug | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L59 |
| #1953 | S98-008 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L60 |
| #1953 | S99-007 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L61 |
| #1953 | S99-008 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L62 |
| #1953 | S99-009 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L63 |
| #1953 | S99-010 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L64 |
| #1953 | S99-011 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L65 |
| #1953 | S99-012 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L66 |
| #1953 | S99-013 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L67 |
| #1953 | S99-014 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L68 |
| #1953 | S99-015 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L69 |
| #1953 | S99-016 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L70 |
| #1953 | S99-017 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L71 |
| #1953 | S100-007 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L72 |
| #1953 | S100-008 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L73 |
| #1953 | S100-009 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L74 |
| #1953 | S100-010 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L75 |
| #1953 | S100-011 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L76 |
| #1953 | S100-012 | fixed | exact_ledger | [2026-11-06-root-runtime-findings.md](2026-11-06-root-runtime-findings.md) L77 |
| #1953 | S100-013 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L38 |
| #1953 | S100-014 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L39 |
| #1953 | S100-015 | not_a_bug | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L40 |
| #1953 | S100-016 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L533 |
| #1953 | S100-017 | fixed | exact_ledger | [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L534 |
| #1953 | S100-018 | fixed | exact_ledger | [2026-11-06-root-unmapped-findings.md](2026-11-06-root-unmapped-findings.md) L41 |
| #1954 | S101-009 | not_a_bug | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L657 |
| #1954 | S101-010 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L658 |
| #1954 | S101-011 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L659 |
| #1954 | S101-012 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L660 |
| #1954 | S101-013 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L661 |
| #1954 | S101-014 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L662 |
| #1954 | S101-015 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L663 |
| #1954 | S101-016 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L664 |
| #1954 | S101-017 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L665 |
| #1954 | S101-018 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L666 |
| #1954 | S101-019 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L667 |
| #1954 | S101-020 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L668; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L45 |
| #1954 | S101-021 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L669; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L46 |
| #1954 | S101-022 | not_a_bug | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L670; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L47 |
| #1954 | S101-023 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L671 |
| #1954 | S101-024 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L672 |
| #1954 | S102-L1 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L673; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L674 |
| #1954 | S102-L2 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L674; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L675 |
| #1954 | S102-L3 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L675; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L676 |
| #1954 | S102-L4 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L676; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L677 |
| #1954 | S102-L5 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L677; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L161 |
| #1954 | S102-L6 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L678; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L162 |
| #1954 | S102-L7 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L679; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L163 |
| #1954 | S103-L02 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L680; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L678 |
| #1954 | S103-L03 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L681; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L679 |
| #1954 | S103-L05 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L682; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L680 |
| #1954 | S103-L07 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L683; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L681 |
| #1954 | S103-L08 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L684; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L682 |
| #1954 | S103-L09 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L685; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L683 |
| #1954 | S103-L10 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L686; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L684 |
| #1954 | S103-L11 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L687; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L685 |
| #1954 | S103-L12 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L688; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L686 |
| #1954 | S103-L13 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L689; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L687 |
| #1954 | S103-L14 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L690; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L688 |
| #1954 | S104-011 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L691; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L689 |
| #1954 | S104-012 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L692; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L690 |
| #1954 | S104-013 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L693; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L691 |
| #1954 | S104-014 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L694; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L692 |
| #1954 | S104-015 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L695; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L164 |
| #1954 | S104-016 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L696; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L165 |
| #1954 | S104-017 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L697 |
| #1954 | S104-018 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L698 |
| #1954 | S104-019 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L699 |
| #1954 | S104-020 | already_fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L700; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L166 |
| #1954 | S104-021 | fixed | core_ledger_with_shared_validation | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L701 |
| #1954 | S105-L1 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L702; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L693 |
| #1954 | S105-L2 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L703; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L694 |
| #1954 | S105-L3 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L704; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L695 |
| #1954 | S105-L4 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L705; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L696 |
| #1954 | S105-L5 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L706; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L48 |
| #1954 | S105-L6 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L707; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L167 |
| #1954 | S105-L7 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L708; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L168 |
| #1954 | S105-L8 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L709; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L169 |
| #1954 | S106-001 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L710; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L136 |
| #1954 | S106-002 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L711; [2026-11-06-core-continuation-review.md](2026-11-06-core-continuation-review.md) L137 |
| #1954 | S106-003 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L712; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L697 |
| #1954 | S106-004 | fixed | exact_ledger | [2026-11-05-core-issues.md](2026-11-05-core-issues.md) L713; [2026-11-05-data-issues.md](2026-11-05-data-issues.md) L698 |
| #1966 | S14-001 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L7 |
| #1966 | S14-002 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L8 |
| #1966 | S14-003 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L9 |
| #1966 | S14-004 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L10 |
| #1966 | S14-005 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L11 |
| #1967 | S14-006 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L12 |
| #1967 | S14-007 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L13 |
| #1967 | S14-008 | already_fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L14 |
| #1967 | S14-009 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L15 |
| #1967 | S14-010 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L16 |
| #1967 | S14-011 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L17 |
| #1967 | S14-012 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L18 |
| #1967 | S14-013 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L19 |
| #1967 | S14-014 | fixed | exact_ledger | [2026-11-05-cartography-abi-review.md](2026-11-05-cartography-abi-review.md) L20 |

## 发布边界

本审计不宣称已在真实Docker daemon/Kubernetes集群完成部署，也不把访客UI截图说成已登录管理员/导出交互的浏览器验证。UI生产构建、自动化权限测试与实际截图的范围分别保存在UI-postbuild-audit及各专项台账。最终合并还需根代理核验最新主分支冲突、源码冻结后的派生产物、全量测试与CI，之后才能按本表逐issue写入关闭说明。
