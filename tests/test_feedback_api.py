import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings
from app.schemas import (
    AnalysisResult,
    CandidateAssessment,
    CandidateProfile,
    JobProfile,
    ScoreBreakdown,
)
from app.storage import AnalysisStore


class FeedbackApiTests(unittest.TestCase):
    def test_qwen_diagnostics_can_run_without_key(self):
        original_settings = main.settings
        try:
            main.settings = Settings(ai_mode="off", api_key="")
            client = TestClient(main.app)

            response = client.get("/api/v1/diagnostics/qwen")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["status"], "failed")
            self.assertFalse(payload["ai_enabled"])
            self.assertFalse(payload["has_key"])
            self.assertEqual(payload["model_call_status"], "skipped")
        finally:
            main.settings = original_settings

    def test_health_exposes_runtime_limits(self):
        client = TestClient(main.app)

        response = client.get("/api/v1/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["max_resumes"], 10)
        self.assertIn("ocr_available", payload)
        self.assertIn("dashscope_proxy_mode", payload)

    def test_analyze_rejects_more_than_ten_resumes(self):
        client = TestClient(main.app)
        files = [
            ("jd", ("jd.txt", b"AI business role", "text/plain")),
            *[
                ("resumes", (f"resume-{index}.txt", b"Python Agent RAG", "text/plain"))
                for index in range(11)
            ],
        ]

        response = client.post("/api/v1/analyze", files=files)

        self.assertEqual(response.status_code, 400)
        self.assertIn("最多上传 10 份", response.json()["detail"])

    def test_feedback_round_trip_and_stats(self):
        original_store = main.store
        try:
            with tempfile.TemporaryDirectory() as directory:
                main.store = AnalysisStore(Path(directory) / "api.db")
                main.store.save(
                    AnalysisResult(
                        analysis_id="analysis-1",
                        job=JobProfile(title="战略分析师"),
                        candidates=[
                            CandidateAssessment(
                                profile=CandidateProfile(
                                    candidate_id="candidate-1",
                                    source_file="candidate.txt",
                                    name="候选人",
                                ),
                                score=72,
                                recommendation="谨慎推进",
                                breakdown=ScoreBreakdown(),
                            )
                        ],
                        trace=[],
                    )
                )
                client = TestClient(main.app)

                response = client.post(
                    "/api/v1/analyses/analysis-1/candidates/candidate-1/feedback",
                    json={
                        "review_status": "partially_accurate",
                        "human_recommendation": "建议推进",
                        "issue_types": ["missed_skill", "score_too_low"],
                        "notes": "遗漏了一段项目经历。",
                    },
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["candidate_name"], "候选人")

                loaded = client.get(
                    "/api/v1/analyses/analysis-1/candidates/candidate-1/feedback"
                )
                self.assertEqual(loaded.status_code, 200)
                self.assertEqual(loaded.json()["issue_types"], ["missed_skill", "score_too_low"])

                stats = client.get("/api/v1/feedback/stats")
                self.assertEqual(stats.status_code, 200)
                self.assertEqual(stats.json()["total_reviews"], 1)
                self.assertTrue(stats.json()["reliability_guidance"])
        finally:
            main.store = original_store


if __name__ == "__main__":
    unittest.main()
