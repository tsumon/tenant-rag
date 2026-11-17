"""Deterministic bilingual enterprise demo seed data for Aurora Cloud & Cedar MedTech."""
from __future__ import annotations

import json
from typing import Any


DEMO_TENANTS: list[dict[str, Any]] = [
    {
        "tenant_id": "aurora-cloud",
        "display_name": "星辰云科 · Aurora Cloud",
        "quota_docs": 500,
        "quota_chunks": 5000,
        "rate_limit_rpm": 240,
        "description": "企业云服务商知识库（含产品 SLA、退费政策、财务报销规范与机密高管薪酬制度）",
        "fixed_key": "tr_live_aurora_demo_key_2026_enterprise",
        "docs": [
            {
                "doc_id": "aurora-sla-2026",
                "source": "docs/aurora_enterprise_sla_2026.md",
                "format": "markdown",
                "metadata": {
                    "dept": "cloud",
                    "category": "sla",
                    "year": 2026,
                    "tier": "enterprise",
                    "sla_pct": 99.95,
                    "_acl": {"visibility": "public"},
                },
                "text": (
                    "# 星辰云科企业版 SLA 服务等级协议（2026 版）\n\n"
                    "## 1. 服务可用性承诺\n"
                    "星辰云科（Aurora Cloud）企业版计算与存储集群承诺月度服务可用性不低于 99.95%。"
                    "标准版（Standard）月度可用性承诺为 99.90%。单月不可用时间超过 21.6 分钟时，"
                    "企业版客户可在次月 10 个工作日内申请 SLA 违约金赔付。\n\n"
                    "## 2. 赔付标准与响应时效\n"
                    "若月度可用性在 99.0% 至 99.95% 之间，按当月实付账单的 10% 返还代金券；"
                    "若低于 99.0%，按当月实付账单的 30% 返还代金券。P1 级生产故障要求 15 分钟内工程师响应，"
                    "2 小时内给出临时恢复方案。\n\n"
                    "## 3. 多租户数据隔离保障\n"
                    "企业版独享物理隔离检索节点（Physical SQLite WAL Pool），支持独立热备快照与 SHA-256 校验。"
                ),
            },
            {
                "doc_id": "aurora-refund-policy",
                "source": "policies/refund_and_cancellation.md",
                "format": "markdown",
                "metadata": {
                    "dept": "support",
                    "category": "billing",
                    "year": 2026,
                    "refund_window_days": 14,
                    "_acl": {"visibility": "public"},
                },
                "text": (
                    "# 订阅计费与退费政策说明\n\n"
                    "## 1. 14 天无理由全额退费\n"
                    "企业新购包年套餐自开通之日起 14 个自然日内，若累计 Token 消耗低于配额的 5%，"
                    "可申请全额原路退费，审核通过后 3 至 5 个工作日到账。\n\n"
                    "## 2. 退费流程与审批\n"
                    "客户需在控制台提交退费工单并附上订单号（格式如 ORD-2026-XXXX）与开票状态。"
                    "已开具增值税专用发票的订单需先完成红冲作废。\n\n"
                    "## 3. 违约与欠费停机\n"
                    "账单逾期超过 7 天将进入只读冻结状态，控制面将在 30 天后执行租户物理存储擦除（Shred）。"
                ),
            },
            {
                "doc_id": "aurora-travel-expense",
                "source": "hr/travel_reimbursement_2026.md",
                "format": "markdown",
                "metadata": {
                    "dept": "finance",
                    "category": "expense",
                    "year": 2026,
                    "hotel_cap_cny": 650,
                    "_acl": {"visibility": "public"},
                },
                "text": (
                    "# 员工差旅住宿与报销标准（2026 修订）\n\n"
                    "## 1. 住宿报销上限\n"
                    "员工因公出差，一线城市（北上广深）住宿报销上限为每晚 650 元人民币，"
                    "二线及省会城市上限为每晚 480 元人民币。旺季超出标准 20% 以内需部门总监特批。\n\n"
                    "## 2. 交通与餐饮补贴\n"
                    "高铁默认二等座，连续飞行超过 4 小时可申请经济舱全价票。"
                    "出差期间每日餐饮包干补贴为 120 元，报销须在返岗后 15 天内提交电子发票。"
                ),
            },
            {
                "doc_id": "aurora-exec-comp-restricted",
                "source": "hr/executive_compensation_confidential.md",
                "format": "markdown",
                "metadata": {
                    "dept": "hr",
                    "category": "confidential",
                    "year": 2026,
                    "_acl": {
                        "visibility": "restricted",
                        "allow_roles": ["hr_director", "finance_vp"],
                        "allow_users": ["ceo-alice"],
                    },
                },
                "text": (
                    "# 核心高管薪酬与期权授予方案（绝密）\n\n"
                    "## 1. 绩效奖金系数\n"
                    "2026 年度 VP 及以上高管年终绩效基数为 6 个月固定月薪，达成 ARR 增长 80% 目标后"
                    "系数上浮至 1.8 倍。期权归属周期为 4 年（Cliff 1 年）。\n\n"
                    "## 2. 留任特别奖金\n"
                    "核心研发总监级另设 30 万元现金留任奖，分两年按季度发放，本文件仅限 HR 总监与财务 VP 查阅。"
                ),
            },
        ],
    },
    {
        "tenant_id": "cedar-medtech",
        "display_name": "青松医疗 · Cedar MedTech",
        "quota_docs": 300,
        "quota_chunks": 3000,
        "rate_limit_rpm": 120,
        "description": "医疗影像设备售后服务与合规知识库（与星辰云科完全物理隔离）",
        "fixed_key": "tr_live_cedar_demo_key_2026_enterprise",
        "docs": [
            {
                "doc_id": "cedar-warranty-2026",
                "source": "service/imaging_warranty_manual.md",
                "format": "markdown",
                "metadata": {
                    "dept": "support",
                    "category": "warranty",
                    "year": 2026,
                    "warranty_months": 36,
                    "_acl": {"visibility": "public"},
                },
                "text": (
                    "# 青松医疗影像设备质保与维保手册\n\n"
                    "## 1. 整机质保周期\n"
                    "青松医疗（Cedar MedTech）CT 与 MRI 系列主机提供自验收合格之日起 36 个月整机质保，"
                    "核心探测器与球管享有 24 个月原厂免费更换保障。\n\n"
                    "## 2. 退换货与备机响应\n"
                    "因硬件制造缺陷导致连续停机超过 48 小时，青松医疗提供同型号备用机或按日延长质保期 3 倍天数。"
                    "三甲医院客户享有 4 小时现场工程师到达承诺（SLA 99.98% 维保备件直达）。"
                ),
            },
            {
                "doc_id": "cedar-compliance-audit",
                "source": "compliance/hipaa_nmpa_guidelines.md",
                "format": "markdown",
                "metadata": {
                    "dept": "compliance",
                    "category": "security",
                    "year": 2026,
                    "_acl": {
                        "visibility": "restricted",
                        "allow_roles": ["compliance_officer", "qa_lead"],
                    },
                },
                "text": (
                    "# 医疗影像数据合规与审计操作规范\n\n"
                    "## 1. 患者隐私脱敏要求\n"
                    "所有 DICOM 影像元信息进入 RAG 检索索引前必须剥离患者姓名、身份证号与住院号，"
                    "仅保留脱敏后的设备型号、故障代码（如 ERR-CT-409）及维修日志。\n\n"
                    "## 2. 审计链留存\n"
                    "每次检索与调阅均须记录操作者角色与 SHA-256 链式摘要，保存周期不少于 5 年。"
                ),
            },
        ],
    },
]


