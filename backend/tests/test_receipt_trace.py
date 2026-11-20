"""回执差错追查核心规则验证：覆盖题目描述的完整场景。

运行：PYTHONPATH=/workspace/backend:/tmp/pylibs python3 -I /tmp/rt_tests/test_receipt_trace.py
"""
import sys
import unittest
from pathlib import Path

# 允许在独立 PYTHONPATH（无 venv 的环境）下运行；项目自身 venv 已装依赖时无副作用。
_BACKEND = Path(__file__).resolve().parents[1]
for _candidate in (Path("/tmp/pylibs"), _BACKEND):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from app.services import receipt_trace as rt  # noqa: E402
from app.store import store  # noqa: E402


class ReceiptTraceTest(unittest.TestCase):
    def setUp(self):
        # 每个用例拿一份干净的内存仓库。
        from app import seed
        store._tables = {name: [dict(row) for row in rows] for name, rows in seed.SEED_ROWS.items()}

    def test_full_scenario_classifies_three_issue_types(self):
        trace, missing = rt.create_trace({
            "审计操作编号": "AUDOP-0001",
            "节点编号": "",              # 留空表示不按节点收窄，覆盖全部节点
            "尝试编号": "",
            "输入批号": "",
            "数据产物指纹": "",
        })
        self.assertEqual(missing, [])
        report = rt.build_trace_report({
            "审计操作编号": "AUDOP-0001",
            "节点编号": "",
            "尝试编号": "",
            "输入批号": "",
            "数据产物指纹": "",
        })

        issue_codes = set(report["差错类型"])
        self.assertEqual(
            issue_codes,
            {rt.ISSUE_TERMINAL_MISMATCH, rt.ISSUE_PRODUCT_MISPLACED, rt.ISSUE_SUCCESS_MISSING},
        )

        # 待复跑：A/B/C；未受影响：D。
        self.assertEqual(sorted(report["待复跑节点"]), ["PIPE-A", "PIPE-B", "PIPE-C"])
        self.assertEqual(report["未受影响节点"], ["PIPE-D"])

        node_a = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-A")
        node_b = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-B")
        node_c = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-C")
        node_d = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-D")

        # A：终态对不上，矛盾终态同时含 失败/已完成。
        a_codes = {i["差错类型"] for i in node_a["差错清单"]}
        self.assertIn(rt.ISSUE_TERMINAL_MISMATCH, a_codes)
        self.assertEqual(set(next(i for i in node_a["差错清单"]
                                  if i["差错类型"] == rt.ISSUE_TERMINAL_MISMATCH)["矛盾终态"]),
                         {"失败", "已完成"})

        # B：产物引用错位，报错引用能追到 ATT-3。
        b_misplaced = [i for i in node_b["差错清单"] if i["差错类型"] == rt.ISSUE_PRODUCT_MISPLACED]
        self.assertEqual(len(b_misplaced), 1)
        self.assertEqual(b_misplaced[0]["涉及尝试编号"], ["ATT-3"])
        self.assertEqual(b_misplaced[0]["错位产物指纹"], "FP-BX")
        self.assertFalse(b_misplaced[0]["引用成立"])

        # C：成功回执丢失。
        self.assertIn(rt.ISSUE_SUCCESS_MISSING, {i["差错类型"] for i in node_c["差错清单"]})

        # D：无差错。
        self.assertFalse(node_d["可疑"])
        self.assertEqual(node_d["差错清单"], [])

    def test_duplicate_product_counted_once_across_attempts_and_retries(self):
        report = rt.build_trace_report({
            "审计操作编号": "AUDOP-0002", "节点编号": "PIPE-B",
            "尝试编号": "", "输入批号": "", "数据产物指纹": "",
        })
        node_b = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-B")
        fingerprints = [p["数据产物指纹"] for p in node_b["产物索引"]]
        # FP-B1 被 ATT-1/ATT-2 两次引用（含中断重试），只计一次；FP-BX 引用不成立不入索引。
        self.assertEqual(fingerprints, ["FP-B1"])
        product = node_b["产物索引"][0]
        self.assertEqual(sorted(product["引用尝试编号"]), ["ATT-1", "ATT-2"])
        # 全局可疑产物索引同样按指纹去重。
        index = rt.suspicious_product_index()
        b_products = [p for p in index if p["节点编号"] == "PIPE-B"]
        self.assertEqual([p["数据产物指纹"] for p in b_products], ["FP-B1"])

    def test_late_receipt_evidence_retained_and_downstream_conflict(self):
        report = rt.build_trace_report({
            "审计操作编号": "AUDOP-0003", "节点编号": "PIPE-A",
            "尝试编号": "", "输入批号": "", "数据产物指纹": "",
        })
        node_a = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-A")
        # 迟到失败回执与重跑成功回执两个不同指纹都在索引里，且都仍被下游消费。
        self.assertEqual(
            sorted(p["数据产物指纹"] for p in node_a["产物索引"]),
            ["FP-A1", "FP-A2"],
        )
        self.assertTrue(all(p["仍被下游消费"] for p in node_a["产物索引"]))
        # 同一下游结果 DOWN-R1 中两个指纹并存 → 指纹冲突。
        r1 = next(r for r in node_a["受影响结果"] if r["下游结果编号"] == "DOWN-R1")
        self.assertTrue(r1["指纹冲突"])
        self.assertEqual(sorted(r1["引用产物指纹"]), ["FP-A1", "FP-A2"])
        # 迟到回执编号进入证据清单。
        self.assertIn("RCPT-0001", report["证据回执编号"])

    def test_affected_results_dedup_and_unaffected_result_excluded(self):
        plan = rt.rerun_plan()
        affected_ids = sorted(r["下游结果编号"] for r in plan["受影响结果清单"])
        self.assertEqual(affected_ids, ["DOWN-R1", "DOWN-R2", "DOWN-R3"])
        self.assertEqual(plan["未受影响节点"], ["PIPE-D"])
        # DOWN-R4 只被健康节点 D 引用，不出现在受影响清单。
        self.assertNotIn("DOWN-R4", affected_ids)
        # 复跑清单里 B 节点能追到报错尝试。
        b_plan = next(n for n in plan["待复跑节点"] if n["节点编号"] == "PIPE-B")
        self.assertIn("ATT-3", b_plan["涉及尝试编号"])

    def test_write_off_keeps_evidence_and_receipts_untouched(self):
        trace, _ = rt.create_trace({
            "审计操作编号": "AUDOP-0004", "节点编号": "PIPE-A",
            "尝试编号": "", "输入批号": "", "数据产物指纹": "",
        })
        receipts_before = [dict(r) for r in store.rows(rt.RECEIPT_MODULE)]
        updated, message = rt.write_off(int(trace["id"]), "RCPT-0001", "迟到失败回执核销")
        self.assertIsNotNone(updated, message)
        self.assertIn("RCPT-0001", updated["核销回执编号"])
        # 原始回执台账一条不删、内容不变。
        self.assertEqual(
            [dict(r) for r in store.rows(rt.RECEIPT_MODULE)],
            receipts_before,
        )
        evidence = rt.get_trace_evidence(trace["追查编号"])
        ev1 = next(e for e in evidence if e["回执编号"] == "RCPT-0001")
        self.assertEqual(ev1["核销状态"], "已核销")
        self.assertTrue(ev1["证据保留"])
        # 另一条证据仍待核销，任务整体保持待核销。
        ev2 = next(e for e in evidence if e["回执编号"] == "RCPT-0002")
        self.assertEqual(ev2["核销状态"], "待核销")
        self.assertEqual(updated["status"], rt.TRACE_STATUS_OPEN)
        # 不属于本追查证据范围的回执不能核销。
        again, msg = rt.write_off(int(trace["id"]), "RCPT-0007", "")
        self.assertIsNone(again)
        self.assertIn("证据范围", msg)
        # 核销剩余证据后任务整体核销。
        rt.write_off(int(trace["id"]), "RCPT-0002", "")
        self.assertEqual(rt.get_trace(int(trace["id"]))["status"], rt.TRACE_STATUS_WRITTEN_OFF)

    def test_write_off_rejects_unknown_and_duplicate(self):
        trace, _ = rt.create_trace({
            "审计操作编号": "AUDOP-0005", "节点编号": "PIPE-A",
            "尝试编号": "", "输入批号": "", "数据产物指纹": "",
        })
        bad, msg = rt.write_off(9999, "RCPT-0001", "")
        self.assertIsNone(bad)
        rt.write_off(int(trace["id"]), "RCPT-0001", "")
        dup, msg2 = rt.write_off(int(trace["id"]), "RCPT-0001", "")
        self.assertIsNone(dup)
        self.assertIn("重复核销", msg2)

    def test_scope_filters_by_attempt_batch_fingerprint(self):
        # 精确到 ATT-3 的追查只看到报错尝试，仍然识别出错位引用。
        report = rt.build_trace_report({
            "审计操作编号": "AUDOP-0006", "节点编号": "PIPE-B",
            "尝试编号": "ATT-3", "输入批号": "BATCH-B", "数据产物指纹": "FP-BX",
        })
        node_b = next(n for n in report["节点核对"] if n["节点编号"] == "PIPE-B")
        self.assertEqual(
            [i["差错类型"] for i in node_b["差错清单"]],
            [rt.ISSUE_PRODUCT_MISPLACED],
        )
        # 精确范围下 FP-BX 引用不成立，产物索引为空。
        self.assertEqual(node_b["产物索引"], [])

    def test_missing_required_fields_rejected(self):
        trace, missing = rt.create_trace({})
        self.assertIsNone(trace)
        self.assertEqual(missing, ["审计操作编号"])
        # 尝试编号、输入批号、数据产物指纹允许留空（不按该维度收窄）。
        trace2, missing2 = rt.create_trace({
            "审计操作编号": "AUDOP-0008", "节点编号": "PIPE-D",
            "尝试编号": "", "输入批号": "", "数据产物指纹": "",
        })
        self.assertEqual(missing2, [])
        self.assertIsNotNone(trace2)
        # 节点编号留空表示覆盖全部节点的全局追查，同样允许登记。
        trace3, missing3 = rt.create_trace({
            "审计操作编号": "AUDOP-0009", "节点编号": "",
            "尝试编号": "", "输入批号": "", "数据产物指纹": "",
        })
        self.assertEqual(missing3, [])
        self.assertIsNotNone(trace3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
