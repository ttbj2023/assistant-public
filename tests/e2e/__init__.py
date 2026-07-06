"""E2E测试模块 - Personal Agent Assistant

基于ASGI TestClient的端到端测试框架:
- 进程内直接调用FastAPI app, 无子进程
- Mock LLM + 每测试独立user/thread数据隔离
- 支持pytest-xdist并行执行
"""
