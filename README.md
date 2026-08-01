<div align="center">

# jemodel

**个人 / 团队自托管的 LLM 模型网关**

只需配置一次 base URL 和 API key，jemodel 帮你管理真实 provider、上游模型、路由策略、负载均衡、故障转移与用量统计。

OpenAI 兼容 · Anthropic 兼容 · 自带管理控制台 · 单容器部署

</div>

---

## 为什么用 jemodel？

- **统一入口**：所有客户端（Claude Code、Codex、Cursor 或自定义脚本）只连一个地址，模型切换在后台完成，客户端零改动。
- **双协议兼容**：同时暴露 OpenAI-compatible（`/v1/chat/completions`）和 Anthropic-compatible（`/v1/messages`）端点。
- **路由与故障转移**：为虚拟模型配置多个上游目标，按优先级、权重、能力（streaming / tools / vision）自动选择和切换。
- **用量可见**：管理控制台提供 token 用量统计（含缓存命中与推理 token）、请求日志和健康状态。
- **模型接入自由**：任意 OpenAI / Anthropic 兼容端点（DeepSeek、Moonshot、本地 vLLM 等）即插即用；也支持通过 OAuth 接入 ChatGPT Codex。

## 快速开始（Docker）

> 需要 Docker 与 Docker Compose。镜像已内置 Web 控制台，无需额外构建前端。

```bash
# 1. 拉取或构建镜像
docker compose up -d

# 2. 验证健康
curl -fsS http://localhost:8010/health
```

启动前需要两个环境变量，用于初始化管理员：

```bash
export JEMODEL_SECRET_KEY=change-me-to-a-long-random-string
export JEMODEL_BOOTSTRAP_ADMIN_API_KEY=change-me-admin-api-key
docker compose up -d
```

打开 <http://localhost:8010>，用引导 API key 登录控制台，然后：

1. **添加 Provider** —— 填入上游 API 地址与密钥（OpenAI / Anthropic 兼容端点）。
2. **添加上游模型** —— 声明该 provider 暴露的真实模型名与能力。
3. **创建虚拟模型** —— 例如 `team-coder`，并绑定路由目标（可配优先级与权重）。
4. **创建 API Key** —— 给客户端一个带模型权限的 key。

### 客户端接入

| 客户端 | Base URL | 认证 |
|---|---|---|
| Claude Code | `http://localhost:8010` | `ANTHROPIC_AUTH_TOKEN=<jemodel-api-key>` |
| OpenAI SDK / Cursor | `http://localhost:8010/v1` | `Authorization: Bearer <jemodel-api-key>` |

`GET /v1/models` 会按请求头自动返回对应协议的模型列表。

### 其他部署方式

默认端口为 `8010`，如需更换：

```bash
JEMODEL_PORT=18100 docker compose up -d
```

停止容器但保留数据卷：

```bash
docker compose down
```

数据（SQLite）保存在 Docker volume `jemodel-data` 中。

## Docker 镜像

镜像发布在 [Docker Hub](https://hub.docker.com/r/jeweis/jemodel)：

```bash
docker pull jeweis/jemodel
```

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JEMODEL_SECRET_KEY` | — | 会话与密钥哈希的 secret，必须设置 |
| `JEMODEL_BOOTSTRAP_ADMIN_API_KEY` | — | 首次启动生成管理员 API key，必须设置 |
| `JEMODEL_BOOTSTRAP_ADMIN_EMAIL` | `admin@local.jemodel` | 引导管理员邮箱 |
| `JEMODEL_PORT` | `8010` | 宿主机映射端口 |
| `JEMODEL_EXPERIMENTAL_CODEX_OAUTH` | `true` | 是否启用 Codex OAuth provider |
| `JEMODEL_MOCK_UPSTREAMS` | `false` | 是否使用本地 mock 上游（冒烟测试） |

## 功能特性

- OpenAI / Anthropic 双协议数据面
- 虚拟模型路由：优先级、权重、能力过滤、冷却与故障转移
- 管理控制台：Provider / 模型 / 路由 / 用户 / API Key / 用量 / 日志
- 用量统计：token 总量、缓存命中、推理 token
- Codex OAuth 登录（device code flow）
- 中英双语界面
- 单容器部署，SQLite 持久化

## 从源码构建

```bash
git clone https://github.com/jeweis/jemodel.git
cd jemodel
docker compose build
docker compose up -d
```

## 文档

- Swagger API 文档：管理控制台侧边栏 → "API 文档"，或访问 `/docs`。
- 协议端点：`/v1/chat/completions`（OpenAI）、`/v1/messages`（Anthropic）、`/v1/models`（模型列表）。

## 开发与测试

```bash
# 后端测试
uv sync
JEMODEL_SECRET_KEY=test uv run pytest

# 代码规范
uv run ruff check app
uv run mypy app
```

## License

[MIT](LICENSE)
