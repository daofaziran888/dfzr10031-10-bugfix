"""数据审计业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import receipt_trace
from app.store import store

MODULE = "audit"
REQUIRED_FIELDS = ["记录编号", "操作类型", "操作人"]
STATUS_ORDER = ["已记录", "已审计", "异常"]
ACTION_RULES = {"提交审计": "已审计", "标记异常": "异常"}
NEGATIVE_ACTIONS = []


class AuditService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
        extra: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if extra:
            for field, val in extra.items():
                rows = [row for row in rows if val in str(row.get(field, ""))]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"审计记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据审计可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"审计记录已{action}"

    # ---- 回执差错追查 ----

    def create_receipt_trace(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        """登记回执差错追查：接收五个定位字段，固化核对结论与证据。"""
        return receipt_trace.create_trace(values)

    def list_receipt_traces(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        return receipt_trace.list_traces(keyword=keyword, status=status, page=page, size=size)

    def get_receipt_trace(self, trace_id: int) -> dict[str, Any] | None:
        return receipt_trace.get_trace(trace_id)

    def get_receipt_trace_report(self, trace_id: int) -> dict[str, Any] | None:
        """按追查任务登记时的范围重新核对，返回完整报告。"""
        trace = receipt_trace.get_trace(trace_id)
        if trace is None:
            return None
        scope = {field: trace.get(field, "") for field in receipt_trace.SCOPE_FIELDS}
        report = receipt_trace.build_trace_report(scope)
        report["追查任务"] = {
            "追查编号": trace.get("追查编号"),
            "status": trace.get("status"),
            "核销回执编号": trace.get("核销回执编号", []),
            "核销说明": trace.get("核销说明", ""),
        }
        report["留存证据"] = receipt_trace.get_trace_evidence(str(trace.get("追查编号")))
        return report

    def write_off_receipt(self, trace_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        """核销终态残留的矛盾回执，证据留存不删除。"""
        return receipt_trace.write_off(
            trace_id,
            str(values.get("回执编号") or ""),
            str(values.get("remark") or values.get("核销说明") or ""),
        )
