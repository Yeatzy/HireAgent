from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from .schemas import (
    AnalysisResult,
    AnalysisSummary,
    CandidateFeedback,
    CandidateFeedbackInput,
    FeedbackStats,
)
from .text_hygiene import normalize_payload_text


FEEDBACK_GUIDANCE = {
    "hallucinated_skill": "技能必须存在正向原文证据；工具名称出现在否定、课程或目标描述中不得计为已掌握。",
    "missed_skill": "技能抽取需检查别名、英文缩写和项目描述，避免只扫描技能清单。",
    "years_error": "工作年限优先按去重后的任职日期区间计算，不得把教育年限或项目持续时间计入。",
    "education_error": "学历只读取明确的教育背景，不得根据学校名称或课程推断学历层级。",
    "achievement_error": "成果必须保留原文数字和因果边界，不得把团队结果改写为个人结果。",
    "score_too_high": "遇到信息缺失时采用保守评分，未知不等于满足要求。",
    "score_too_low": "检查是否遗漏可定位证据；只在找到原文支持后恢复对应分值。",
    "question_hallucination": "面试题不得把缺失能力写成既成事实，应改为条件式验证问题。",
    "ocr_error": "OCR 文本存在异常字符或结构断裂时降低置信度，并提示人工核验。",
    "other": "对人工标记的异常保持保守判断，无法验证时输出风险而非确定结论。",
}


class AnalysisStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._setup()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self) -> None:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS analyses (
                        analysis_id TEXT PRIMARY KEY,
                        job_title TEXT NOT NULL,
                        candidate_count INTEGER NOT NULL,
                        result_json TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS candidate_feedback (
                        analysis_id TEXT NOT NULL,
                        candidate_id TEXT NOT NULL,
                        job_title TEXT NOT NULL,
                        candidate_name TEXT NOT NULL,
                        system_score INTEGER NOT NULL,
                        system_recommendation TEXT NOT NULL,
                        review_status TEXT NOT NULL,
                        human_recommendation TEXT,
                        issue_types_json TEXT NOT NULL,
                        notes TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (analysis_id, candidate_id),
                        FOREIGN KEY (analysis_id) REFERENCES analyses(analysis_id)
                    )
                    """
                )

    def save(self, result: AnalysisResult) -> None:
        created_at = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO analyses
                    (analysis_id, job_title, candidate_count, result_json, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        result.analysis_id,
                        result.job.title,
                        len(result.candidates),
                        result.model_dump_json(),
                        created_at,
                    ),
                )

    def list_recent(self, limit: int = 50) -> list[AnalysisSummary]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT analysis_id, job_title, candidate_count, created_at
                FROM analyses ORDER BY created_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [AnalysisSummary.model_validate(dict(row)) for row in rows]

    def get(self, analysis_id: str) -> AnalysisResult | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT result_json FROM analyses WHERE analysis_id = ?",
                (analysis_id,),
            ).fetchone()
        if not row:
            return None
        return AnalysisResult.model_validate(
            normalize_payload_text(json.loads(row["result_json"]))
        )

    def delete(self, analysis_id: str) -> bool:
        with closing(self._connect()) as connection:
            with connection:
                connection.execute(
                    "DELETE FROM candidate_feedback WHERE analysis_id = ?",
                    (analysis_id,),
                )
                cursor = connection.execute(
                    "DELETE FROM analyses WHERE analysis_id = ?",
                    (analysis_id,),
                )
        return cursor.rowcount > 0

    def save_feedback(
        self,
        analysis: AnalysisResult,
        candidate_id: str,
        feedback: CandidateFeedbackInput,
    ) -> CandidateFeedback:
        candidate = next(
            (
                item
                for item in analysis.candidates
                if item.profile.candidate_id == candidate_id
            ),
            None,
        )
        if candidate is None:
            raise ValueError("候选人不存在")
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            existing = connection.execute(
                """
                SELECT created_at FROM candidate_feedback
                WHERE analysis_id = ? AND candidate_id = ?
                """,
                (analysis.analysis_id, candidate_id),
            ).fetchone()
            created_at = existing["created_at"] if existing else now
            with connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO candidate_feedback (
                        analysis_id, candidate_id, job_title, candidate_name,
                        system_score, system_recommendation, review_status,
                        human_recommendation, issue_types_json, notes,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        analysis.analysis_id,
                        candidate_id,
                        analysis.job.title,
                        candidate.profile.name,
                        candidate.score,
                        candidate.recommendation,
                        feedback.review_status,
                        feedback.human_recommendation,
                        json.dumps(feedback.issue_types, ensure_ascii=False),
                        feedback.notes.strip(),
                        created_at,
                        now,
                    ),
                )
        return self.get_feedback(analysis.analysis_id, candidate_id)

    def get_feedback(
        self,
        analysis_id: str,
        candidate_id: str,
    ) -> CandidateFeedback | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM candidate_feedback
                WHERE analysis_id = ? AND candidate_id = ?
                """,
                (analysis_id, candidate_id),
            ).fetchone()
        if not row:
            return None
        payload = dict(row)
        payload["issue_types"] = json.loads(payload.pop("issue_types_json"))
        payload = normalize_payload_text(payload)
        return CandidateFeedback.model_validate(payload)

    def feedback_stats(self, limit: int = 200) -> FeedbackStats:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT review_status, system_recommendation,
                       human_recommendation, issue_types_json
                FROM candidate_feedback
                ORDER BY updated_at DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        issue_counts: dict[str, int] = {}
        agreements = 0
        comparable = 0
        inaccurate = 0
        for row in rows:
            if row["review_status"] == "inaccurate":
                inaccurate += 1
            if row["human_recommendation"]:
                comparable += 1
                if row["human_recommendation"] == row["system_recommendation"]:
                    agreements += 1
            for issue in json.loads(row["issue_types_json"]):
                issue_counts[issue] = issue_counts.get(issue, 0) + 1
        ranked_issues = sorted(
            issue_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
        guidance = [
            FEEDBACK_GUIDANCE[issue]
            for issue, _count in ranked_issues[:5]
            if issue in FEEDBACK_GUIDANCE
        ]
        return FeedbackStats(
            total_reviews=len(rows),
            inaccurate_reviews=inaccurate,
            agreement_rate=round(agreements / comparable * 100, 1) if comparable else 0,
            issue_counts=issue_counts,
            reliability_guidance=guidance,
        )
