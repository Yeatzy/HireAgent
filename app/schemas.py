from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Evidence(BaseModel):
    field: str
    snippet: str


class JobProfile(BaseModel):
    title: str = "未命名岗位"
    responsibilities: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    preferred_skills: list[str] = Field(default_factory=list)
    minimum_years: float = 0
    education: str = ""
    keywords: list[str] = Field(default_factory=list)


class CandidateProfile(BaseModel):
    candidate_id: str
    source_file: str
    name: str
    years_experience: float = 0
    education: str = ""
    skills: list[str] = Field(default_factory=list)
    experience_highlights: list[str] = Field(default_factory=list)
    achievements: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    skills: int = 0
    experience: int = 0
    education: int = 0
    achievements: int = 0
    evidence_quality: int = 0


class InterviewQuestion(BaseModel):
    question: str
    focus: str
    difficulty: Literal["基础", "进阶", "挑战"] = "进阶"
    scoring_criteria: list[str] = Field(default_factory=list)


class CandidateAssessment(BaseModel):
    profile: CandidateProfile
    score: int
    recommendation: Literal["建议推进", "谨慎推进", "暂不推进"]
    breakdown: ScoreBreakdown
    matched_requirements: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    interview_questions: list[InterviewQuestion] = Field(default_factory=list)
    follow_up_questions: list[str] = Field(default_factory=list)


class TraceEvent(BaseModel):
    stage: str
    message: str


class ModelCallTrace(BaseModel):
    task: str
    status: Literal["success", "fallback", "skipped"]
    attempts: int = 0
    error: str = ""


class QualityCheck(BaseModel):
    name: str
    status: Literal["pass", "warn", "fail"]
    message: str


class QualityAudit(BaseModel):
    passed: bool = True
    summary: str = "质量审计尚未运行"
    checks: list[QualityCheck] = Field(default_factory=list)


FeedbackIssue = Literal[
    "hallucinated_skill",
    "missed_skill",
    "years_error",
    "education_error",
    "achievement_error",
    "score_too_high",
    "score_too_low",
    "question_hallucination",
    "ocr_error",
    "other",
]


class CandidateFeedbackInput(BaseModel):
    review_status: Literal["accurate", "partially_accurate", "inaccurate"]
    human_recommendation: Literal["建议推进", "谨慎推进", "暂不推进"] | None = None
    issue_types: list[FeedbackIssue] = Field(default_factory=list, max_length=10)
    notes: str = Field(default="", max_length=1000)


class CandidateFeedback(BaseModel):
    analysis_id: str
    candidate_id: str
    job_title: str
    candidate_name: str
    system_score: int
    system_recommendation: str
    review_status: str
    human_recommendation: str | None = None
    issue_types: list[FeedbackIssue] = Field(default_factory=list)
    notes: str = ""
    created_at: str
    updated_at: str


class FeedbackStats(BaseModel):
    total_reviews: int = 0
    inaccurate_reviews: int = 0
    agreement_rate: float = 0
    issue_counts: dict[str, int] = Field(default_factory=dict)
    reliability_guidance: list[str] = Field(default_factory=list)
    policy_version: str = "feedback-v1"


class AnalysisResult(BaseModel):
    analysis_id: str
    job: JobProfile
    candidates: list[CandidateAssessment]
    trace: list[TraceEvent]
    warnings: list[str] = Field(default_factory=list)
    model_calls: list[ModelCallTrace] = Field(default_factory=list)
    ai_enhanced: bool = False
    feedback_memory_used: int = 0
    feedback_policy_version: str = "feedback-v1"
    quality_audit: QualityAudit = Field(default_factory=QualityAudit)


class AnalysisSummary(BaseModel):
    analysis_id: str
    job_title: str
    candidate_count: int
    created_at: str
