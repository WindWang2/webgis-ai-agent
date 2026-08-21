# 所有工具执行统一经 ToolDispatchService，入口差异用参数表达

Date: 2026-08-21

## Status

Accepted

## Context

工具执行存在多个入口：chat 循环、plan_mode、workflow 引擎、admin 端点。审计家族（#589/#594/#694-6）反复呈现同一故障模式：**绕过 ToolDispatchService 直调 `tool_registry.dispatch` 的入口，会漏掉服务层的横切守卫**——#589 错误形态折叠、Redis-unavailable ref 哨兵检测、去重拦截。ADR-0014 曾豁免 plan_mode/admin 端点，但 #694 发现 workflow 重跑同样绕过（烧 provider 配额、可锁定哨兵 ref），已改为经 service 执行。

每个绕过案例的修复都是把同一组守卫重新接一遍；守卫散落在各入口意味着每加一个守卫就要审计所有入口。

## Decision

1. **统一原则**：一切程序化的工具执行都经 `ToolDispatchService.dispatch`；入口差异（去重范围、错误形态、是否携带 session 上下文）用**显式参数**表达，不用"换一条更短的路径"表达。
2. workflow 的去重语义已参数化先例：每步骤独立 `executed_tools` 集合——不同步骤同参是**有意的重复执行**（重跑语义），不是 chat 循环的重复拦截对象。
3. admin 只读端点维持 ADR-0014 豁免（无配额/哨兵面）；新增执行入口时默认走 service，豁免需要 ADR 级理由。

## Consequences

- ToolDispatchService 成为守卫的唯一落点：新守卫只写一次，全部入口自动获得。
- service 的判别式结果（ok/repeated/error）成为所有入口的共同契约；入口把 error 映射为自己的失败语义（chat 忽略并继续、workflow 上抛回滚）。
