# 报告与证据

## 从执行数据生成报告

默认使用项目已有报告器；需要把 UI、API、手工结果合并成离线报告时使用自带脚本。源结果按 [result-contract.md](result-contract.md) 编写。

```text
python "<skill-dir>/scripts/report.py" --input api-results.json --input ui-results.json --out-dir run-report
```

`--input` 可重复。不同文件里的用例 ID 也必须唯一；不要用重复输入覆盖先前失败。输出目录必须不存在或为空，保留每次运行。

- `report.html`：离线打开，无 CDN 或外部脚本；统计、用例、缺陷线索和证据附件。
- `report.md`：适合聊天或仓库审阅的文本报告。
- `junit.xml`：CI 可读结果，未执行/阻塞映射为 skipped。
- `summary.json`：机器可读汇总、状态数量和门禁结论。

报告器返回码 `0` 为通过，`1` 有失败，`2` 结果不完整或输入/输出错误。即使测试失败也应运行报告；不要使用 `run-tests && generate-report`。生成失败时保留原始结果并报告阻塞。

## Playwright 导入

将 JSON reporter 文件与截图/trace 放在同一本次运行目录内。配置示例位于 [playwright.config.cjs](../assets/playwright.config.cjs)，测试模板位于 [ui-smoke.spec.cjs](../assets/ui-smoke.spec.cjs)。模板选择器仅针对自带演示页面，使用前替换为目标的真实预期。

```text
python "<skill-dir>/scripts/import_playwright.py" --input run/playwright.json --output run/ui-results.json --target https://staging.example.test
python "<skill-dir>/scripts/report.py" --input run/ui-results.json --out-dir run/report
```

导入器按项目/测试生成稳定 ID，保留重试记录；最终重试通过标 flaky，报告判不完整。声明的预期失败会写明语义，不能解释为功能通过。全局 setup 错误、被中断和无执行记录不会消失为零失败。无法验证的优先级默认 P1，AI 按计划校正。

Playwright 声明的预期失败导入为 `SKIPPED` 与 `expected_failure: true`，单独计数。一次尝试、每例累计及全局耗时均须在 31 天内；异常值受控拒绝，不产生虚假结果。

截图和 trace 必须仍在结果目录内才会成为可复制证据。报告会移入自身 `evidence/`；只复制 HTML 会丢失附件。

## 没有执行工具时

仅设计请求：交付用例和脚本，状态为 NOT_RUN。

请求执行但没有浏览器/网络/目标环境：继续独立检查，将受影响用例记为 BLOCKED，记录缺少的具体能力；仍可生成报告。外部提供的日志可以导入，但必须说明来源，不能说成 AI 亲自运行。

## 报告结论

先列范围、环境和结果，再给严重缺陷、复现步骤、预期/实际、证据、影响与未覆盖项。计划数量与实际执行数量分别表述。

文本凭据字段会做尽力脱敏，不是完整 DLP。原始截图、trace 和附件不会自动消除隐私或认证状态，分享前检查。报告默认保留本地，不自动上传或发送。
