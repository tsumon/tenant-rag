# tenant-rag：多租户混合检索（RAG + grep）

[English](README.md) | 中文

给独立开发者个人 AI 应用用的**轻量检索底座**，不是另一个 Elasticsearch。

一句话定位：你的 App 需要"搜用户自己的私域数据"时，接上它——向量（语义）+ FTS5（关键词/grep）混合检索，租户之间物理级隔离，单文件 SQLite，零运维。

## 为什么不是重复造轮子

ES / OpenSearch / Qdrant / pgvector 都是轮子，但它们是**重型 infra**：要部署、要运维，多租户隔离得自己搭（RLS、payload filter，漏一处就串数据）。本项目的切口是"**小而多租户**"：

- 个人级语料（每租户几千文档），暴力向量检索足够，ANN 是杀鸡用牛刀
- 开箱即用的租户隔离：API key 即租户，跨租户泄漏有测试锁死
- 检索评测门禁：改 tokenizer / 换 embedding 模型，先看数字再上线

切口守不住做大了就撞轮子——非目标见文末。

## 快速开始

```python
from tenantrag import TenantRAG
from tenantrag.embed import FakeEmbedder  # 离线；线上换 OpenAIEmbedder

rag = TenantRAG("app.db", embedder=FakeEmbedder())
rag.register("user-123", api_key="sk-xxx", quota_docs=5000)

rag.index("sk-xxx", "Netflix 每月 198 元自动续费", doc_id="bill-1")
hits = rag.search("sk-xxx", "Netflix 多少钱")
# [{'doc_id': 'bill-1', 'text': ..., 'score': ..., 'matched': ['vector', 'fts']}]
```

线上 embedding（OpenAI 兼容接口，DeepSeek/SiliconFlow/本地 vLLM 都行）：

```python
from tenantrag.embed import OpenAIEmbedder
emb = OpenAIEmbedder(api_key="...", base_url="https://api.siliconflow.cn/v1",
                     model="BAAI/bge-m3")
```

## 租户管理后台（`admin/`）

纯标准库 Web UI，反向代理到 M2 HTTP API，只消费公开接口：

```bash
# 1. 起 M2 API
PYTHONPATH=. python3 -m tenantrag.server --db tenantrag.db --port 8000
# 2. 起管理后台
python3 admin/admin_server.py --api http://127.0.0.1:8000 --port 8002
# 3. 浏览器打开 http://127.0.0.1:8002/ ，粘贴租户 API key 登录
```

功能：租户概览（文档数/配额、今日请求、限流）、上传文档（文本框或 .txt/.md 文件，服务端自动向量化）、文档列表分页 + 删除、混合检索（结果带向量/关键词通道徽标和 RRF 分数）。

说明：key 只存浏览器内存；后台本身无鉴权，只监听 127.0.0.1，是本地运维工具，不要暴露到公网。租户创建目前走 Python API（`TenantRAG.register`）。

## 架构

```
search(query)
  ├─ query → embedding → vector_search (cosine, 余弦<=0 过滤)
  ├─ query → build_fts_query → fts_search (FTS5 trigram + JOIN 租户过滤)
  └─ RRF 融合 → fetch_docs → 返回（含 matched: vector/fts）
```

三处最容易错的地方，都在代码里标出来了：

1. **FTS 租户过滤**：FTS5 表不存 tenant_id，全文检索必须 `JOIN docs` 过滤，否则一个 MATCH 就串租户。`test_isolation.py` 锁死。
2. **CJK 分词**：unicode61 会把"腾讯视频"切成整词、单字查不到；改用 trigram，query 侧切滑动 trigram。2 字以下短词 trigram 查不了，走向量通道——这正是混合检索的意义。
3. **用户输入进 MATCH**：引号/AND/括号会触发 FTS5 语法错误，`build_fts_query` 只抽 token 并转义。

## 商业级 checklist（MVP 已落地）

