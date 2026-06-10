from __future__ import annotations

import uuid
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from .audit import build_quality_audit
from .extraction import extract_candidate_profile, extract_job_profile
from .llm import QwenClient
from .questions import generate_questions
from .schemas import (
    AnalysisResult,
    CandidateAssessment,
    CandidateProfile,
    JobProfile,
    TraceEvent,
)
from .scoring import assess_candidate, skill_keys


class HireState(TypedDict, total=False):
    analysis_id: str
    jd_text: str
    resumes: list[dict[str, str]]
    job: JobProfile
    profiles: list[CandidateProfile]
    assessments: list[CandidateAssessment]
    trace: list[TraceEvent]
    warnings: list[str]
    reliability_guidance: list[str]
    feedback_memory_count: int


class HireWorkflow:
    def __init__(self, llm: QwenClient):
        self.llm = llm
        graph = StateGraph(HireState)
        graph.add_node("load_feedback_memory", self._load_feedback_memory)
        graph.add_node("parse_job", self._parse_job)
        graph.add_node("parse_candidates", self._parse_candidates)
        graph.add_node("validate_candidates", self._validate_candidates)
        graph.add_node("score", self._score)
        graph.add_node("questions", self._questions)
        graph.add_node("reflect", self._reflect)
        graph.add_edge(START, "load_feedback_memory")
        graph.add_edge("load_feedback_memory", "parse_job")
        graph.add_edge("parse_job", "parse_candidates")
        graph.add_edge("parse_candidates", "validate_candidates")
        graph.add_edge("validate_candidates", "score")
        graph.add_edge("score", "questions")
        graph.add_edge("questions", "reflect")
        graph.add_edge("reflect", END)
        self.app = graph.compile()

    @staticmethod
    def _event(state: HireState, stage: str, message: str) -> list[TraceEvent]:
        return [*state.get("trace", []), TraceEvent(stage=stage, message=message)]

    def _load_feedback_memory(self, state: HireState) -> HireState:
        count = state.get("feedback_memory_count", 0)
        message = (
            f"已加载 {count} 条人工复核记录形成可靠性策略"
            if count
            else "暂无人工复核经验，使用基础可靠性策略"
        )
        return {
            "trace": self._event(state, "load_feedback_memory", message),
        }

    def _parse_job(self, state: HireState) -> HireState:
        job = extract_job_profile(
            state["jd_text"],
            self.llm,
            state.get("reliability_guidance", []),
        )
        return {
            "job": job,
            "trace": self._event(state, "parse_job", f"已解析岗位：{job.title}"),
        }

    def _parse_candidates(self, state: HireState) -> HireState:
        profiles = [
            extract_candidate_profile(
                candidate_id=f"candidate-{index}",
                filename=item["filename"],
                text=item["text"],
                llm=self.llm,
                reliability_guidance=state.get("reliability_guidance", []),
            )
            for index, item in enumerate(state["resumes"], 1)
        ]
        return {
            "profiles": profiles,
            "trace": self._event(state, "parse_candidates", f"已结构化解析 {len(profiles)} 份简历"),
        }

    def _score(self, state: HireState) -> HireState:
        assessments = [assess_candidate(state["job"], profile) for profile in state["profiles"]]
        assessments.sort(key=lambda item: item.score, reverse=True)
        return {
            "assessments": assessments,
            "trace": self._event(state, "score", "已完成可解释匹配评分与候选人排序"),
        }

    def _validate_candidates(self, state: HireState) -> HireState:
        profiles = []
        warnings = list(state.get("warnings", []))
        for profile in state["profiles"]:
            grounded_skills = {
                evidence.field.removeprefix("技能:")
                for evidence in profile.evidence
                if evidence.field.startswith("技能:")
            }
            unsupported = [skill for skill in profile.skills if skill not in grounded_skills]
            if unsupported:
                profile.skills = [
                    skill for skill in profile.skills if skill in grounded_skills
                ]
                warnings.append(
                    f"{profile.name} 移除了缺少原文证据的技能：{'、'.join(unsupported)}"
                )
            profiles.append(profile)
        return {
            "profiles": profiles,
            "warnings": warnings,
            "trace": self._event(
                state,
                "validate_candidates",
                "已完成评分字段的原文证据校验",
            ),
        }

    def _questions(self, state: HireState) -> HireState:
        assessments = []
        source_by_file = {
            item["filename"]: item["text"] for item in state["resumes"]
        }
        for assessment in state["assessments"]:
            questions, followups = generate_questions(
                state["job"],
                assessment,
                self.llm,
                source_by_file.get(assessment.profile.source_file, ""),
                state.get("reliability_guidance", []),
            )
            assessment.interview_questions = questions
            assessment.follow_up_questions = followups
            assessments.append(assessment)
        return {
            "assessments": assessments,
            "trace": self._event(state, "questions", "已生成面试题、考察点、难度和评分标准"),
        }

    def _reflect(self, state: HireState) -> HireState:
        warnings = list(state.get("warnings", []))
        assessments = []
        for assessment in state["assessments"]:
            if len(assessment.interview_questions) < 10:
                warnings.append(f"{assessment.profile.name} 的面试题不足 10 道")
            if not 3 <= len(assessment.follow_up_questions) <= 5:
                warnings.append(f"{assessment.profile.name} 的追问题量异常")
            grounded_skills = {
                key
                for evidence in assessment.profile.evidence
                if evidence.field.startswith("技能:")
                for key in skill_keys(evidence.field.removeprefix("技能:"))
            }
            unsupported = [
                skill for skill in assessment.matched_requirements
                if not (skill_keys(skill) & grounded_skills)
            ]
            if unsupported:
                questions = assessment.interview_questions
                followups = assessment.follow_up_questions
                assessment.profile.skills = [
                    skill for skill in assessment.profile.skills
                    if skill not in unsupported
                ]
                assessment = assess_candidate(state["job"], assessment.profile)
                assessment.interview_questions = questions
                assessment.follow_up_questions = followups
                warnings.append(f"{assessment.profile.name} 移除了缺少证据的匹配项")
            assessments.append(assessment)
        assessments.sort(key=lambda item: item.score, reverse=True)
        return {
            "assessments": assessments,
            "warnings": warnings,
            "trace": self._event(state, "reflect", "已完成结果完整性与证据一致性复核"),
        }

    def run(
        self,
        jd_text: str,
        resumes: list[dict[str, str]],
        initial_warnings: list[str] | None = None,
        reliability_guidance: list[str] | None = None,
        feedback_memory_count: int = 0,
    ) -> AnalysisResult:
        self.llm.begin_run()
        analysis_id = uuid.uuid4().hex[:12]
        state: HireState = {
            "analysis_id": analysis_id,
            "jd_text": jd_text,
            "resumes": resumes,
            "trace": [],
            "warnings": list(initial_warnings or []),
            "reliability_guidance": list(reliability_guidance or []),
            "feedback_memory_count": feedback_memory_count,
        }
        result = self.app.invoke(state)
        model_calls = self.llm.call_traces
        active_model_calls = [
            item for item in model_calls if item.status != "skipped"
        ]
        fallback_calls = [item for item in model_calls if item.status == "fallback"]
        warnings = list(result.get("warnings", []))
        trace = list(result["trace"])
        if active_model_calls:
            successes = sum(item.status == "success" for item in active_model_calls)
            trace.append(
                TraceEvent(
                    stage="harness",
                    message=f"模型调用校验完成：{successes}/{len(active_model_calls)} 次结果通过",
                )
            )
        if fallback_calls:
            warnings.append(
                f"{len(fallback_calls)} 次模型调用未通过格式或调用校验，已使用确定性结果降级"
            )
        audit = build_quality_audit(
            result["assessments"],
            warnings,
            model_calls,
        )
        trace.append(
            TraceEvent(
                stage="quality_audit",
                message=f"质量审计完成：{audit.summary}",
            )
        )
        return AnalysisResult(
            analysis_id=analysis_id,
            job=result["job"],
            candidates=result["assessments"],
            trace=trace,
            warnings=warnings,
            model_calls=model_calls,
            ai_enhanced=self.llm.enhanced_this_run,
            feedback_memory_used=feedback_memory_count,
            quality_audit=audit,
        )
