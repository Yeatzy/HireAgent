import unittest
from pathlib import Path

from app.config import Settings
from app.llm import QwenClient
from app.workflow import HireWorkflow


ROOT = Path(__file__).resolve().parents[1]


class WorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = HireWorkflow(QwenClient(Settings(ai_mode="off")))
        cls.jd = (ROOT / "samples" / "jd_ai_business.txt").read_text(encoding="utf-8")
        cls.resumes = [
            {"filename": path.name, "text": path.read_text(encoding="utf-8")}
            for path in sorted((ROOT / "samples" / "resumes").glob("*.txt"))
        ]

    def test_workflow_is_complete(self):
        result = self.workflow.run(self.jd, self.resumes)

        self.assertEqual(len(result.candidates), 3)
        self.assertEqual(
            [event.stage for event in result.trace],
            [
                "load_feedback_memory",
                "parse_job",
                "parse_candidates",
                "validate_candidates",
                "score",
                "questions",
                "reflect",
                "quality_audit",
            ],
        )
        self.assertFalse(result.ai_enhanced)
        for candidate in result.candidates:
            self.assertGreaterEqual(len(candidate.interview_questions), 10)
            self.assertGreaterEqual(len(candidate.follow_up_questions), 3)
            self.assertLessEqual(len(candidate.follow_up_questions), 5)
        self.assertTrue(result.quality_audit.passed)
        self.assertIn("质量审计完成", result.trace[-1].message)
        self.assertTrue(result.quality_audit.checks)

    def test_negative_experience_is_not_treated_as_skill(self):
        result = self.workflow.run(self.jd, self.resumes)
        candidate = next(item for item in result.candidates if item.profile.name == "周然")

        self.assertNotIn("Python", candidate.profile.skills)
        self.assertNotIn("Agent", candidate.profile.skills)

    def test_candidates_are_sorted_by_score(self):
        result = self.workflow.run(self.jd, self.resumes)
        scores = [candidate.score for candidate in result.candidates]

        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_reflection_removes_unsupported_match_and_recalculates_score(self):
        from app.schemas import (
            CandidateAssessment,
            CandidateProfile,
            InterviewQuestion,
            JobProfile,
            ScoreBreakdown,
        )

        profile = CandidateProfile(
            candidate_id="candidate-risk",
            source_file="risk.txt",
            name="测试候选人",
            skills=["Python"],
            evidence=[],
        )
        assessment = CandidateAssessment(
            profile=profile,
            score=90,
            recommendation="建议推进",
            breakdown=ScoreBreakdown(skills=40),
            matched_requirements=["Python"],
            interview_questions=[
                InterviewQuestion(
                    question=f"问题 {index}",
                    focus="验证",
                    scoring_criteria=["事实", "证据"],
                )
                for index in range(10)
            ],
            follow_up_questions=["追问一", "追问二", "追问三"],
        )

        reflected = self.workflow._reflect(
            {
                "job": JobProfile(title="测试岗", required_skills=["Python"]),
                "assessments": [assessment],
                "warnings": [],
                "trace": [],
            }
        )
        result = reflected["assessments"][0]

        self.assertEqual(result.matched_requirements, [])
        self.assertNotIn("Python", result.profile.skills)
        self.assertLess(result.score, 90)

    def test_feedback_memory_is_visible_in_trace_and_result(self):
        result = self.workflow.run(
            self.jd,
            self.resumes[:1],
            reliability_guidance=["未知信息采用保守判断。"],
            feedback_memory_count=8,
        )

        self.assertEqual(result.feedback_memory_used, 8)
        self.assertIn(
            "已加载 8 条人工复核记录",
            result.trace[0].message,
        )


if __name__ == "__main__":
    unittest.main()
