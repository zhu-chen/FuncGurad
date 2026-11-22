"""FastAPI 应用：单条运行、批量提交、报告与轨迹查询。"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.api.job_service import JobError, JobService
from backend.config import AgentSettings
from backend.providers.base import ProviderError
from backend.services.sample_service import SampleServiceError
from repo_service.config import REPO_ROOT


def _ok(data: Any, status_code: int = 200, ok: bool = True) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"ok": ok, "data": data, "error": None},
    )


def _error(status_code: int, message: str, code: str | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "ok": False,
            "data": None,
            "error": {"code": code or str(status_code), "message": message},
        },
    )


class RunRequest(BaseModel):
    idx: int = Field(..., ge=0, description="测试集样本 idx")
    pair: bool = Field(True, description="是否自动补齐相邻配对样本以计算 P-x")


class BatchRequest(BaseModel):
    idx_list: list[int] = Field(..., min_length=1, max_length=100)
    pair: bool = Field(True, description="每条 idx 是否补齐配对样本")


def create_app() -> FastAPI:
    settings = AgentSettings.from_env()
    dataset_path = Path(
        os.getenv("FUNCGUARD_DATASET", str(settings.cache_dir / "dataset" / "test.jsonl"))
    ).resolve()
    jobs = JobService(settings, dataset_path)

    data_state = jobs.data_status()
    if not data_state["ready"]:
        print(
            "!! FuncGurad 警告: 数据未就绪（"
            + "、".join(data_state["missing"])
            + "），界面可打开，但运行会返回 503；准备方式见 README「运行前准备」。"
        )

    app = FastAPI(title="FuncGurad P5", version="0.1.0")
    app.state.jobs = jobs
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://127.0.0.1:8000",
            "http://localhost:8000",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(JobError)
    def handle_job_error(request: Request, exc: JobError) -> JSONResponse:
        return _error(exc.status_code, str(exc))

    @app.exception_handler(ProviderError)
    def handle_provider_error(request: Request, exc: ProviderError) -> JSONResponse:
        return _error(503, f"模型 provider 不可用：{exc}", "provider_unavailable")

    @app.exception_handler(SampleServiceError)
    def handle_sample_error(
        request: Request, exc: SampleServiceError
    ) -> JSONResponse:
        # 数据未就绪属于服务端依赖缺失：服务在线但暂时无法完成请求，用 503。
        return _error(503, str(exc), "data_not_ready")

    @app.exception_handler(RequestValidationError)
    def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(item) for item in first.get("loc", []))
        message = f"请求参数不合法：{location} {first.get('msg', '')}".strip()
        return _error(422, message, "validation_error")

    @app.exception_handler(StarletteHTTPException)
    def handle_http_error(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        return _error(exc.status_code, str(exc.detail))

    @app.get("/api/health")
    def health() -> JSONResponse:
        data_state = jobs.data_status()
        if not data_state["ready"]:
            return _ok(
                {
                    "status": "degraded",
                    "provider": jobs.provider_info(),
                    "missing": data_state["missing"],
                    "message": data_state["message"],
                    "runs_dir": str(settings.runs_dir),
                },
                ok=False,
            )
        return _ok(
            {
                "status": "ok",
                "provider": jobs.provider_info(),
                "dataset": jobs.dataset_stats(),
                "runs_dir": str(settings.runs_dir),
            }
        )

    @app.get("/api/pairs")
    def list_pairs(limit: int = Query(30, ge=1, le=200)) -> JSONResponse:
        return _ok({"pairs": jobs.list_pairs(limit=limit)})

    @app.get("/api/runs")
    def list_runs(limit: int = Query(20, ge=1, le=100)) -> JSONResponse:
        return _ok({"runs": jobs.list_runs(limit=limit)})

    @app.post("/api/runs")
    def create_run(payload: RunRequest) -> JSONResponse:
        return _ok(jobs.create_run(payload.idx, payload.pair), status_code=202)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str) -> JSONResponse:
        return _ok(jobs.get_run(run_id))

    @app.get("/api/runs/{run_id}/report")
    def get_report(run_id: str) -> JSONResponse:
        return _ok(jobs.read_report(run_id))

    @app.get("/api/runs/{run_id}/trace")
    def get_trace(
        run_id: str,
        after: int = Query(0, ge=0, description="已收到的事件条数"),
    ) -> JSONResponse:
        return _ok(jobs.read_trace(run_id, after=after))

    @app.post("/api/batches")
    def create_batch(payload: BatchRequest) -> JSONResponse:
        return _ok(
            jobs.create_batch(payload.idx_list, payload.pair), status_code=202
        )

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str) -> JSONResponse:
        return _ok(jobs.get_batch(batch_id))

    dist_dir = REPO_ROOT / "web" / "dist"
    assets_dir = dist_dir / "assets"
    index_file = dist_dir / "index.html"

    if assets_dir.is_dir():
        from fastapi.staticfiles import StaticFiles

        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/")
    def index() -> Any:
        if index_file.exists():
            return FileResponse(str(index_file))
        return HTMLResponse(
            status_code=503,
            content=(
                "<h1>前端尚未构建</h1>"
                "<p>请先在 web/ 目录执行 <code>npm install</code> 和 "
                "<code>npm run build</code>，或直接使用仓库中已提交的 dist。</p>"
            ),
        )

    return app


app = create_app()
