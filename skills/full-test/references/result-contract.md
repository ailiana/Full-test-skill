# 执行结果格式 v1

UI、API 和手工记录采用同一格式。JSON 编码 UTF-8，时间必须是带时区的 ISO 8601，结束时间不能早于开始时间。

```json
{
  "schema_version": 1,
  "run_id": "run-20261113T100000Z-api",
  "target": "http://127.0.0.1:8765",
  "started_at": "2026-11-13T10:00:00+00:00",
  "finished_at": "2026-11-13T10:00:01+00:00",
  "cases": [{
    "id": "API-001",
    "title": "健康接口返回可用状态",
    "type": "api",
    "priority": "P0",
    "status": "PASS",
    "duration_ms": 15.2,
    "expected": "HTTP 200; $.ok 为 true",
    "actual": "HTTP 200; 2 个断言通过",
    "evidence": [],
    "notes": []
  }],
  "notes": []
}
```

| 字段 | 约束与含义 |
|---|---|
| `schema_version` | 整数 `1` |
| `run_id` | 非空运行标识，合并不同执行器时保持可追踪 |
| `target` | 目标描述或脱敏 URL，可为空字符串 |
| `started_at` / `finished_at` | 带时区 ISO 8601 |
| `cases` | 数组；空数组不算通过 |
| `id` / `title` | 非空字符串；合并后的 ID 唯一，UI/API 可加前缀 |
| `type` | `ui` / `api` / `manual` |
| `priority` | `P0` / `P1` / `P2` / `P3` |
| `status` | `PASS` / `FAIL` / `BLOCKED` / `SKIPPED` / `NOT_RUN` |
| `duration_ms` | 有限非负数字，未执行填 `0` |
| `expected` / `actual` | 字符串，未知或未执行可为空；不要包含凭据 |
| `notes` | 字符串数组，说明阻塞/跳过/重试/预期失败等上下文 |
| `flaky` | 可选布尔值，重试后通过应保留 `true` |
| `expected_failure` | 可选布尔值，Playwright 声明的预期失败；导入为 SKIPPED，不代表功能通过 |
| `attempts` | 可选逐次执行记录，Playwright 导入器保留它 |
| `evidence` | 可选数组，元素含 `kind`、`path`、可选 `note` |

`kind` 支持 `screenshot`、`trace`、`log`、`file`、`http`。`path` 使用 `/` 分隔，相对于结果 JSON 的目录，文件必须在该目录内；不允许 `../`、绝对路径、URI 或越界符号链接。报告器只复制存在且路径有效的文件。缺文件会提示，不生成伪造链接。

此格式不自动证明提供者真的执行了测试。AI 必须依据工具实际观测填写；外部导入的 PASS 应说明数据来源。标准 API Runner 的状态/耗时/断言 JSON 是证据，因此不强制每个 PASS 附截图或正文文件。

## 汇总语义

- 通过率 = `PASS / (PASS + FAIL)`；分母为零时显示无已执行结果，不能显示 100%。
- 总体 `FAIL`：任一用例失败。
- 总体 `INCOMPLETE`：没有失败，但存在阻塞、跳过、未执行、空集合或 flaky。
- Playwright xfail（预期失败）单列统计，不纳入功能通过率；即使声明符合预期，也不能据此给出发布就绪结论。
- 总体 `PASS`：非空集合全部通过且稳定。
- P0/P1 失败阻止发布；其余失败也不能宣称全部通过。范围、未覆盖项、阻塞和重试需与统计同时呈现。
- JUnit 将 BLOCKED/SKIPPED/NOT_RUN 映射为 `<skipped>` 并保留原因，防止 CI 错读成成功。
