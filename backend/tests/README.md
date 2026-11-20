# 回执差错追查测试

纯标准库 + FastAPI TestClient，无额外测试框架依赖。

在 `backend/` 目录下直接以脚本方式运行（测试会自行把后端源码目录加入
`sys.path`，兼容无 venv、依赖装在独立目录的环境）：

```bash
python3 tests/test_receipt_trace.py   # 核心核对规则，8 个用例
python3 tests/test_receipt_api.py     # HTTP 接口集成，4 个用例
```

若运行环境没有安装 fastapi/httpx，先让解释器能找到依赖目录，例如：

```bash
PYTHONPATH=/path/to/site-packages python3 tests/test_receipt_api.py
```
