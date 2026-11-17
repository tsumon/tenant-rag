"""FastAPI endpoint integration tests for the Enterprise Production Gateway."""
from __future__ import annotations

import json
import tempfile
import unittest

try:
    from fastapi.testclient import TestClient
    from tenantrag.enterprise.app import create_app
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False


@unittest.skipUnless(HAS_FASTAPI, "fastapi / httpx enterprise dependencies not installed")
class EnterpriseAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.admin_token = "test-superadmin-secret-token"
        self.app = create_app(
            data_dir=self.tmp.name,
            admin_token=self.admin_token,
            demo_mode=False,
            seed_demo=False,
        )
        self.client = TestClient(self.app)
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.tmp.cleanup()

    def test_admin_auth_fail_closed_and_full_lifecycle(self):
        # Demo bootstrap disabled when demo_mode=False
        self.assertEqual(self.client.get("/api/v1/admin/demo/bootstrap").status_code, 404)
        # Missing/wrong admin token rejected
        self.assertEqual(self.client.get("/api/v1/admin/overview").status_code, 401)
        self.assertEqual(
            self.client.get("/api/v1/admin/overview", headers={"X-Admin-Token": "wrong"}).status_code,
            401,
        )

        # Create tenant
        ah = {"X-Admin-Token": self.admin_token}
        r = self.client.post(
            "/api/v1/admin/tenants",
            headers=ah,
            json={"tenant_id": "corp-a", "display_name": "Corp A", "quota_docs": 10, "rate_limit_rpm": 60},
        )
        self.assertEqual(r.status_code, 200)

        # Issue key with scopes
        kr = self.client.post(
            "/api/v1/admin/tenants/corp-a/keys",
            headers=ah,
            json={"name": "rw-key", "scopes": ["read", "write", "chat", "eval"]},
        )
        self.assertEqual(kr.status_code, 200)
        api_key = kr.json()["key"]["api_key"]
        key_id = kr.json()["key"]["key_id"]
        th = {"Authorization": f"Bearer {api_key}"}

        # Readiness & whoami
        self.assertEqual(self.client.get("/api/v1/ready").status_code, 200)
        who = self.client.get("/api/v1/whoami", headers=th)
        self.assertEqual(who.status_code, 200)
        self.assertEqual(who.json()["tenant_id"], "corp-a")

        # Ingest + Document Detail + Search + SSE Chat
        ing = self.client.post(
            "/api/v1/ingest",
            headers=th,
            json={
                "doc_id": "policy-1",
                "source": "policy.md",
                "format": "markdown",
                "text": "# SLA Policy\n\nEnterprise uptime commitment is 99.95% monthly.",
                "metadata": {"dept": "cloud", "sla_pct": 99.95},
                "acl": {"visibility": "public"},
            },
        )
        self.assertEqual(ing.status_code, 200)

        det = self.client.get("/api/v1/docs/policy-1", headers=th)
        self.assertEqual(det.status_code, 200)
        self.assertGreaterEqual(len(det.json()["chunks"]), 1)

        sr = self.client.post(
            "/api/v1/search",
            headers=th,
            json={
                "query": "Enterprise uptime 99.95",
                "k": 3,
                "filters": {"dept": {"$eq": "cloud"}},
                "reranker": "lexical",
                "explain": True,
            },
        )
        self.assertEqual(sr.status_code, 200)
        self.assertTrue(sr.json()["evidence_sufficient"])
        self.assertIn("timings_ms", sr.json()["diagnostics"])

        # Multipart file upload (sync)
        up = self.client.post(
            "/api/v1/ingest/files",
            headers=th,
            files={"file": ("notes.html", b"<h1>Release Notes</h1><p>Zero downtime key rotation.</p>", "text/html")},
            data={"async_job": "false", "metadata_json": '{"dept":"ops"}'},
        )
        self.assertEqual(up.status_code, 200)
        self.assertEqual(up.json()["mode"], "sync")

        # SSE stream chat
        with self.client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers=th,
            json={"query": "Enterprise uptime 99.95", "k": 3},
        ) as stream_res:
            self.assertEqual(stream_res.status_code, 200)
            body_text = "".join(stream_res.iter_text())
            self.assertIn("event: retrieval", body_text)
            self.assertIn("event: delta", body_text)
            self.assertIn("event: citations", body_text)
            self.assertIn("event: done", body_text)

        # Online eval
        ev = self.client.post("/api/v1/eval/run", headers=th, json={"suite": "pipeline", "k": 3})
        self.assertEqual(ev.status_code, 200)
        self.assertIn("hybrid_rerank", ev.json()["summary"])

        # Classic /v1/* backward compatibility
        v1_idx = self.client.post("/v1/index", headers=th, json={"doc_id": "legacy-1", "text": "Legacy compatibility check"})
        self.assertEqual(v1_idx.status_code, 200)
        v1_sr = self.client.post("/v1/search", headers=th, json={"query": "Legacy compatibility", "k": 3})
        self.assertEqual(v1_sr.status_code, 200)
        self.assertGreaterEqual(len(v1_sr.json()["hits"]), 1)
        v1_us = self.client.get("/v1/usage", headers=th)
        self.assertEqual(v1_us.status_code, 200)
        self.assertIn("rate_limit", v1_us.json())

        # Backup, audit verification & metrics
        bkp = self.client.post("/api/v1/admin/tenants/corp-a/backups", headers=ah)
        self.assertEqual(bkp.status_code, 200)
        audit_ver = self.client.get("/api/v1/admin/audit/verify", headers=ah)
        self.assertEqual(audit_ver.status_code, 200)
        self.assertTrue(audit_ver.json()["valid"])

        prom = self.client.get("/metrics")
        self.assertEqual(prom.status_code, 200)
        self.assertIn("tenant_rag_http_requests_total", prom.text)

        # Key rotation and revocation
        rot = self.client.post(f"/api/v1/admin/keys/{key_id}/rotate", headers=ah, json={"grace_seconds": 0})
        self.assertEqual(rot.status_code, 200)
        # Old key now fails
        self.assertEqual(self.client.get("/api/v1/whoami", headers=th).status_code, 401)
        # New key works
        new_th = {"Authorization": f"Bearer {rot.json()['new_key']['api_key']}"}
        self.assertEqual(self.client.get("/api/v1/whoami", headers=new_th).status_code, 200)


if __name__ == "__main__":
    unittest.main()
