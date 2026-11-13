# 方法参考与原创范围

本项目面向 ChatGPT 桌面客户端和 Codex 的通用 Agent Skills 格式。下面记录设计时比较过的方法，查询日期为 **2026-11-13**。

本项目的说明、模板和执行脚本均为原创实现。下列链接用于说明方法选择，不表示本项目复制、打包或再分发了这些仓库的 Skill、代码或资源；也不把上游项目的 Star 数当成本项目质量证明。

| 参考项目 / 文件 | 采用的设计思路 | 本项目的取舍 |
|---|---|---|
| [Anthropic webapp-testing](https://github.com/anthropics/skills/blob/main/skills/webapp-testing/SKILL.md) | 围绕真实页面、截图、日志与服务就绪状态验证网页 | 使用当前客户端实际提供的工具；不假定某个 MCP 或浏览器必然可用 |
| [gstack qa-only](https://github.com/garrytan/gstack/blob/main/qa-only/SKILL.md) | 报告需要复现步骤与可检查的证据，区分测试与修复 | 默认报告；只在用户请求修复时更改产品代码；保留失败与重试历史 |
| [ECC e2e-testing](https://github.com/affaan-m/ECC/blob/main/skills/e2e-testing/SKILL.md) | 可维护的 E2E、失败产物和多种报告格式 | 复用项目已有测试框架；用统一结果汇总 API、UI 和手工观察 |
| [LambdaTest api-to-testcase-generator](https://github.com/LambdaTest/agent-skills/blob/main/api-skill/api-to-testcase-generator/SKILL.md) | 从 OpenAPI、请求示例和契约形成正向、异常及边界测试 | 不把未知业务规则补成“预期”；内置轻量执行器支持明确断言，复杂契约使用项目工具 |
| [Softaworks qa-test-planner](https://github.com/softaworks/agent-toolkit/blob/main/skills/qa-test-planner/SKILL.md) | 需求追踪、风险优先级及关键路径冒烟集 | 小任务使用最小必要记录；设计完成不等于实际执行通过 |
| [QA Skills Allure Report Generator](https://github.com/PramodDutta/qaskills/blob/main/seed-skills/allure-report-generator/SKILL.md) | 报告关联断言、附件、运行信息和 CI 结果 | 内置报告不依赖 CDN 或 Allure 安装；项目已有 Allure 时保留其原生报告 |
| [Naodeng awesome-qa-skills](https://github.com/naodeng/awesome-qa-skills) | 中文测试术语及需求、用例、执行、报告之间的工作流组织 | 仅比较概念，不复制其内容或资源；本项目保留中文说明并使用通用客户端入口 |

`naodeng/awesome-qa-skills` 在上述查询日期声明 **PolyForm Noncommercial 1.0.0**。本项目未复制或再分发该仓库内容，不将其标成 MIT 来源，也不把本项目许可证解释为对上游内容的重新授权。

## 客户端规范

- [OpenAI：Build skills](https://learn.chatgpt.com/docs/build-skills) — `SKILL.md`、渐进式加载、显式与隐式调用、桌面客户端元数据及本地发现路径。
- [OpenAI：Skills](https://developers.openai.com/plugins/concepts/skills) — Skill 是工作流说明；工具和连接器提供实际能力。Skill 本身不能凭空获得浏览器、终端、网络或授权。

当前客户端工具、环境可达性和项目依赖决定哪些测试能实际执行。文档兼容并不代表所有客户端环境都能运行所有脚本；执行结果必须以本轮证据为准。