- [x] API key 鉴权（key = tenant，最简单的隔离模型）
- [x] 租户配额（超限 `QuotaError`，更新不占配额，删除释放配额）
- [x] 跨租户泄漏测试（7 项，含"相同文本两租户各存一份"这种阴险 case）
- [x] 检索评测集 + CI 门禁（`eval/run_eval.py --min-recall 0.8`）
- [x] 用量查询（`usage()`：docs / quota）
- [x] M2：HTTP 服务（`tenantrag/server.py`，标准库，零依赖）
  - `POST /v1/index` / `POST /v1/search` / `DELETE /v1/docs/<id>` / `GET /v1/docs?limit=&offset=` / `GET /v1/usage` / `GET /v1/health`
  - 鉴权中间件：`Authorization: Bearer` → 401；错误映射：配额/隔离 → 403，限流 → 429 + Retry-After
  - 注册强制 api_key ≥ 8 字符（弱 key 直接拒，key 即租户身份）；socket 超时 10s 防 slowloris；body 上限 10MB
  - 限流：每租户令牌桶（默认 60/min，可配）；用量计量表按天计请求
  - 测试：HTTP 级隔离、配额 403、限流 429（0 回补确定性）、计量、list 分页、slowloris 超时
- [x] M3：dogfood——订阅管家 Agent（`demo/`，离线零成本）
  - `SubscriptionAgent`：意图识别 → 数据获取 → 作答；lookup 走混合检索，聚合走 `list_docs` 全量（RAG 应用的真实分工）
  - 双租户演示：同一问题"腾讯视频多少钱"，两人得到不同答案（隔离的价值）
  - 每个回答带 `sources: [doc_id]`，可追溯
  - 作答默认模板，`composer` 可换 LLM（OpenAI 兼容接口）
  - M3 逼出的 schema 修正：doc_id 从全局主键改为 `(tenant_id, doc_id)` 复合主键（两人都叫 "bill-txvideo" 很自然）；FTS 表加 `tenant_id` 列直接过滤，不再 JOIN；老库自动迁移（`test_migration.py`）

## 评测

```bash
python3 eval/run_eval.py --k 3
# | hybrid | 0.929 | 1.000 |
# | vector | 0.929 | 1.000 |
# | fts    | 0.571 | 0.571 |
```

诚实声明：默认 FakeEmbedder 是**词法级**向量（哈希词袋），所以 vector 和 hybrid 持平——这个数字只验证管线正确性。换上真实 embedding 后，预期 hybrid > vector（改写 query 场景）> fts。评测集的价值是回归门禁，不是绝对分数。

## 面试 talking points

1. **为什么混合**：纯向量对订单号/金额/人名翻车，纯关键词对同义改写翻车；RRF 免训练、鲁棒，是生产最稳的 baseline。
2. **隔离的实现与取舍**：MVP 单库逻辑隔离（每条 SQL 强制 `tenant_id`）+ 测试锁死；升级路径是每租户独立 SQLite 文件（物理隔离），代价是文件数和备份复杂度。
3. **CJK 的坑**：unicode61 vs trigram 的实测对比（"腾讯视频"整词问题），query 侧滑动 trigram。
4. **评测驱动**：先有 golden 集再调参，门禁进 CI——和"护栏层"哲学一脉相承。
5. **dogfood 驱动设计**：订阅管家 demo 跑起来第一天就发现 doc_id 全局主键是错的——真实多租户里"两人都叫 bill-txvideo"太自然了。改成复合主键 + 老库自动迁移，这就是"先有用户再有 schema"的价值。

## 找 bug 专项（2026-09-23，七轮共 26 项）

第一轮 8 项：假余弦（未归一化的点积）、index 缺输入校验、HTTP 未捕获异常致连接重置、全局 HTTP 锁包住 embedding 网络调用、限流桶无限增长、负数 k 切片语义、`register` 重复租户裸抛 SQLite 错误、无价格订阅作答空洞。顺带给 Store 加了 RLock + `busy_timeout`，配额检查与写入原子化。

第二轮 5 项：重复 **api_key** 注册裸抛 IntegrityError（tenant_id 修了，key 漏了）、`api_key=None` 抛 AttributeError 而非 AuthError、空字符串进 FTS5 MATCH 语法错误、非整数 k 切片 TypeError、`list_docs` 无 offset 导致聚合在大租户下静默漏算（demo agent 改成分页取全量）。另补了 HTTP 缺的 `GET /v1/docs` 路由。

