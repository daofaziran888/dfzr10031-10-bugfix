"""数据审计接口：维护审计记录，覆盖提交审计、标记异常等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.audit import AuditService

router = APIRouter(prefix="/api/audit", tags=["数据审计"])

service = AuditService()

LIST_FIELDS = ["记录编号", "操作类型", "操作人", "操作对象", "操作时间", "变更内容", "审计结果", "记录状态"]
STATUSES = ["已记录", "已审计", "异常"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出数据审计清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "audit", "total": total, "items": items}


@router.post("/receipt-traces", response_model=ActionResult)
def create_receipt_trace(payload: EntryPayload) -> ActionResult:
    """回执差错追查入口：接收审计操作编号、节点编号、尝试编号、输入批号与数据产物指纹。

    识别终态对不上、产物引用错位和成功回执丢失，固化待复跑节点、受影响结果与
    证据回执；缺字段时说明原因而不是静默丢弃。
    """
    trace, missing = service.create_receipt_trace(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    report = service.get_receipt_trace_report(int(trace["id"]))
    return ActionResult(ok=True, message="回执差错追查已登记，证据已留存", entry={"追查任务": trace, "核对报告": report})


@router.get("/receipt-traces", response_model=PageResult[dict])
def list_receipt_traces(
    keyword: str | None = Query(default=None, description="按追查编号或审计操作编号检索"),
    status: str | None = Query(default=None, description="待核销、已核销"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """回执差错追查任务列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_receipt_traces(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/receipt-traces/{trace_id}", response_model=dict)
def get_receipt_trace(trace_id: int) -> dict:
    """读取单次追查的完整核对报告与留存证据；不存在时给出可读的错误说明。"""
    report = service.get_receipt_trace_report(trace_id)
    if report is None:
        raise HTTPException(status_code=404, detail=f"回执差错追查任务 {trace_id} 不存在或已归档")
    return report


@router.post("/receipt-traces/{trace_id}/write-off", response_model=ActionResult)
def write_off_receipt(trace_id: int, payload: EntryPayload) -> ActionResult:
    """核销终态残留的矛盾回执：只改审计日志中的核销状态，回执证据与数据产物均保留。"""
    trace, message = service.write_off_receipt(trace_id, payload.values)
    if trace is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=trace)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="已记录、已审计、异常"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号与状态过滤数据审计列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条审计记录明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"审计记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条审计记录，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="审计记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条审计记录执行提交审计、标记异常；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
