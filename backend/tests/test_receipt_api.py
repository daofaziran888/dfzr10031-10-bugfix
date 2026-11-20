"""回执差错追查 HTTP 接口集成测试：走完整 FastAPI 路由。"""
import sys
import unittest
from pathlib import Path

# 允许在独立 PYTHONPATH（无 venv 的环境）下运行；项目自身 venv 已装依赖时无副作用。
_BACKEND = Path(__file__).resolve().parents[1]
for _candidate in (Path("/tmp/pylibs"), _BACKEND):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from fastapi.testclient import TestClient  # noqa: E402

from app import seed  # noqa: E402
from app.main import app  # noqa: E402
from app.services import receipt_trace as rt  # noqa: E402
from app.store import store  # noqa: E402


class ReceiptTraceApiTest(unittest.TestCase):
    def setUp(self):
        store._tables = {name: [dict(row) for row in rows] for name, rows in seed.SEED_ROWS.items()}
        self.client = TestClient(app)

    def test_audit_trace_create_get_list_and_write_off(self):
        payload = {"values": {
            "审计操作编号": "AUDOP-1001",
            "节点编号": "",
            "尝试编号": "",
            "输入批号": "",
            "数据产物指纹": "",
        }}
        resp = self.client.post("/api/audit/receipt-traces", json=payload)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        report = body["entry"]["核对报告"]
        self.assertEqual(sorted(report["待复跑节点"]), ["PIPE-A", "PIPE-B", "PIPE-C"])
        self.assertEqual(report["未受影响节点"], ["PIPE-D"])
        self.assertEqual(
            sorted(r["下游结果编号"] for r in report["受影响结果清单"]),
            ["DOWN-R1", "DOWN-R2", "DOWN-R3"],
        )
        trace_id = body["entry"]["追查任务"]["id"]

        # 列表与明细。
        listed = self.client.get("/api/audit/receipt-traces").json()
        self.assertEqual(listed["total"], 1)
        detail = self.client.get(f"/api/audit/receipt-traces/{trace_id}").json()
        self.assertIn("留存证据", detail)
        evidence_ids = {e["回执编号"] for e in detail["留存证据"]}
        # 迟到失败回执与已接受重跑回执都在证据里。
        self.assertIn("RCPT-0001", evidence_ids)
        self.assertIn("RCPT-0002", evidence_ids)

        # 核销迟到的矛盾失败回执。
        off = self.client.post(
            f"/api/audit/receipt-traces/{trace_id}/write-off",
            json={"values": {"回执编号": "RCPT-0001"}, "remark": "迟到失败回执核销"},
        ).json()
        self.assertTrue(off["ok"], off)
        self.assertIn("RCPT-0001", off["entry"]["核销回执编号"])

        # 越权核销（不属于本追查证据）被拒。
        denied = self.client.post(
            f"/api/audit/receipt-traces/{trace_id}/write-off",
            json={"values": {"回执编号": "RCPT-0007"}},
        ).json()
        self.assertFalse(denied["ok"])

        # 明细 404。
        self.assertEqual(self.client.get("/api/audit/receipt-traces/9999").status_code, 404)

    def test_create_trace_requires_audit_op_id(self):
        resp = self.client.post("/api/audit/receipt-traces", json={"values": {"节点编号": "PIPE-A"}})
        body = resp.json()
        self.assertFalse(body["ok"])
        self.assertIn("审计操作编号", body["message"])

    def test_pipeline_suspicious_products_and_rerun_plan(self):
        resp = self.client.get("/api/pipeline/receipts/suspicious-products").json()
        fingerprints = sorted(item["数据产物指纹"] for item in resp["items"])
        # FP-B1 两次尝试只出现一次；FP-BX/FP-CX 引用不成立不入索引；FP-D1 节点健康不入索引。
        self.assertEqual(fingerprints, ["FP-A1", "FP-A2", "FP-B1"])
        b_item = next(item for item in resp["items"] if item["数据产物指纹"] == "FP-B1")
        self.assertEqual(sorted(b_item["引用尝试编号"]), ["ATT-1", "ATT-2"])
        self.assertTrue(b_item["可疑"])

        plan = self.client.get("/api/pipeline/receipts/rerun-plan").json()
        self.assertEqual(
            sorted(node["节点编号"] for node in plan["待复跑节点"]),
            ["PIPE-A", "PIPE-B", "PIPE-C"],
        )
        self.assertEqual(plan["未受影响节点"], ["PIPE-D"])
        b_plan = next(node for node in plan["待复跑节点"] if node["节点编号"] == "PIPE-B")
        self.assertIn("ATT-3", b_plan["涉及尝试编号"])

        # 节点过滤。
        only_a = self.client.get("/api/pipeline/receipts/rerun-plan", params={"node_id": "PIPE-A"}).json()
        self.assertEqual([n["节点编号"] for n in only_a["待复跑节点"]], ["PIPE-A"])

    def test_scoped_trace_by_attempt_and_fingerprint(self):
        resp = self.client.post("/api/audit/receipt-traces", json={"values": {
            "审计操作编号": "AUDOP-1002",
            "节点编号": "PIPE-B",
            "尝试编号": "ATT-3",
            "输入批号": "BATCH-B",
            "数据产物指纹": "FP-BX",
        }}).json()
        self.assertTrue(resp["ok"])
        node_b = resp["entry"]["核对报告"]["节点核对"][0]
        issue_codes = [i["差错类型"] for i in node_b["差错清单"]]
        # 精确到 ATT-3：产物引用错位必中；该范围只有失败回执，终态口径同样对不上。
        self.assertIn(rt.ISSUE_PRODUCT_MISPLACED, issue_codes)
        # 错位引用能追到具体尝试与指纹。
        misplaced = next(i for i in node_b["差错清单"]
                         if i["差错类型"] == rt.ISSUE_PRODUCT_MISPLACED)
        self.assertEqual(misplaced["关联回执编号"], ["RCPT-0005"])
        self.assertEqual(misplaced["涉及尝试编号"], ["ATT-3"])
        self.assertEqual(misplaced["错位产物指纹"], "FP-BX")
        self.assertEqual(node_b["产物索引"], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
