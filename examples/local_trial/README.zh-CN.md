# tenant-rag v0.5.0：双租户真实本地模型试用

这是可复制的完整检索案例，不是 LLM 聊天机器人。使用正式发布的 wheel、
免费的本地多语言 MiniLM embedding 和 10 份中英合成 Markdown 文档。
没有私人数据、付费 API、公开服务器或自动发布。

## 在 Linux CPU 环境运行

本次实际测试为 Python 3.12.14。SQLite 需要支持 FTS5 trigram。
初次安装需要联网下载 Python 依赖和约 500 MB 模型；正式检索只在本地运行。
模型权重和 wheel 不提交到仓库；下载脚本会从官方固定版本下载并校验。
仓库 v0.5.1 加入本案例，案例仍固定使用已正式发布的 v0.5.0 wheel。
新版本 v0.5.1 wheel 由发布流程另外安装验收，核心检索行为不变。

```sh
cd examples/local_trial
python3 -m venv .venv
. .venv/bin/activate
unset PYTHONPATH
python -m pip install 'torch==2.13.0' --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements-local.txt
python scripts/download_assets.py --model-dir model
python -m pip install --no-deps wheel/tenant_rag-0.5.0-py3-none-any.whl
python -m unittest discover -s tests -v
python run_trial.py --model-dir model --output my-run.json
```

已有结果文件不会被覆盖，重复运行请换文件名。脚本校验 wheel 的安装字节、
模型、冻结的数据和标签，随机生成只在内存保存的租户 key，并使用临时数据库。
结束后不会留下可访问数据库的 key。首次下载后无需任何服务凭证。

## 你会看到什么

- Aurora 和 Cedar 各 5 份文档，共用 5 个父文档 ID，政策数值故意不同。
- 批量导入形成 50 个块；原样重导入无需再次计算 embedding。
- 同一句免邮问题，Aurora 的原文是 60 credits，Cedar 是 90 credits。
- 返回原文片段、来源、父文档 ID 和半开区间字符偏移；每条引用都逐字核对。
- 双租户检索、列表、删除及重开数据库检查。32 次换 key 的检索未出现跨租户来源。
- 16 个冻结问题的完整结果：14 个有答案，2 个没有答案。

## 如实看结果

默认 RRF 的相关父文档命中为 14/14，但完整标注句只返回了 12/14。
两个退货期限问题找对了退款文档，却返回了运费相关片段，未取回 21 天或
35 天的答案。这说明“来源文档对了”仍不等于“答案证据对了”。

固定权重 0.2 的实验性 score 融合在本例完整标注句为 14/14，不能据此推广。
v0.5.0 的更广泛公共数据评测中，score 融合在两个数据集都降低了召回率。
因此保留 RRF 默认，不以这个小样本宣称普遍改进。

两个无答案问题在两种模式下都返回了片段。检索不是拒答判定，分数不是置信度。
本例仅展示可核对的片段，不会把最近的结果包装成已经回答了问题。

50 个块最多 72 tokens，问题最多 20 tokens，均低于模型 128-token 上限。
字符切块与 token 限制不是同一回事；脚本单独检查并在超限时停止，不静默截断。

## 交付与边界

先看 `run_trial.py`、`data/queries.json` 和 `evidence/real-model-final.json`。
完整复测、测试范围和独立审查记录在 `evidence/VALIDATION.md`。
详细模型和 wheel 版本、校验、指标定义及局限见英文 README。

这是小型合成演示，不是独立质量评测，也不是已有真实外部试用者的证据。
双语原文有中文释义，不能作为纯跨语言能力证明。单机逻辑隔离不是物理隔离、
加密或安全审计；有数据库文件权限的人能读到全部租户。没有验证真实生产负载、
HTTP/浏览器操作、文档权限管理或生成答案质量。

推理进程使用离线加载选项和 Python socket 审计阻断，本次没有网络尝试。
这不等同于操作系统防火墙。模型和依赖下载是单独、明确的联网安装步骤。
