"""管道运行回执差错追查的共享业务规则。

审计模块负责“追查任务”的登记、核销与证据留存，分析管道模块负责读取可疑产物
索引、生成待复跑节点与受影响结果清单；两边用的是同一套核对口径，因此收敛在
本模块里，避免两个服务各写一份判断逻辑导致结论不一致。

数据全部放在内存仓库（app.store）的四张表里：

- ``pipeline_receipt``：管道运行回执。每个节点每次尝试的提交回执与迟到回执各
  一条，记录终态、数据产物指纹与引用是否成立。
- ``pipeline_downstream``：下游结果索引。记录每份下游结果实际引用了哪些节点的
  哪个数据产物指纹，是“按实际产物引用”追查的依据。
- ``audit_trace``：回执差错追查任务。一次追查对应一条，固化核对结论与证据。
- ``audit_trace_receipt``：追查任务关联到的回执证据，核销只改关联状态，不删除
  原始回执，避免纠错时误清理下游仍在消费的产物。
"""
from __future__ import annotations

from typing import Any

from app.store import store

RECEIPT_MODULE = "pipeline_receipt"
DOWNSTREAM_MODULE = "pipeline_downstream"
TRACE_MODULE = "audit_trace"
TRACE_RECEIPT_MODULE = "audit_trace_receipt"

# 追查入口接收的五个定位字段；除审计操作编号外均可留空，留空表示不按该维度收窄。
SCOPE_FIELDS = ["审计操作编号", "节点编号", "尝试编号", "输入批号", "数据产物指纹"]
REQUIRED_SCOPE_FIELDS = ["审计操作编号"]

RECEIPT_SUCCESS = "成功"
RECEIPT_FAILURE = "失败"
NODE_TERMINAL_DONE = "已完成"
NODE_TERMINAL_FAILED = "失败"

# 追查任务自身的状态流转。
TRACE_STATUS_OPEN = "待核销"
TRACE_STATUS_WRITTEN_OFF = "已核销"

# 三类差错的固定编码，前端与测试都按编码识别，不依赖中文文案是否调整。
ISSUE_TERMINAL_MISMATCH = "terminal_mismatch"
ISSUE_PRODUCT_MISPLACED = "product_misplaced"
ISSUE_SUCCESS_MISSING = "success_missing"

ISSUE_LABELS = {
    ISSUE_TERMINAL_MISMATCH: "终态对不上",
    ISSUE_PRODUCT_MISPLACED: "产物引用错位",
    ISSUE_SUCCESS_MISSING: "成功回执丢失",
}


def _norm(value: Any) -> str:
    """把编号、指纹这类标识统一成去空白的字符串，空值归一为空串。"""
    return str(value or "").strip()


def _next_id(rows: list[dict[str, Any]]) -> int:
    return max((int(row.get("id", 0)) for row in rows), default=0) + 1


def scope_matches(receipt: dict[str, Any], scope: dict[str, Any]) -> bool:
    """判断一条回执是否落在追查范围内。

    追查入口接收五个定位字段；提交时留空的字段视为不限制（节点编号留空即覆盖
    全部节点），填了的字段必须与回执完全一致。
    """
    field_map = {
        "节点编号": "节点编号",
        "尝试编号": "尝试编号",
        "输入批号": "输入批号",
        "数据产物指纹": "数据产物指纹",
    }
    for scope_field, receipt_field in field_map.items():
        expected = _norm(scope.get(scope_field))
        if not expected:
            continue
        if _norm(receipt.get(receipt_field)) != expected:
            return False
    return True


def _success_receipts(receipts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in receipts if _norm(r.get("回执结果")) == RECEIPT_SUCCESS]


def _terminal_states(receipts: list[dict[str, Any]]) -> set[str]:
    return {_norm(r.get("节点终态")) for r in receipts if _norm(r.get("节点终态"))}


