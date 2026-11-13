---
name: full-test
description: "Generate test cases from requirements, run UI smoke and API tests, and produce evidence-backed reports. 用于需求生成测试用例、UI冒烟、接口测试、回归和测试报告；适配 ChatGPT 与 Codex，可按请求只完成其中一项。"
---

# Full Test · 全流程测试

把需求、测试用例、实际执行和报告连起来。使用用户的语言，默认沿用项目已有测试框架；只做用户请求的阶段。

## 开始时确定三件事

1. **范围**：目标项目/网址/API、环境、关键业务和本次要求（设计、冒烟、接口、回归、报告或全流程）。优先从对话、仓库、PRD、OpenAPI 和现有测试获取；仅询问会改变测试结论的缺失信息。未知预期标为待确认，不替产品编造规则。
2. **工具**：检查当前宿主实际暴露的文件、终端、浏览器、网络工具。先读宿主相应工具说明，使用已有会话或执行器；不假定某个 MCP 名称、浏览器、操作系统或外部 Skill 一定存在。
3. **执行条件**：区分本地工具能访问的目标与云端沙箱里的 localhost。无执行工具时照常产出用例/脚本；用户要求执行而环境阻塞的用例标 `BLOCKED`。仅设计的用例标 `NOT_RUN`。

已有的目标、权限、凭据和偏好在会话中继续有效。完成已授权的常规检查，不为每一步重复索要确认。测试请求不自动包含修复、发布、删除数据、压测或向他人发送报告；遇到这些范围扩展时，先完成可独立进行的部分，再说明具体待授权动作。

## 按任务读取

| 用户目标 | 读取与行动 |
|---|---|
| PRD/用户故事 → 用例、测试计划 | [test-design.md](references/test-design.md)：稳定编号、边界/异常、风险优先级和需求追踪 |
| UI 冒烟/浏览器回归 | [ui-testing.md](references/ui-testing.md)：关键流程实操、可见断言、截图和日志证据 |
| 接口/鉴权/异常测试 | [api-testing.md](references/api-testing.md)：从契约生成请求与断言，选择已有框架或内置小型 Runner |
| 结果导入、汇总、报告 | [reporting.md](references/reporting.md)；写 JSON 前看 [result-contract.md](references/result-contract.md) |

需要哪个阶段就读哪个参考，不要一次加载全部文件。独立 UI/API 检查可以并行；共享测试数据、认证状态和有状态流程按顺序执行。

## 全流程请求的工作顺序

1. 从需求/契约抽取可验证行为和 P0/P1 关键路径，生成适量用例；已有用例沿用编号并补齐缺口。
2. 明确每例的前置条件、操作、预期和证据。最小冒烟先验证基本可用性，再覆盖本次变更及高风险异常；不因冒烟通过就声称全量回归通过。
3. 在项目输出位置创建新的运行目录（例如 `test-results/run-<UTC时间>-<短随机码>/`），保存用例与结果，保留历史。不要在已安装 Skill 目录里写运行结果。
4. 使用实际工具执行，保留命令退出码、HTTP 观测或浏览器交互证据。隔离数据和身份；凭据用环境变量/宿主密钥工具传入，不写入用例、仓库或报告。真实接口验证和 mock 验证分别标注。
5. 失败时保留原结果；在范围内定位并重跑相应用例。重试通过保留历史并标 flaky。用户只要求测试时，输出复现与修复建议。
6. 汇总结果与未覆盖项。`PASS` 必须有实际断言；环境失败为 `BLOCKED`，未执行为 `NOT_RUN`，主动跳过为 `SKIPPED`。不要把生成了测试、工具退出码为零或看起来正常当作通过。

## 自带助手脚本

将 `<skill-dir>` 替换为本文件所在目录，路径加引号。先运行相应脚本的 `--help`。Python 命令按宿主选择 `python`、`python3` 或 `py -3`；脚本仅使用标准库（Python 3.10+）。

```text
python "<skill-dir>/scripts/run_api.py" --plan "<run-dir>/api-plan.json" --output "<run-dir>/api-results.json"
python "<skill-dir>/scripts/import_playwright.py" --input "<run-dir>/playwright.json" --output "<run-dir>/ui-results.json" --target "<目标URL>"
python "<skill-dir>/scripts/report.py" --input "<run-dir>/api-results.json" --input "<run-dir>/ui-results.json" --out-dir "<run-dir>/report"
```

返回码：`0` 全部通过且稳定；`1` 存在失败；`2` 输入错误或结果不完整。**有失败/阻塞时仍生成报告**；不要用 `&&` 让报告被前一步的非零退出码跳过。没有产生结果文件时，先把执行阻塞记录为规范结果，再汇总。

报告生成 HTML、Markdown、JUnit XML 和 JSON 摘要。内置 API Runner 适合小型显式 HTTP 计划；复杂鉴权、动态提取、OpenAPI 全 schema、负载或契约测试使用项目工具，不能声称本脚本覆盖它们。UI 执行依赖当前宿主浏览器工具或项目安装的 Playwright。

## 给用户的交付

先给结论：通过、失败或不完整，以及范围和通过/失败/阻塞/未执行数量；再给严重缺陷、证据和报告路径。说明关键未覆盖项及其原因。仅设计时交付用例和待确认项，不给发布通过结论。报告按用户原请求留在本地；上传或发送需具备相应授权。
