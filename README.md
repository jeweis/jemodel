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

## 快速开始：一分钟部署

> 无需下载代码、无需构建、无需配置任何环境变量。镜像已内置 Web 控制台，直接拉取 Docker Hub 镜像即可运行。

### 方式一：docker run（最简）

```bash
docker run -d --name jemodel \
  --restart always \
  -p 8010:8000 \
  -v jemodel-data:/data \
  jeweis/jemodel
```

验证是否启动成功：

```bash
curl -fsS http://localhost:8010/health
# {"status":"ok"}
```

### 方式二：docker compose

```bash
# 拉取镜像并启动（默认开启 --restart always）
docker compose up -d

# 验证健康
curl -fsS http://localhost:8010/health
```

### 首次登录

打开 <http://localhost:8010>，页面会**自动展示一个首次生成的引导管理员 API key**：

- 点击「复制 key」保存
- 首次展示后此 key 不再出现；同时它也打印在容器启动日志里（`docker logs jemodel`）
- 之后用这个 key 登录控制台（或作为客户端认证凭据）

> 想自定义引导 key？设置环境变量 `JEMODEL_BOOTSTRAP_ADMIN_API_KEY` 即可，此时不再自动生成。
> 想自定义密钥？设置 `JEMODEL_SECRET_KEY`（默认值为 `jemodel`）。

登录后，在控制台完成：

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

### 停止与数据持久化

```bash
# 停止容器（数据保留在 jemodel-data 卷中）
docker stop jemodel

# 数据（SQLite）保存在 Docker volume 中，重启容器数据不丢失
docker start jemodel
```

## Docker 镜像

镜像发布在 [Docker Hub](https://hub.docker.com/r/jeweis/jemodel)：

```bash
docker pull jeweis/jemodel
```

每次 push `main` 或发布 release 都会自动构建并推送 `latest` 镜像。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `JEMODEL_SECRET_KEY` | `jemodel` | 会话与密钥哈希的 secret，生产环境建议自定义 |
| `JEMODEL_BOOTSTRAP_ADMIN_API_KEY` | 自动生成 | 设置后使用该 key 引导，否则首次启动自动生成并展示 |
| `JEMODEL_BOOTSTRAP_ADMIN_EMAIL` | `admin@local.jemodel` | 引导管理员邮箱 |
| `JEMODEL_PORT` | `8010` | 宿主机映射端口（compose 方式） |
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
# 本地构建镜像（覆盖拉取行为，compose 默认从 Docker Hub 拉取）
docker build -t jeweis/jemodel .
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
