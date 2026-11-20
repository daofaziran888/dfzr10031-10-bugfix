"""分析管道接口：维护分析节点，覆盖提交配置、启动节点、标记失败等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.pipeline import PipelineService

router = APIRouter(prefix="/api/pipeline", tags=["分析管道"])

service = PipelineService()

LIST_FIELDS = ["节点编号", "输入数据集", "分析算子", "输出结果", "执行顺序", "依赖节点", "失败策略", "节点状态"]
STATUSES = ["草稿", "已配置", "运行中", "已完成", "失败"]


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出分析管道清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "pipeline", "total": total, "items": items}


@router.get("/receipts/suspicious-products", response_model=dict)
def suspicious_products(
    node_id: str | None = Query(default=None, description="按节点编号过滤可疑产物索引"),
) -> dict[str, Any]:
    """读取节点可疑产物索引：按实际产物引用生成，同一数据产物指纹只计一次。"""
    items = service.suspicious_products(node_id)
    return {"module": "pipeline", "total": len(items), "items": items}


@router.get("/receipts/rerun-plan", response_model=dict)
def rerun_plan(
    node_id: str | None = Query(default=None, description="按节点编号过滤复跑清单"),
) -> dict[str, Any]:
    """按实际产物引用生成待复跑节点及受影响结果清单，并单列未受影响节点。"""
    return service.rerun_plan(node_id)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按节点编号检索"),
    status: str | None = Query(default=None, description="草稿、已配置、运行中、已完成、失败"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按节点编号与状态过滤分析管道列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    extra = {k: v for k, v in request.query_params.items() if k not in ("keyword", "status", "page", "size") and v.strip()}
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size, extra=extra)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条分析节点明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"分析节点 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条分析节点，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="分析节点已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条分析节点执行提交配置、启动节点、标记失败；不允许的动作会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