def analyze_node(
    node_id: str,
    receipts: list[dict[str, Any]],
    downstream_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """核对单个节点的全部回执，输出差错、实际产物索引与下游影响。

    影响范围按“数据产物指纹”去重：同一个产物被多次尝试引用，或取数中断后重试
    再次命中同一个有效产物，都只计一次。
    """
    node_receipts = [r for r in receipts if _norm(r.get("节点编号")) == node_id]
    issues: list[dict[str, Any]] = []

    terminal_states = _terminal_states(node_receipts)
    success_rows = _success_receipts(node_receipts)
    has_success = bool(success_rows)
    accepted_rows = [r for r in node_receipts if r.get("已接受")]

    # 差错一：终态对不上。同一追查范围内同一节点的回执出现互相矛盾的终态口径
    # （例如中断前计算的失败回执迟到，与已经接受的重跑成功回执同时存在）。
    # 单条回执不与“已接受”口径做比较，避免把成功回执丢失场景误判成终态矛盾。
    if len(terminal_states) > 1:
        contradict_rows = [
            r for r in node_receipts
            if _norm(r.get("节点终态")) and _norm(r.get("节点终态")) != NODE_TERMINAL_DONE
        ] or node_receipts
        issues.append({
            "差错类型": ISSUE_TERMINAL_MISMATCH,
            "差错说明": ISSUE_LABELS[ISSUE_TERMINAL_MISMATCH],
            "矛盾终态": sorted(terminal_states),
            "关联回执编号": [_norm(r.get("回执编号")) for r in contradict_rows],
            "涉及尝试编号": sorted({_norm(r.get("尝试编号")) for r in contradict_rows if _norm(r.get("尝试编号"))}),
        })

    # 差错二：产物引用错位。两类情况：
    # 1) 回执引用本身不成立（导致执行报错的引用，如 PIPE-B 的 ATT-3 关联 FP-BX），
    #    无论回执成败都要能追到对应尝试；
    # 2) 成功回执声称的产物在本追查范围可见的成功产物索引里找不到（关联错的产物）。
    # 失败回执的产物不与成功产物索引比对：失败尝试不要求产出成功产物，避免把
    # 迟到的失败回执误判成引用错位。
    valid_fingerprints = {
        _norm(r.get("数据产物指纹"))
        for r in success_rows
        if _norm(r.get("数据产物指纹")) and r.get("引用成立", True)
    }
    for row in node_receipts:
        fingerprint = _norm(row.get("数据产物指纹"))
        reference_valid = bool(row.get("引用成立", True))
        is_success = _norm(row.get("回执结果")) == RECEIPT_SUCCESS
        misplaced = not reference_valid or (
            is_success and bool(fingerprint) and fingerprint not in valid_fingerprints
        )
        if misplaced:
            issues.append({
                "差错类型": ISSUE_PRODUCT_MISPLACED,
                "差错说明": ISSUE_LABELS[ISSUE_PRODUCT_MISPLACED],
                "关联回执编号": [_norm(row.get("回执编号"))],
                "涉及尝试编号": [_norm(row.get("尝试编号"))],
                "错位产物指纹": fingerprint,
                "引用成立": reference_valid,
            })

    # 差错三：成功回执丢失。节点已接受口径为完成，却没有任何成功回执可核对。
    accepted_done = any(_norm(r.get("节点终态")) == NODE_TERMINAL_DONE for r in accepted_rows)
    if accepted_done and not has_success:
        issues.append({
            "差错类型": ISSUE_SUCCESS_MISSING,
            "差错说明": ISSUE_LABELS[ISSUE_SUCCESS_MISSING],
            "关联回执编号": [_norm(r.get("回执编号")) for r in node_receipts],
            "涉及尝试编号": sorted({_norm(r.get("尝试编号")) for r in node_receipts if _norm(r.get("尝试编号"))}),
        })

    # 实际产物索引：收录引用成立的产物（含迟到但仍被下游消费的产物），按指纹去重。
    # 失败尝试只要引用成立、产物确实存在，也保留在索引里，防止纠错时误清理。
    product_index: dict[str, dict[str, Any]] = {}
    for row in node_receipts:
        fingerprint = _norm(row.get("数据产物指纹"))
        if not fingerprint or not row.get("引用成立", True):
            continue
        entry = product_index.setdefault(fingerprint, {
            "节点编号": node_id,
            "数据产物指纹": fingerprint,
            "引用尝试编号": [],
            "下游结果编号": [],
            "仍被下游消费": False,
            "可疑": False,
        })
        attempt_no = _norm(row.get("尝试编号"))
        if attempt_no and attempt_no not in entry["引用尝试编号"]:
            entry["引用尝试编号"].append(attempt_no)

    # 下游结果按“实际产物引用”反查；同一结果引用同一产物只记一次。引用的产物在
    # 节点实际产物索引里找不到的（关联错的产物、成功回执丢失后无法核对的产物），
    # 仍登记为受影响结果并标记“产物缺失”，但不进入产物索引。
    affected_results: dict[str, dict[str, Any]] = {}
    for ds_row in downstream_rows:
        if _norm(ds_row.get("节点编号")) != node_id:
            continue
        result_id = _norm(ds_row.get("下游结果编号"))
        fingerprint = _norm(ds_row.get("数据产物指纹"))
        if not result_id:
            continue
        affected = affected_results.setdefault(result_id, {
            "下游结果编号": result_id,
            "引用节点编号": node_id,
            "引用产物指纹": [],
            "指纹冲突": False,
            "产物缺失": False,
        })
        if fingerprint in product_index:
            entry = product_index[fingerprint]
            if result_id not in entry["下游结果编号"]:
                entry["下游结果编号"].append(result_id)
            entry["仍被下游消费"] = True
        else:
            affected["产物缺失"] = True
        if fingerprint and fingerprint not in affected["引用产物指纹"]:
            affected["引用产物指纹"].append(fingerprint)

    # 同一份下游结果里出现两个不同指纹（中断前迟到产物与重跑产物并存），标记冲突。
    for affected in affected_results.values():
        affected["指纹冲突"] = len(affected["引用产物指纹"]) > 1

    # 有差错的节点，其产物索引整体标记为可疑，供分析管道侧读取。
    suspicious = bool(issues)
    if suspicious:
        for entry in product_index.values():
            entry["可疑"] = True

    return {
        "节点编号": node_id,
        "可疑": suspicious,
        "差错清单": issues,
        "产物索引": sorted(product_index.values(), key=lambda item: item["数据产物指纹"]),
        "受影响结果": sorted(affected_results.values(), key=lambda item: item["下游结果编号"]),
        "待复跑": suspicious,
    }


def build_trace_report(scope: dict[str, Any]) -> dict[str, Any]:
    """按追查范围核对回执，生成完整追查报告（不写库）。"""
    all_receipts = store.rows(RECEIPT_MODULE)
    downstream_rows = store.rows(DOWNSTREAM_MODULE)
    scoped = [r for r in all_receipts if scope_matches(r, scope)]

    node_ids = sorted({_norm(r.get("节点编号")) for r in scoped if _norm(r.get("节点编号"))})
    node_reports = [analyze_node(node_id, scoped, downstream_rows) for node_id in node_ids]

    rerun_nodes = [report["节点编号"] for report in node_reports if report["待复跑"]]
    unaffected_nodes = [report["节点编号"] for report in node_reports if not report["待复跑"]]

    # 受影响结果与涉及产物都按编号/指纹去重。
    result_map: dict[str, dict[str, Any]] = {}
    product_map: dict[str, dict[str, Any]] = {}
    evidence_receipt_ids: list[str] = []
    for report in node_reports:
        for product in report["产物索引"]:
            product_map.setdefault(product["数据产物指纹"], dict(product))
        if not report["可疑"]:
            # 健康节点的下游结果不属于受影响范围，整体排除。
            continue
        for affected in report["受影响结果"]:
            result_map.setdefault(affected["下游结果编号"], dict(affected))
        if not report["可疑"]:
            continue
        # 可疑节点的全部回执都作为证据留存（含迟到回执与已接受的重跑回执），
        # 核销只标记状态、不删除，避免下游仍在消费的产物被误清理。
        for receipt in scoped:
            if _norm(receipt.get("节点编号")) != report["节点编号"]:
                continue
            receipt_no = _norm(receipt.get("回执编号"))
            if receipt_no and receipt_no not in evidence_receipt_ids:
                evidence_receipt_ids.append(receipt_no)
        for issue in report["差错清单"]:
            for receipt_no in issue.get("关联回执编号", []):
                if receipt_no and receipt_no not in evidence_receipt_ids:
                    evidence_receipt_ids.append(receipt_no)

    issue_codes = sorted({
        issue["差错类型"]
        for report in node_reports
        for issue in report["差错清单"]
    })

    return {
        "追查范围": {field: _norm(scope.get(field)) for field in REQUIRED_SCOPE_FIELDS},
        "节点核对": node_reports,
        "待复跑节点": rerun_nodes,
        "未受影响节点": unaffected_nodes,
        "受影响结果清单": sorted(result_map.values(), key=lambda item: item["下游结果编号"]),
        "可疑产物索引": sorted(product_map.values(), key=lambda item: item["数据产物指纹"]),
        "差错类型": issue_codes,
        "证据回执编号": evidence_receipt_ids,
        "存在差错": bool(issue_codes),
    }


def create_trace(scope: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """登记一次回执差错追查并固化核对结论。"""
    missing = [field for field in REQUIRED_SCOPE_FIELDS if not _norm(scope.get(field))]
    if missing:
        return None, missing

    report = build_trace_report(scope)
    trace_rows = store.rows(TRACE_MODULE)
    trace = {
        "id": _next_id(trace_rows),
        "追查编号": f"TRCE-{len(trace_rows) + 1:04d}",
        "审计操作编号": _norm(scope.get("审计操作编号")),
        "节点编号": _norm(scope.get("节点编号")),
        "尝试编号": _norm(scope.get("尝试编号")),
        "输入批号": _norm(scope.get("输入批号")),
        "数据产物指纹": _norm(scope.get("数据产物指纹")),
        "status": TRACE_STATUS_OPEN,
        "pending": True,
        "abnormal": report["存在差错"],
        "待复跑节点": report["待复跑节点"],
        "未受影响节点": report["未受影响节点"],
        "受影响结果编号": [item["下游结果编号"] for item in report["受影响结果清单"]],
        "差错类型": report["差错类型"],
        "证据回执编号": report["证据回执编号"],
        "核销回执编号": [],
        "核销说明": "",
    }
    trace_rows.append(trace)

    # 证据关联落库：核销只改这张关联表与追查任务状态，原始回执一律保留。
    link_rows = store.rows(TRACE_RECEIPT_MODULE)
    for receipt_no in report["证据回执编号"]:
        link_rows.append({
            "id": _next_id(link_rows),
            "追查编号": trace["追查编号"],
            "回执编号": receipt_no,
            "核销状态": "待核销",
            "证据保留": True,
        })
    return trace, []


def list_traces(
    *,
    keyword: str | None = None,
    status: str | None = None,
    page: int = 1,
    size: int = 20,
) -> tuple[list[dict[str, Any]], int]:
    rows = store.rows(TRACE_MODULE)
    if keyword:
        rows = [row for row in rows if keyword in _norm(row.get("追查编号")) or keyword in _norm(row.get("审计操作编号"))]
    if status:
        rows = [row for row in rows if row.get("status") == status]
    total = len(rows)
    start = max(page - 1, 0) * size
    return rows[start:start + size], total


def get_trace(trace_id: int) -> dict[str, Any] | None:
    return store.find(TRACE_MODULE, trace_id)


def get_trace_evidence(trace_no: str) -> list[dict[str, Any]]:
    """读取一次追查保留下来的回执证据（含核销状态）。"""
    links = [row for row in store.rows(TRACE_RECEIPT_MODULE) if _norm(row.get("追查编号")) == trace_no]
    receipt_by_no = {_norm(row.get("回执编号")): row for row in store.rows(RECEIPT_MODULE)}
    result = []
    for link in links:
        receipt = receipt_by_no.get(_norm(link.get("回执编号")), {})
        result.append({
            "回执编号": link.get("回执编号"),
            "核销状态": link.get("核销状态"),
            "证据保留": link.get("证据保留"),
            "节点编号": receipt.get("节点编号", ""),
            "尝试编号": receipt.get("尝试编号", ""),
            "回执结果": receipt.get("回执结果", ""),
            "节点终态": receipt.get("节点终态", ""),
            "数据产物指纹": receipt.get("数据产物指纹", ""),
            "迟到": receipt.get("迟到", False),
        })
    return result


def write_off(trace_id: int, receipt_no: str, remark: str) -> tuple[dict[str, Any] | None, str]:
    """核销一条终态残留的矛盾回执。

    只在审计日志里把矛盾回执标记为“已核销”，不删除回执、不清理任何数据产物：
    迟到回执的证据必须留存，防止下游仍在消费的产物被纠错流程误清理。
    """
    trace = store.find(TRACE_MODULE, trace_id)
    if trace is None:
        return None, f"追查任务 {trace_id} 不存在或已归档"
    receipt_no = _norm(receipt_no)
    if not receipt_no:
        return None, "缺少待核销的回执编号"

    link = next(
        (row for row in store.rows(TRACE_RECEIPT_MODULE)
         if _norm(row.get("追查编号")) == _norm(trace.get("追查编号"))
         and _norm(row.get("回执编号")) == receipt_no),
        None,
    )
    if link is None:
        return None, f"回执 {receipt_no} 不属于追查任务 {trace.get('追查编号')} 的证据范围，不能核销"
    if link.get("核销状态") == "已核销":
        return None, f"回执 {receipt_no} 已核销，不能重复核销"

    link["核销状态"] = "已核销"
    link["证据保留"] = True
    written_off = list(trace.get("核销回执编号", []))
    if receipt_no not in written_off:
        written_off.append(receipt_no)
    trace["核销回执编号"] = written_off
    trace["核销说明"] = _norm(remark) or "终态矛盾回执已在审计日志核销，证据留存"

    evidence = [row for row in store.rows(TRACE_RECEIPT_MODULE)
                if _norm(row.get("追查编号")) == _norm(trace.get("追查编号"))]
    if evidence and all(row.get("核销状态") == "已核销" for row in evidence):
        trace["status"] = TRACE_STATUS_WRITTEN_OFF
        trace["pending"] = False
    return trace, f"矛盾回执 {receipt_no} 已核销，证据已保留"


def suspicious_product_index(node_id: str | None = None) -> list[dict[str, Any]]:
    """分析管道侧读取的可疑产物索引（按实际产物引用生成，指纹去重）。"""
    all_receipts = store.rows(RECEIPT_MODULE)
    downstream_rows = store.rows(DOWNSTREAM_MODULE)
    node_ids = sorted({_norm(r.get("节点编号")) for r in all_receipts if _norm(r.get("节点编号"))})
    if node_id:
        node_ids = [value for value in node_ids if value == _norm(node_id)]
    products: list[dict[str, Any]] = []
    for value in node_ids:
        report = analyze_node(value, all_receipts, downstream_rows)
        if report["可疑"]:
            products.extend(report["产物索引"])
    return products


def rerun_plan(node_id: str | None = None) -> dict[str, Any]:
    """分析管道侧生成待复跑节点及受影响结果清单。"""
    all_receipts = store.rows(RECEIPT_MODULE)
    downstream_rows = store.rows(DOWNSTREAM_MODULE)
    node_ids = sorted({_norm(r.get("节点编号")) for r in all_receipts if _norm(r.get("节点编号"))})
    if node_id:
        node_ids = [value for value in node_ids if value == _norm(node_id)]

    rerun_nodes: list[dict[str, Any]] = []
    unaffected_nodes: list[str] = []
    result_map: dict[str, dict[str, Any]] = {}
    for value in node_ids:
        report = analyze_node(value, all_receipts, downstream_rows)
        if report["待复跑"]:
            rerun_nodes.append({
                "节点编号": value,
                "差错类型": sorted({issue["差错类型"] for issue in report["差错清单"]}),
                "涉及尝试编号": sorted({
                    attempt
                    for issue in report["差错清单"]
                    for attempt in issue.get("涉及尝试编号", [])
                }),
                "受影响结果编号": [item["下游结果编号"] for item in report["受影响结果"]],
            })
            for affected in report["受影响结果"]:
                result_map.setdefault(affected["下游结果编号"], dict(affected))
        else:
            unaffected_nodes.append(value)

    return {
        "待复跑节点": rerun_nodes,
        "未受影响节点": unaffected_nodes,
        "受影响结果清单": sorted(result_map.values(), key=lambda item: item["下游结果编号"]),
        "可疑产物索引": suspicious_product_index(node_id),
    }
