# `tsumon/tenant-rag` 企业级生产架构说明（v0.6.0 Enterprise Dual-Mode）

本文档说明 `tenant-rag` 从个人级单库检索组件向**企业真实生产级多租户 RAG 平台**演进的架构设计、隔离保证、接口规范与部署方式。

## 一、双模兼容原则

1. **零依赖核心 SDK 保持 100% 兼容**：
   - `pyproject.toml` 的基础 `dependencies = []` 保持不变。
   - 原有 `tenantrag.TenantRAG`、`tenantrag.Store`、`tenant-rag`（HTTP API）与 `tenant-rag-admin` 行为不变，完整通过既有单元测试、评测门禁与发布包校验。
2. **可选企业生产服务层（`tenantrag.enterprise`）**：
   - 新增 `pip install "tenant-rag[enterprise]"` 可选依赖组（`fastapi`、`uvicorn`、`python-multipart`、`pypdf`、`pydantic`）及 `tenant-rag-enterprise` 启动入口。
   - 企业核心模块除 HTTP 网关（FastAPI）与 `.pdf` 解析（`pypdf`）外，均采用 Python 标准库实现。

## 二、核心架构模块

```text
+-------------------------------------------------------------------------+
|               React 19 企业双视角工作台 (Platform Admin + Tenant)          |
+------------------------------------+------------------------------------+
                                     | 同源 API / SSE / Prometheus
+------------------------------------v------------------------------------+
| FastAPI 生产网关 (tenantrag/enterprise/app.py)                           |
|  - X-Request-ID 追踪 / 动态租户令牌桶限流 / X-RateLimit-* 响应头           |
|  - 兼容历史 /v1/* 路由 | 新增租户 /api/v1/* | 新增平台超管 /api/v1/admin/*  |
+------------------+-----------------------------------+------------------+
                   |                                   |
   +---------------v---------------+   +---------------v---------------+
   | Control Plane (control_plane) |   | TenantPool 物理分库池          |
   |  - 租户生命周期 / 启停熔断       |   |  - tenants/<tid>/index.db     |
   |  - Scoped API Key 轮换/过期/吊销|   |  - WAL 多连接只读池并发检索    |
   |  - SHA-256 防篡改审计哈希链     |   |  - generation 版本 VectorCache|
   |  - 异步任务与热备元数据          |   |  - 在线 backup() 热备与还原    |
   +-------------------------------+   +---------------+---------------+
                                                       |
   +---------------------------------------------------v---------------+
   | 企业级检索与生成管道                                                 |
   |  1. 多格式文档解析 (MD/TXT/HTML/CSV/JSON/DOCX/PDF) + AsyncJobQueue  |
   |  2. Pre-Retrieval Metadata DSL 过滤 + _acl 文档级权限校验           |
   |  3. 向量余弦 + FTS5 trigram -> RRF / Score 融合                     |
   |  4. 二阶段 Rerank (NoOp / LexicalCrossReranker / APIReranker)       |
   |  5. 父子上下文窗口展开 (context_window) + 阶段耗时 Waterfall         |
   |  6. Evidence Abstention 拒答门控 -> Grounded SSE 引用溯源问答        |
   +-------------------------------------------------------------------+
```

## 三、关键生产边界

1. **物理隔离与并发读**：
   - 每个租户拥有独立 `tenants/<tenant_id>/index.db`（WAL 模式），彻底消除跨租户 FTS5 BM25 词频统计污染。
   - `tenant_id` 严格校验 `^[A-Za-z0-9._-]{1,64}$` 防止目录穿越。
   - 写操作在租户级 `TenantRAG` 内原子执行并推进 `VectorCache` 版本；检索查询通过只读 `mode=ro` 连接池与内存向量缓存并发执行。
2. **IAM 与密钥轮换**：
   - 平台超管通过 `X-Admin-Token`（配置 `TENANT_RAG_ADMIN_TOKEN` 或 `--admin-token`）管理租户与密钥；未显式配置且未开启 `--demo-mode` 时，超管接口默认 fail-closed 拒绝访问。
   - 租户 Key 格式为 `tr_live_...`，数据库仅保存 SHA-256 哈希与前缀。支持 `rotate_key(grace_seconds)` 在宽限期内新旧 Key 并行有效，实现零停机密钥轮换。
3. **审计链校验**：
   - 每次关键读写与管理操作均以链式哈希 `entry_hash = sha256(prev_hash | payload)` 落库，调用 `GET /api/v1/admin/audit/verify` 可全库验证是否发生过篡改。
4. **模型身份与热备**：
   - 每个租户独立 SQLite 库依然继承 `v0.4`/`v0.5` 的模型身份绑定与原子重建约束。
   - `create_backup` 基于 `sqlite3.Connection.backup()` 在线热备并校验 `PRAGMA integrity_check` 与 SHA-256；`restore_backup` 会先校验证据再原子切换库文件。

## 四、快速启动

```bash
# 1. 开发演示模式（自动生成双租户演示数据与离线合成语料）
tenant-rag-enterprise --data-dir ./enterprise_data --port 8080 --demo-mode --seed-demo

# 2. 生产安全模式（绑定真实 Embedding 与可选 LLM 端点）
export TENANT_RAG_ADMIN_TOKEN="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export EMBED_BASE_URL="https://api.openai.com/v1"
export EMBED_API_KEY="..."
export EMBED_MODEL="text-embedding-3-small"
export EMBED_REVISION="2024-02"
tenant-rag-enterprise --host 0.0.0.0 --port 8080 --data-dir /data/tenantrag
```
