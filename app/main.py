from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

from .config import PROJECT_ROOT, Settings
from .llm import QwenClient
from .parsers import UnsupportedDocumentError, ocr_available, parse_document_with_metadata
from .reports import build_analysis_report
from .schemas import (
    AnalysisResult,
    AnalysisSummary,
    CandidateFeedback,
    CandidateFeedbackInput,
    FeedbackStats,
)
from .storage import AnalysisStore
from .workflow import HireWorkflow


logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
settings = Settings.from_env()
store = AnalysisStore(settings.database_path)
workflow = HireWorkflow(QwenClient(settings))

app = FastAPI(title="HireAgent", version="0.1.0")
app.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(PROJECT_ROOT / "static" / "index.html")


@app.get("/api/v1/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "HireAgent",
        "ai_enabled": settings.llm_enabled,
        "model": settings.model,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "llm_max_attempts": settings.llm_max_attempts,
        "llm_input_char_limit": settings.llm_input_char_limit,
        "dashscope_proxy_mode": settings.dashscope_proxy_mode,
        "max_file_mb": settings.max_file_mb,
        "max_resumes": 10,
        "ocr_available": ocr_available(),
        "memory_management": True,
        "feedback_flywheel": True,
    }


@app.get("/api/v1/diagnostics/qwen")
async def qwen_diagnostics() -> dict:
    client = QwenClient(settings)
    client.begin_run()
    payload = await run_in_threadpool(
        client.call_json,
        '只输出合法 JSON 对象，例如 {"ok": true}',
        '请输出 {"ok": true}',
        task="qwen_diagnostics",
    )
    trace = client.call_traces[0] if client.call_traces else None
    return {
        "status": "success" if payload else "failed",
        "ai_enabled": settings.llm_enabled,
        "model": settings.model,
        "has_key": bool(settings.api_key),
        "llm_input_char_limit": settings.llm_input_char_limit,
        "payload": payload,
        "model_call_status": trace.status if trace else "",
        "attempts": trace.attempts if trace else 0,
        "error": trace.error if trace else "",
    }


async def _read_upload(upload: UploadFile) -> tuple[str, bytes]:
    data = await upload.read()
    if len(data) > settings.max_file_mb * 1024 * 1024:
        raise HTTPException(413, f"{upload.filename} 超过 {settings.max_file_mb}MB")
    return upload.filename or "upload.txt", data


@app.post("/api/v1/analyze", response_model=AnalysisResult)
async def analyze(
    jd: UploadFile = File(...),
    resumes: list[UploadFile] = File(...),
) -> AnalysisResult:
    if not resumes:
        raise HTTPException(400, "请至少上传一份简历")
    if len(resumes) > 10:
        raise HTTPException(400, "一次最多上传 10 份简历，请分批分析")
    try:
        jd_name, jd_data = await _read_upload(jd)
        jd_document = parse_document_with_metadata(jd_name, jd_data)
        resume_payload = []
        warnings = [jd_document.warning] if jd_document.warning else []
        for resume in resumes:
            name, data = await _read_upload(resume)
            document = parse_document_with_metadata(name, data)
            resume_payload.append({"filename": name, "text": document.text})
            if document.warning:
                warnings.append(document.warning)
        feedback_stats = store.feedback_stats()
        result = await run_in_threadpool(
            workflow.run,
            jd_document.text,
            resume_payload,
            warnings,
            feedback_stats.reliability_guidance,
            feedback_stats.total_reviews,
        )
        store.save(result)
        return result
    except UnsupportedDocumentError as exc:
        raise HTTPException(415, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/v1/analyses", response_model=list[AnalysisSummary])
async def analyses() -> list[AnalysisSummary]:
    return store.list_recent()


@app.get("/api/v1/analyses/{analysis_id}", response_model=AnalysisResult)
async def get_analysis(analysis_id: str) -> AnalysisResult:
    result = store.get(analysis_id)
    if not result:
        raise HTTPException(404, "分析记录不存在")
    return result


@app.get("/api/v1/analyses/{analysis_id}/report", response_class=Response)
async def analysis_report(analysis_id: str) -> Response:
    result = store.get(analysis_id)
    if not result:
        raise HTTPException(404, "分析记录不存在")
    filename = f"hireagent-{analysis_id}-report.md"
    return Response(
        content=build_analysis_report(result),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.delete("/api/v1/analyses/{analysis_id}")
async def delete_analysis(analysis_id: str) -> dict:
    if not store.delete(analysis_id):
        raise HTTPException(404, "分析记录不存在")
    return {"deleted": True, "analysis_id": analysis_id}


@app.get("/api/v1/feedback/stats", response_model=FeedbackStats)
async def feedback_stats() -> FeedbackStats:
    return store.feedback_stats()


@app.get(
    "/api/v1/analyses/{analysis_id}/candidates/{candidate_id}/feedback",
    response_model=CandidateFeedback | None,
)
async def get_candidate_feedback(
    analysis_id: str,
    candidate_id: str,
) -> CandidateFeedback | None:
    return store.get_feedback(analysis_id, candidate_id)


@app.post(
    "/api/v1/analyses/{analysis_id}/candidates/{candidate_id}/feedback",
    response_model=CandidateFeedback,
)
async def save_candidate_feedback(
    analysis_id: str,
    candidate_id: str,
    feedback: CandidateFeedbackInput,
) -> CandidateFeedback:
    analysis = store.get(analysis_id)
    if not analysis:
        raise HTTPException(404, "分析记录不存在")
    try:
        return store.save_feedback(analysis, candidate_id, feedback)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)