def ensure_demo_seeded(engine) -> dict[str, Any]:
    """Idempotently seed demo tenants, keys, and bilingual documents."""
    seeded_tenants: list[dict[str, Any]] = []
    for spec in DEMO_TENANTS:
        tid = spec["tenant_id"]
        existing_tenants = {t["tenant_id"]: t for t in engine.control_plane.list_tenants()}
        if tid not in existing_tenants:
            engine.create_tenant(
                tid,
                display_name=spec["display_name"],
                quota_docs=spec["quota_docs"],
                quota_chunks=spec["quota_chunks"],
                rate_limit_rpm=spec["rate_limit_rpm"],
                description=spec["description"],
                actor_id="demo-seeder",
            )
        # Ensure fixed demo key exists and is active
        keys = engine.control_plane.list_keys(tid)
        active_demo_keys = [k for k in keys if k["name"] == "demo-full-access" and k["status"] == "active"]
        if not active_demo_keys:
            try:
                engine.control_plane.issue_key(
                    tid,
                    name="demo-full-access",
                    scopes=["read", "write", "chat", "eval", "tenant_admin"],
                    raw_key_override=spec["fixed_key"],
                )
            except Exception:
                # Key hash may already exist on an older revoked key; issue fresh key if needed
                issued = engine.control_plane.issue_key(
                    tid,
                    name="demo-full-access",
                    scopes=["read", "write", "chat", "eval", "tenant_admin"],
                )
                spec_key = issued["api_key"]
            else:
                spec_key = spec["fixed_key"]
        else:
            spec_key = spec["fixed_key"]

        node = engine.pool.get_node(tid, quota_docs=spec["quota_docs"])
        existing_docs = {d["doc_id"] for d in node.list_docs(limit=1000)}
        for doc in spec["docs"]:
            if doc["doc_id"] not in existing_docs:
                node.ingest_doc(
                    doc["text"],
                    doc_id=doc["doc_id"],
                    metadata=doc["metadata"],
                    source=doc["source"],
                    format=doc["format"],
                    max_chars=180,
                    overlap=30,
                )
        seeded_tenants.append(
            {
                "tenant_id": tid,
                "display_name": spec["display_name"],
                "api_key": spec_key,
                "docs": node.stats()["docs"],
                "chunks": node.stats()["chunks"],
            }
        )
    return {"tenants": seeded_tenants}
