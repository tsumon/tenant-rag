"""Unit tests for enterprise core modules (ControlPlane, TenantPool, Retrieval, Rerank, Parsers, Chat, Audit)."""
from __future__ import annotations

import io
import sqlite3
import tempfile
import time
import unittest
import zipfile

from tenantrag.enterprise import (
    ControlPlane,
    EnterpriseTenantRAG,
    ForbiddenError,
    LexicalCrossReranker,
    TenantPool,
    UnauthorizedError,
    parse_document_bytes,
)
from tenantrag.enterprise.control_plane import validate_tenant_id


def _build_minimal_docx(paragraphs: list[tuple[str, str]]) -> bytes:
    """Create a valid minimal .docx zip archive using Python stdlib only."""
    body_xml = []
    for style, text in paragraphs:
        ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
        body_xml.append(f"<w:p>{ppr}<w:r><w:t>{text}</w:t></w:r></w:p>")
    doc_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body_xml)}</w:body></w:document>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", doc_xml)
    return buf.getvalue()


class EnterpriseCoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = EnterpriseTenantRAG(self.tmp.name)

    def tearDown(self):
        self.engine.close()
        self.tmp.cleanup()

    def test_tenant_id_path_traversal_rejected(self):
        for bad in ("", ".", "..", "../etc", "a/b", "tenant with space"):
            with self.assertRaises(ValueError):
                validate_tenant_id(bad)

    def test_iam_scoped_keys_rotation_revocation_and_suspension(self):
        self.engine.create_tenant("acme", display_name="Acme Corp", quota_docs=50, rate_limit_rpm=60)
        k1 = self.engine.control_plane.issue_key("acme", name="reader", scopes=["read"])
        ctx = self.engine.control_plane.authenticate_key(k1["api_key"], required_scope="read")
        self.assertEqual(ctx["tenant_id"], "acme")

        with self.assertRaises(ForbiddenError):
            self.engine.control_plane.authenticate_key(k1["api_key"], required_scope="write")

        # Zero-downtime rotation with grace period: both old and new work initially
        rot = self.engine.control_plane.rotate_key(k1["key_id"], grace_seconds=300)
        new_raw = rot["new_key"]["api_key"]
        self.assertEqual(self.engine.control_plane.authenticate_key(k1["api_key"], required_scope="read")["tenant_id"], "acme")
        self.assertEqual(self.engine.control_plane.authenticate_key(new_raw, required_scope="read")["tenant_id"], "acme")

        # Immediate rotation (grace=0) revokes old key at once
        rot2 = self.engine.control_plane.rotate_key(rot["new_key"]["key_id"], grace_seconds=0)
        with self.assertRaises(UnauthorizedError):
            self.engine.control_plane.authenticate_key(new_raw)

        # Tenant suspension circuit breaker blocks active keys immediately
        self.engine.update_tenant("acme", status="suspended")
        with self.assertRaises(ForbiddenError):
            self.engine.control_plane.authenticate_key(rot2["new_key"]["api_key"])

        self.engine.update_tenant("acme", status="active")
        self.assertEqual(
            self.engine.control_plane.authenticate_key(rot2["new_key"]["api_key"])["tenant_id"],
            "acme",
        )

        # Revocation blocks forever
        self.engine.control_plane.revoke_key(rot2["new_key"]["key_id"])
        with self.assertRaises(UnauthorizedError):
            self.engine.control_plane.authenticate_key(rot2["new_key"]["api_key"])

    def test_physical_sqlite_isolation_and_hot_backup_restore(self):
        self.engine.create_tenant("t-alpha", quota_docs=20)
        self.engine.create_tenant("t-beta", quota_docs=20)
        self.engine.ingest_text("t-alpha", "Alpha confidential budget 8848 CNY", doc_id="budget")
        self.engine.ingest_text("t-beta", "Beta medical device warranty 36 months", doc_id="warranty")

        alpha_db = self.engine.pool.tenants_dir / "t-alpha" / "index.db"
        beta_db = self.engine.pool.tenants_dir / "t-beta" / "index.db"
        self.assertTrue(alpha_db.is_file())
        self.assertTrue(beta_db.is_file())

        # Physical isolation check: alpha never sees beta
        res_a = self.engine.search("t-alpha", "medical warranty", check_abstention=False)
        self.assertTrue(all("medical" not in h["text"].lower() for h in res_a["hits"]))

        # Online hot backup & restore after accidental deletion
        bkp = self.engine.create_backup("t-alpha")
        self.assertTrue(bkp["integrity_ok"])
        self.assertEqual(bkp["doc_count"], 1)

        node_a = self.engine.get_tenant_node("t-alpha")
        self.assertTrue(node_a.delete_doc("budget"))
        self.assertEqual(node_a.stats()["docs"], 0)

        restored_stats = self.engine.restore_backup("t-alpha", bkp["backup_id"])
        self.assertEqual(restored_stats["docs"], 1)
        res_after = self.engine.search("t-alpha", "budget 8848")
        self.assertEqual(len(res_after["hits"]), 1)

    def test_metadata_dsl_acl_prefilter_rerank_context_and_abstention(self):
        self.engine.create_tenant("corp")
        long_handbook = (
            "# Part One: Onboarding\nEmployees receive laptops on day one.\n\n"
            "# Part Two: Travel Cap\nFirst-tier city hotel reimbursement cap is 650 CNY per night.\n\n"
            "# Part Three: Meal Stipend\nDaily meal stipend is 120 CNY submitted within 15 days."
        )
        self.engine.ingest_text(
            "corp",
            long_handbook,
            doc_id="handbook",
            source="handbook.md",
            format="markdown",
            max_chars=95,
            overlap=15,
            metadata={"dept": "finance", "year": 2026, "tier": "all", "_acl": {"visibility": "public"}},
        )
        self.engine.ingest_text(
            "corp",
            "Executive bonus multiplier 1.8x and 300k retention award.",
            doc_id="exec-comp",
            source="exec.md",
            metadata={
                "dept": "hr",
                "year": 2026,
                "_acl": {"visibility": "restricted", "allow_roles": ["hr_director"]},
            },
        )

        # Ordinary user blocked from restricted doc during pre-filtering
        res_public = self.engine.search(
            "corp",
            "Executive bonus retention",
            principal={"user_id": "u1", "roles": ["engineer"]},
            explain=True,
        )
        self.assertTrue(all("exec-comp" not in h["doc_id"] and h.get("metadata", {}).get("_chunk", {}).get("parent_id") != "exec-comp" for h in res_public["hits"]))
        self.assertGreaterEqual(res_public["diagnostics"]["filtered_out_acl"], 1)

        # HR director sees restricted doc
        res_hr = self.engine.search(
            "corp",
            "Executive bonus retention",
            principal={"user_id": "hr1", "roles": ["hr_director"]},
            explain=True,
        )
        self.assertTrue(res_hr["evidence_sufficient"])
        self.assertGreaterEqual(len(res_hr["hits"]), 1)

        # Metadata DSL filter + sibling context expansion
        res_meta = self.engine.search(
            "corp",
            "hotel reimbursement 650",
            filters={"dept": {"$eq": "finance"}, "year": {"$gte": 2025}},
            context_window=1,
            reranker_mode="lexical",
            explain=True,
        )
        self.assertTrue(res_meta["evidence_sufficient"])
        self.assertIn("650", res_meta["hits"][0].get("expanded_text", res_meta["hits"][0]["text"]))

        # Out-of-scope query triggers abstention gate & chat refusal
        res_oos = self.engine.search("corp", "NASA Artemis lunar propellant")
        self.assertFalse(res_oos["evidence_sufficient"])
        chat_oos = self.engine.chat.answer(res_oos, "NASA Artemis lunar propellant")
        self.assertTrue(chat_oos["abstained"])
        self.assertEqual(chat_oos["citations"], [])

        # Grounded chat returns inline [1] citation with offsets
        chat_ok = self.engine.chat.answer(res_meta, "hotel reimbursement 650")
        self.assertFalse(chat_ok["abstained"])
        self.assertGreaterEqual(len(chat_ok["citations"]), 1)
        self.assertIn("[1]", chat_ok["answer"])

    def test_parsers_and_async_jobs(self):
        # DOCX
        docx_bytes = _build_minimal_docx([("Heading1", "Cloud SLA Policy"), ("Normal", "Monthly uptime 99.95% guaranteed.")])
        parsed_docx = parse_document_bytes("sla.docx", docx_bytes)
        self.assertEqual(parsed_docx["format"], "markdown")
        self.assertIn("# Cloud SLA Policy", parsed_docx["text"])
        self.assertIn("99.95%", parsed_docx["text"])

        # HTML
        html_bytes = b"<html><script>alert(1)</script><body><h1>Billing</h1><p>14 day refund window.</p></body></html>"
        parsed_html = parse_document_bytes("billing.html", html_bytes)
        self.assertIn("# Billing", parsed_html["text"])
        self.assertNotIn("alert", parsed_html["text"])

        # CSV
        csv_bytes = "sku,price\nPro,199\n".encode("utf-8")
        parsed_csv = parse_document_bytes("plans.csv", csv_bytes)
        self.assertIn("Pro", parsed_csv["text"])
        self.assertIn("199", parsed_csv["text"])

        # Async file ingestion job
        self.engine.create_tenant("job-tenant")
        out = self.engine.ingest_file_bytes("job-tenant", "sla.docx", docx_bytes, async_job=True)
        self.assertEqual(out["mode"], "async")
        job_id = out["job"]["job_id"]
        deadline = time.time() + 5.0
        job = out["job"]
        while time.time() < deadline and job["status"] in ("queued", "running"):
            time.sleep(0.05)
            job = self.engine.control_plane.get_job(job_id)
        self.assertEqual(job["status"], "succeeded")
        self.assertEqual(job["progress_pct"], 100)

    def test_audit_hash_chain_and_tamper_detection(self):
        self.engine.create_tenant("aud-t")
        self.engine.ingest_text("aud-t", "Audit test document content", doc_id="d1")
        v1 = self.engine.control_plane.verify_audit_chain()
        self.assertTrue(v1["valid"])
        self.assertGreaterEqual(v1["checked"], 2)

        # Directly tamper with SQLite audit row
        with sqlite3.connect(self.engine.control_plane.db_path) as con:
            con.execute("UPDATE audit_logs SET detail_json='{\"tampered\":true}' WHERE audit_id=1")
            con.commit()

        v2 = self.engine.control_plane.verify_audit_chain()
        self.assertFalse(v2["valid"])
        self.assertEqual(v2["broken_at"], 1)


if __name__ == "__main__":
    unittest.main()