第三轮 2 项（并发与 SQLite）：单连接多线程并发读写报 `InterfaceError: bad parameter or other API misuse`——`check_same_thread=False` 只关掉线程亲和检查，并不让并发 `execute()` 变安全；连带 `resolve_tenant` 偶发返回 None（合法 key 误判 401）。修法：Store 所有 `_con` 访问（读和写）全部走 RLock。另：双实例同 DB 时 RLock 跨实例不协调，配额超卖（quota=30 写出 31）；`service.index` 加 `BEGIN IMMEDIATE` 让"检查→写入"跨连接原子。WAL 快照语义经压测确认无半提交可见。

第四轮 0 项（FTS 大小写，嫌疑排除）：实测 FTS5 trigram 默认 ASCII 大小写不敏感，`Tencent Video` 用 `tencent` 查 fts 通道正常命中；FakeEmbedder 也 lower，两侧一致。加回归测试锁死该行为，防将来换 tokenizer 静默退化。

第五轮 2 项（安全与 DoS）：无 socket 超时 → slowloris 半截连接永久占线程，加 `Handler.timeout = 10`；`register` 弱 key（如 `"1"`）直接放行——产品决策：api_key 是唯一的鉴权因子，<8 字符注册时抛 ValueError（8 位随机 keyspace ~2e14，HTTP 爆破不可行）。另验证：深层嵌套 JSON 致 RecursionError 时进程不崩、返回 500 JSON（符合预期，加测试锁死）；10MB body 上限保留（embedding 上游本来也收不了 MB 级输入）。

第六轮 3 项（一致性与运维）：`upsert_doc`/`delete_doc` 多条 SQL 无 rollback——docs 写成功但 FTS 失败会留半提交开事务占写锁，补 try/except rollback；`delete` miss（0 行）同样开过隐式事务，补 commit 收尾否则一直占 RESERVED；`_migrate_v2` 半失败不可恢复（RENAME 后崩溃 → 数据孤儿在 docs_old，新表是空的，检测直接跳过），改包进显式事务、失败整体回滚、下次启动重试。另加 `purge_usage(keep_days=90)`：usage_meter 按 (tenant, day) 无限增长，建议定期清理。

第七轮 4 项（API 一致性与 demo）：Python `usage()` 缺 `requests_today`（HTTP 有），对齐补上；demo 里显式币种限定被 total/max 静默忽略（"用美元结算的一共多少钱"把 CNY 也算进去），改为限定生效、无结果时明确提示；`detect_currency` 不认识欧元，补 EUR；`_answer_lookup` 传给 composer 的文本不截断（换 LLM composer 会爆 token），加 500 字截断 + 省略号标记，sources 不受影响。意图优先级（total > max > list > lookup）经多意图 query 验证合理，不动。HTTP search k 上限 100 而 Python API 实际受候选池（vec_k+fts_k）约束——文档说明，不做硬对齐。

测试从 24 → 33 → 40 → 42 → 44 → 47 → 51 → 58，全绿；评测分数未动（hybrid 0.929/1.000）。`tests/test_robustness.py` 是回归资产。

## 运维注意事项

- **备份**：WAL 模式下 DB 由 `*.db` + `*.db-wal` + `*.db-shm` 三个文件组成，只拷 `*.db` 会丢最近提交。备份前先 `PRAGMA wal_checkpoint(TRUNCATE)`，或用 `VACUUM INTO 'backup.db'` / `sqlite3 db .dump`。
- **usage_meter 清理**：`store.purge_usage(keep_days=90)`，建议 cron 每月跑一次。
- **api_key 策略**：注册强制 ≥8 字符；key 即租户身份，泄漏=租户接管，请用随机字符串并走 HTTPS（或只绑 127.0.0.1）。
- **HTTP k 上限**：`/v1/search` 的 k 钳制到 100；Python API 的 k 实际受候选池（vec_k=50 + fts_k=50）约束，传再大也拿不到更多。

## 非目标（守住切口）

- 分布式、高并发：个人级场景不需要
- 大规模 ANN：单租户几千文档暴力检索足够，升级路径是 sqlite-vss
- learned reranker：RRF 够用，留接口
