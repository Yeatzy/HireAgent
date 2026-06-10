import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app.reports import build_analysis_report
from app.schemas import (
    AnalysisResult,
    CandidateAssessment,
    CandidateProfile,
    Evidence,
    InterviewQuestion,
    JobProfile,
    ScoreBreakdown,
    TraceEvent,
)
from app.storage import AnalysisStore


def sample_result() -> AnalysisResult:
    return AnalysisResult(
        analysis_id="analysis-report",
        job=JobProfile(
            title="AI 业务探索",
            responsibilities=["挖掘业务痛点", "搭建 Agent 原型"],
            required_skills=["Prompt", "Agent"],
            preferred_skills=["B2B"],
        ),
        candidates=[
            CandidateAssessment(
                profile=CandidateProfile(
                    candidate_id="candidate-1",
                    source_file="candidate.txt",
                    name="林晓",
                    years_experience=3,
                    education="本科",
                    skills=["Prompt", "Agent"],
                    risks=["需要验证 B2B 场景深度"],
                    evidence=[
                        Evidence(
                            field="技能:Agent",
                            snippet="独立搭建客服 Agent 原型并用于业务验证",
                        )
                    ],
                ),
                score=86,
                recommendation="建议推进",
                breakdown=ScoreBreakdown(
                    skills=42,
                    experience=18,
                    education=10,
                    achievements=8,
                    evidence_quality=8,
                ),
                matched_requirements=["Prompt", "Agent"],
                missing_requirements=["B2B"],
                reasons=["匹配 2 项核心要求：Prompt、Agent"],
                interview_questions=[
                    InterviewQuestion(
                        question="请拆解一个 Agent 原型从需求到上线的过程。",
                        focus="Agent 实战深度",
                        difficulty="进阶",
                        scoring_criteria=["目标清晰", "能说明验证方式"],
                    )
                ],
                follow_up_questions=["请补充 B2B 业务场景中的落地约束。"],
            )
        ],
        trace=[TraceEvent(stage="score", message="已完成可解释匹配评分")],
    )


class ReportTests(unittest.TestCase):
    def test_report_contains_decision_package(self):
        report = build_analysis_report(sample_result())

        self.assertIn("# HireAgent 招聘评估报告：AI 业务探索", report)
        self.assertIn("| 1 | 林晓 | 86 | 建议推进 | B2B |", report)
        self.assertIn("技能:Agent：独立搭建客服 Agent 原型并用于业务验证", report)
        self.assertIn("请拆解一个 Agent 原型从需求到上线的过程", report)
        self.assertIn("质量检查项", report)
        self.assertIn("score：已完成可解释匹配评分", report)

    def test_report_endpoint_downloads_markdown(self):
        original_store = main.store
        try:
            with tempfile.TemporaryDirectory() as directory:
                main.store = AnalysisStore(Path(directory) / "api.db")
                main.store.save(sample_result())
                client = TestClient(main.app)

                response = client.get("/api/v1/analyses/analysis-report/report")

                self.assertEqual(response.status_code, 200)
                self.assertIn("text/markdown", response.headers["content-type"])
                self.assertIn(
                    'filename="hireagent-analysis-report-report.md"',
                    response.headers["content-disposition"],
                )
                self.assertIn("HireAgent 招聘评估报告", response.text)
        finally:
            main.store = original_store


if __name__ == "__main__":
    unittest.main()
