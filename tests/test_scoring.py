import unittest

from app.schemas import CandidateProfile, Evidence, JobProfile
from app.scoring import assess_candidate
from app.audit import build_quality_audit


class ScoringTests(unittest.TestCase):
    def test_strong_candidate_is_ranked_high(self):
        job = JobProfile(
            title="AI 业务探索",
            required_skills=["Python", "Agent", "RAG", "数据分析"],
            preferred_skills=["B2B"],
            minimum_years=1,
            education="本科",
        )
        candidate = CandidateProfile(
            candidate_id="c1",
            source_file="candidate.txt",
            name="测试候选人",
            years_experience=3,
            education="硕士",
            skills=["Python", "Agent", "RAG", "数据分析", "B2B"],
            achievements=["将处理时间降低 60%"],
            evidence=[
                Evidence(field="技能:Python", snippet="使用 Python 开发自动化脚本"),
                Evidence(field="技能:Agent", snippet="搭建 Agent 工作流"),
                Evidence(field="技能:RAG", snippet="构建 RAG 知识库"),
            ],
        )

        result = assess_candidate(job, candidate)

        self.assertGreaterEqual(result.score, 75)
        self.assertEqual(result.recommendation, "建议推进")
        self.assertFalse(result.missing_requirements)

    def test_missing_skills_are_exposed(self):
        job = JobProfile(title="AI 业务探索", required_skills=["Python", "Agent", "RAG"])
        candidate = CandidateProfile(
            candidate_id="c2",
            source_file="candidate.txt",
            name="测试候选人",
            skills=["Python"],
        )

        result = assess_candidate(job, candidate)

        self.assertEqual(result.missing_requirements, ["Agent", "RAG"])
        self.assertLess(result.score, 75)

    def test_verbose_requirements_match_canonical_candidate_skills(self):
        job = JobProfile(
            title="AI 应用岗",
            required_skills=["会搭建 AI Agent 应用", "熟悉 RAG 知识库"],
        )
        candidate = CandidateProfile(
            candidate_id="c3",
            source_file="candidate.txt",
            name="测试候选人",
            skills=["Agent", "RAG"],
            evidence=[
                Evidence(field="技能:Agent", snippet="搭建 Agent 工作流"),
                Evidence(field="技能:RAG", snippet="构建 RAG 知识库"),
            ],
        )

        result = assess_candidate(job, candidate)

        self.assertEqual(result.matched_requirements, ["会搭建 AI Agent 应用", "熟悉 RAG 知识库"])
        self.assertFalse(result.missing_requirements)

    def test_audit_accepts_alias_matched_evidence(self):
        job = JobProfile(
            title="AI 应用岗",
            required_skills=["会搭建 AI Agent 应用"],
        )
        candidate = CandidateProfile(
            candidate_id="c4",
            source_file="candidate.txt",
            name="测试候选人",
            skills=["Agent"],
            evidence=[Evidence(field="技能:Agent", snippet="搭建 Agent 工作流")],
        )

        assessment = assess_candidate(job, candidate)
        audit = build_quality_audit([assessment], [], [])
        evidence_check = next(item for item in audit.checks if item.name == "证据一致性")

        self.assertEqual(evidence_check.status, "pass")


if __name__ == "__main__":
    unittest.main()
