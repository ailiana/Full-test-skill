# API 测试与执行

## 从接口资料生成用例

读取用户提供的 OpenAPI/Swagger、Postman collection、curl 或现有路由和测试。记录来源和版本；没有契约时把探索得出的字段当作假设。为高风险操作设计正常、字段缺失/类型/边界、无效/过期身份、跨权限身份、资源不存在、重复提交与状态转换用例。分页、幂等、并发仅在接口定义和任务涉及它们时加入。

每个断言需有依据：HTTP 状态、响应语义/业务状态、关键字段、鉴权行为；只有 `200` 无法证明业务成功。错误响应也要断言合理状态和错误结构。测试生成和执行是两个独立阶段。不要把 mock 的成功报告为真实服务验证成功。

## 选择执行器

- 已有 pytest、Playwright APIRequestContext、Newman、REST Assured 等时，扩展原有用例与 fixtures。
- 明确少量请求，且 Python 可访问目标时，可使用 `scripts/run_api.py`。
- 当前环境无法到达目标时保留可执行计划、说明阻塞；不切换到未经授权的生产服务。

内置 Runner 不调用 AI、不解析 OpenAPI、不生成断言。AI 根据契约写 JSON 计划，Runner 只负责确定性执行和记录结果。它不做完整 JSON Schema 验证、动态变量提取、自动 token 刷新、性能/负载或多身份状态编排；需要时使用项目框架。

## 计划格式

例子见 [api-plan.example.json](../assets/api-plan.example.json)。路径相对本次运行的目标，必须以单个 `/` 开始。不允许绝对 URL、跨域跳转、URL 凭据；不会跟随 HTTP 重定向。

```json
{
  "schema_version": 1,
  "base_url": "http://127.0.0.1:8765",
  "timeout_seconds": 10,
  "headers": {"Accept": "application/json"},
  "cases": [{
    "id": "API-001",
    "title": "健康接口返回可用状态",
    "priority": "P0",
    "method": "GET",
    "path": "/health",
    "expect": {"status": 200, "json": {"$.ok": true}}
  }]
}
```

| 位置 | 支持字段 |
|---|---|
| 计划 | `schema_version`（可省略，值为 1）、`base_url`、`headers`、`timeout_seconds`、`cases` |
| 用例 | `id`、`title`、`priority`（P0–P3，默认 P1）、`method`、`path`、`headers`、`body`、`expect` |
| 预期 | `status`（必需，整数或整数列表）、`json`（JSONPath → 预期值）、`body_contains`（字符串）、`max_duration_ms`（正数） |

`json` 路径仅支持 `$`、`.field`、`[0]` 等简单形式；值做精确相等比较。`body` 使用 JSON 序列化。用例编号必须唯一；不识别的字段会被拒绝，防止拼错断言却误报成功。全部计划先验证再发送请求。

## 命令与身份

```text
python "<skill-dir>/scripts/run_api.py" --plan api-plan.json --output api-results.json --dry-run
python "<skill-dir>/scripts/run_api.py" --plan api-plan.json --output api-results.json
```

`--dry-run` 只验证和列出 `NOT_RUN` 结果，不发网络请求，也不算通过。可用 `--base-url` 覆盖计划目标。

认证头使用环境变量引用，例如 `"Authorization": "Bearer ${API_TOKEN}"`。由用户通过当前宿主的密钥/环境设置提供值；不要在聊天、shell 历史或报告粘贴 token。缺少变量会在执行前阻塞整份计划。

默认只发 GET/HEAD/OPTIONS。POST/PUT/PATCH/DELETE 需要 `--allow-write`；它只是防误执行开关，不代替用户对目标与数据变更范围的授权。用户已经授权测试环境创建测试数据时可继续，不要求重复确认；新增删除、生产写入或负载范围时先确认具体动作。

Runner 按顺序执行、默认不重试、保持 TLS 校验、限制单响应体为 2 MiB。响应过大/不可达/超时等记为 `BLOCKED`；实际响应与预期不符记为 `FAIL`。HTTP 4xx/5xx 可以是负向用例的正确预期。输出只保留 HTTP 状态、耗时、断言计数和失败标签，不保存请求/响应正文或认证头。

已有结果文件会在请求前被拒绝；输出采用硬链接独占发布以保留原证据，需使用支持硬链接的本地文件系统。不支持时返回错误，不降级覆盖。

## 结果与清理

原始执行 JSON 本身是内置 API Runner 的最小证据，`evidence` 可以为空。需要正文证据时只保存脱敏后的最小字段，或使用已有项目报告器。记录创建的合成资源和清理方法；清理必须保持在已授权范围。失败后继续调用报告脚本，准确列出未执行与环境阻塞。
