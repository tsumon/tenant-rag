<div align="center">

# tenant-rag

**每个用户的数据，只出现在自己的检索结果里。**

面向个人 AI 应用的轻量多租户混合检索组件：一个 SQLite 数据库，零第三方运行时依赖。

[![CI](https://github.com/tsumon/tenant-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/tsumon/tenant-rag/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
[![MIT](https://img.shields.io/badge/license-MIT-yellow)](LICENSE)

[English](README.md) · [发布检查清单](docs/RELEASING.md)

</div>

> **v0.4**：新增模型身份校验、原子重建索引、文本/Markdown 增量导入。旧数据库需显式确认原模型身份或重建索引；升级前请阅读 [升级与导入指南](docs/V0.4.md)。

## 能做什么

- **混合检索**：余弦向量检索 + FTS5 trigram 关键词检索，通过 RRF 融合排名。
- **租户逻辑隔离**：API key 解析成租户身份，索引、查询、删除、配额和用量均按租户过滤；不同租户可以使用相同文档 ID。
- **轻量存储**：SQLite + Python 标准库，适合个人级语料，不需要单独部署向量数据库。
- **可直接使用**：Python API、Bearer 鉴权 HTTP API、本地浏览器管理界面。
- **显式长文档导入**：确定性文本/Markdown 切块、来源偏移、父文档原子更新、批量增量向量复用。
- **模型安全**：保存模型身份和维度，旧数据明确确认来源或全库原子重建，防止同维度模型混用。
- **离线回归门禁**：隔离、迁移回滚、并发配额、错误请求与检索管线测试。

这是**检索组件**，不是完整知识库业务产品，也不是托管 SaaS。它不负责回答生成、PDF 解析、用户账号、租户内文档权限或分布式存储。订阅管家示例用模板回答演示检索，不是大模型 Agent 平台。

**v0.5.0** 新增显式启用的实验性 score 融合及可复现本地评测，默认仍为 RRF。参见[升级说明](docs/V0.5.md)。

## 安装 v0.5.0

从 [GitHub Release](https://github.com/tsumon/tenant-rag/releases/tag/v0.5.0) 下载 wheel 和 `SHA256SUMS`，核对 wheel 的 SHA-256 后执行 `python -m pip install tenant_rag-0.5.0-py3-none-any.whl`。本版本通过 GitHub 分发，未发布到 PyPI；仍需满足下述环境要求并注意使用边界。

## 从源码安装

要求 **Python 3.10+**，SQLite 支持 **FTS5 + trigram tokenizer**（SQLite 3.34+）。运行时无第三方依赖；构建包使用 setuptools。

```bash
git clone https://github.com/tsumon/tenant-rag.git
cd tenant-rag
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
python -c "import sqlite3; sqlite3.connect(':memory:').execute(\"CREATE VIRTUAL TABLE probe USING fts5(text, tokenize='trigram')\")"
```

上面是源码安装流程，不表示已经发布到 PyPI。

## 五分钟跑通

生成随机租户 key，初始化服务端将使用的**同一个数据库**。注册只需执行一次；重复注册会报错。保留此终端，后续 curl 使用同一个环境变量。

```bash
export TENANT_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
python - <<'PY'
import os
from tenantrag import TenantRAG
with TenantRAG('tenantrag.db') as rag:
    rag.register('demo-user', os.environ['TENANT_KEY'], quota_docs=5000)
    rag.index(os.environ['TENANT_KEY'], '腾讯视频 VIP 年费 198 元', doc_id='bill-1')
    print(rag.search(os.environ['TENANT_KEY'], '腾讯视频多少钱'))
PY

# 在另一终端进入同一目录、激活虚拟环境，再启动服务：
tenant-rag --db tenantrag.db --port 8000
```

回到保存 TENANT_KEY 的终端：

```bash
curl -sS http://127.0.0.1:8000/v1/search \
  -H "Authorization: Bearer $TENANT_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"query":"腾讯视频多少钱","k":5}'
```

默认 FakeEmbedder 是确定性**词法哈希向量**，不具备真正的语义能力。上述流程不下载模型、不调用外部 API，也不产生模型费用。

### 本地管理界面

```bash
tenant-rag-admin --api http://127.0.0.1:8000 --port 8002
```

打开 http://127.0.0.1:8002，粘贴生成的租户 key，即可上传文本/Markdown、浏览分页文档、检索和删除。key 只保存在页面内存，不写浏览器存储。创建租户仍通过可信 Python 操作完成。两个服务默认仅监听 loopback，管理界面**不能作为公网管理后台**。安装 wheel 后也包含界面 HTML。

## 接口约定

Python 用法见 [英文完整示例](README.md#python-api)。`TenantRAG` 支持 `with` 自动关闭；同租户相同 doc_id 会覆盖文本、metadata 和向量。全量遍历要显式翻页。

长文档使用 `rag.ingest(key, text, doc_id="handbook", source="handbook.md", format="markdown")`，批量使用 `rag.ingest_batch(key, documents)`。固定父文档 ID 的相同内容重试跳过写入和模型请求；内容哈希包含文本、metadata、source 和切块选项。列表/配额按父文档计数，搜索返回片段及 `metadata._chunk` 中的父 ID、来源、起止偏移。偏移按 Unicode 字符计数，结束位置不包含在内，不是字节或 token。替换/删除父文档会一并移除旧片段；不允许直接修改片段。原有 `index` 仍索引整篇，不自动切块。详见[参数、更新与配额语义](docs/V0.4.md#text-and-markdown-ingestion)。

除 health 外，HTTP 都需 `Authorization: Bearer <tenant-key>`。

| 方法 | 路径 | 参数 / 返回 |
|---|---|---|
| POST | `/v1/index` | text，doc_id / metadata 可选；返回文档 ID |
| POST | `/v1/ingest` | text，doc_id / metadata / source / format / max_chars / overlap 可选；返回父文档及切块摘要 |
| POST | `/v1/ingest/batch` | documents 和共用 max_chars / overlap；返回原子批量摘要 |
| POST | `/v1/search` | query、k；返回排名及 matched 通道 |
| GET | `/v1/docs?limit=100&offset=0` | 分页文档 |
| DELETE | `/v1/docs/<URL编码后的ID>` | deleted 布尔值 |
| GET | `/v1/usage` | 配额、今日请求量、限流策略 |
| GET | `/v1/health` | 无需鉴权，仅检查进程存活，不验证模型服务 |

错误码：参数 `400`，鉴权 `401`，配额 `403`，模型身份或并发导入冲突 `409`，限流 `429`（含 Retry-After），意外后端/模型错误 `500`。JSON 请求体上限 10 MiB；搜索 k 限制在 1–100，HTTP 列表 limit 限制在 1–1000。默认每租户每进程 60 次/分钟。合法租户请求会计量，包括失败、限流以及 usage 请求本身；按服务器本地日期分天。Python 直接调用不参与 HTTP 限流与请求计量。

## 接入真实 embedding

只有设置 `--embed-base-url` 或 EMBED_BASE_URL，HTTP 服务才使用真实模型：

```bash
export EMBED_BASE_URL='http://127.0.0.1:8080/v1'
export EMBED_MODEL='your-served-embedding-model'
export EMBED_REVISION='immutable-model-revision'  # 与提供方部署版本保持一致。
export EMBED_API_KEY='local-demo'  # 仅用于忽略鉴权的本地端点的占位 Bearer 值。
# 如果端点要求鉴权，请安全设置实际提供方密钥。
tenant-rag --db semantic.db
```

Python 可传入 `OpenAIEmbedder(api_key=..., base_url=..., model=...)`。使用提供方明确支持的 embeddings 接口；远程服务应使用 HTTPS。文档和查询会发往该端点，可能产生费用，需自行确认隐私处理政策。不要提交密钥。

**一个数据库固定使用同一 embedding 模型。** 数据库保存 endpoint/model/revision 身份和观测维度，同维度换模型也会拒绝混用；初始化不请求模型服务。优先使用不可变 revision，默认 `unspecified` 无法发现同名模型在服务端偷偷换权重。旧库没有身份信息时，只有能核实全部旧向量来源的可信管理员才可调用 `adopt_legacy_embeddings()`；否则执行 `reindex(target_embedder)`。重建影响全部租户，仅通过可信 Python 接口提供，不向 HTTP 暴露。失败或提交前中断保持旧索引，重试从头计算而非断点续传，可能重复产生模型费用。

**升级前停止全部 v0.3 进程并验证备份**：旧代码和直接 Store 写入绕过新增身份安全机制，不可混用版本。参阅[迁移操作步骤和边界](docs/V0.4.md#model-identity-and-upgrading-an-existing-database)。

## 评测与测试

```bash
python -m unittest discover -v
python eval/run_eval.py --k 3 --min-recall 0.8 --min-mrr 0.7
python eval/run_eval.py --json --output eval-report.json
python demo/run_demo.py
```

新增独立版本的运维合成挑战集、来源/标签说明、校验和校验以及不联网的 `--dry-run` 请求计划，详见[评测指南](eval/README.md)。

也可使用可选的 **CPU 本地模型评测入口**：安装 `.[local-eval]` 后，传入 `--embedder local --model-path "$MODEL_DIR"`，读取事先准备好的可信 safetensors 模型，不自动下载或调用收费接口。现已对固定版本多语言模型实际评测，记录模型指纹、依赖、逐查询排名和退步。[融合研究](docs/FUSION.md)区分旧语料调参与独立合成评测，不把开发集分数当作生产质量证明。

默认仍为 RRF。Python 和 HTTP 搜索可显式选择 `fusion="score"`、`lexical_weight=0.2`，保留余弦差距并限制关键词加分。此权重仅为开发集选出的候选，不是通用最优值；金额、币种等约束仍需专门处理。评测融合差异时使用 `--allow-fusion-change`，工具会核对模型、完整候选指纹及其他控制变量相同。

默认只对 **pipeline 回归集**设置门禁；另有中英双语 semantic 挑战集，默认输出诊断指标。数据是人工合成用例，不是生产质量基准。报告区分模型、数据集及 hybrid/vector/fts 通道，计算 recall@k 与截断排名上的倒数排名指标。

明确选择真实端点后再执行：

```bash
python eval/run_eval.py --embedder openai --allow-provider-requests \
  --base-url http://127.0.0.1:8080/v1 --model your-served-embedding-model \
  --gate-suite semantic --output semantic-report.json
```

设置 EMBED_API_KEY；仅对忽略鉴权的本地端点使用非敏感占位值。该命令会发送评测文本到指定模型服务，可能付费。**离线测试不能证明真实模型质量**，也不保证 hybrid 在所有数据集都胜过 vector。请用已获授权、有代表性的数据实测后制定门槛。

## 架构和边界

```text
API key → tenant ID
查询 ┬→ embedding → 租户内余弦召回 ┐
     └→ 安全 trigram 查询 → 租户内 FTS ├→ RRF → 租户内文档
                                       ┘
```

FTS 表含未索引的 tenant_id 列，查询显式过滤。查询文字先分词，不直接拼接 FTS 语法；少于三个字符的中文词无法由 trigram 命中，依赖向量通道。向量检索扫描租户语料，适合个人规模，容量与延迟需自行测量。RRF 分数不是概率或置信度。

- **不是物理隔离**：多个租户共用数据库，通过逻辑过滤隔离；不提供静态加密，需保护主机、数据库文件和备份。key 保存为 SHA-256 哈希，泄露 Bearer key 相当于泄露租户权限，目前无内置轮换/撤销流程。
- **不是公网加固服务**：标准库线程 HTTP 适合本地或可信环境。公网部署前需 TLS、网关、请求/并发限制、监控与安全审查；进程内限流不跨 worker 共享。
- **备份**：使用 SQLite 在线 backup API 或 VACUUM INTO，并验证恢复。WAL 模式不能只复制运行中的 .db；checkpoint 后若写入恢复，复制仍不安全。文件级复制须先停止所有写入者。
- **维护**：`rag.store.purge_usage(keep_days=90)` 清理旧计量行。旧全局 doc_id schema 在打开时事务迁移，操作前保存已验证的备份。
- **尚未证明**：真实模型效果/可用性、生产负载、完整浏览器交互、分布式部署与对抗性安全审计。正式发布前按[清单](docs/RELEASING.md)检查。

## License

[MIT](LICENSE)。

公开数据补测中，score 在英语 SciFact 和中文 T2 子集的召回均低于 RRF，保持实验性可选模式，不替换默认策略。长文截断、数字条件与成本见[完整评测说明](eval/public-study/README.md)。
