"""分析管道业务规则：状态流转、字段校验与筛选口径都收在这里。"""
from __future__ import annotations

from typing import Any

from app.services import receipt_trace
from app.store import store

MODULE = "pipeline"
REQUIRED_FIELDS = ["节点编号", "输入数据集", "分析算子"]
STATUS_ORDER = ["草稿", "已配置", "运行中", "已完成", "失败"]
ACTION_RULES = {"提交配置": "已配置", "启动节点": "运行中", "标记失败": "失败"}
NEGATIVE_ACTIONS = []


class PipelineService:
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
            rows = [row for row in rows if keyword in str(row.get("节点编号", ""))]
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
            return None, f"分析节点 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于分析管道可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"分析节点已{action}"

    # ---- 回执差错追查：可疑产物索引与复跑清单 ----

    def suspicious_products(self, node_id: str | None = None) -> list[dict[str, Any]]:
        """读取节点可疑产物索引，按实际产物引用生成、按指纹去重。"""
        return receipt_trace.suspicious_product_index(node_id)

    def rerun_plan(self, node_id: str | None = None) -> dict[str, Any]:
        """生成待复跑节点及受影响结果清单，未受影响节点单列。"""
        return receipt_trace.rerun_plan(node_id)
