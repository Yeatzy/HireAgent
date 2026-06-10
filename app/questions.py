from __future__ import annotations

import re

from .guardrails import (
    follow_up_is_grounded,
    question_is_grounded,
    reliability_prompt,
)
from .llm import QwenClient
from .prompts import QUESTION_REFINEMENT_PROMPT
from .schemas import CandidateAssessment, InterviewQuestion, JobProfile


def _question(
    question: str,
    focus: str,
    difficulty: str,
    criteria: list[str],
) -> InterviewQuestion:
    return InterviewQuestion(
        question=question,
        focus=focus,
        difficulty=difficulty,
        scoring_criteria=criteria,
    )


def _clean_fact(value: str, limit: int = 80) -> str:
    cleaned = re.sub(r"\s+", " ", value).strip(" -•\t")
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip(" ，,。.;；") + "..."


def _unique_facts(items: list[str], limit: int = 8) -> list[str]:
    facts: list[str] = []
    for item in items:
        fact = _clean_fact(item)
        if fact and fact not in facts:
            facts.append(fact)
        if len(facts) >= limit:
            break
    return facts


def _candidate_facts(profile) -> list[str]:
    evidence_snippets = [item.snippet for item in profile.evidence]
    return _unique_facts(
        [
            *profile.achievements,
            *profile.experience_highlights,
            *evidence_snippets,
        ],
        limit=10,
    )


def _fact_at(facts: list[str], index: int, fallback: str) -> str:
    if facts:
        return facts[index % len(facts)]
    return fallback


def _fallback_followups(
    job: JobProfile,
    assessment: CandidateAssessment,
    facts: list[str],
) -> list[str]:
    profile = assessment.profile
    follow_ups: list[str] = []

    if profile.achievements:
        follow_ups.append(
            f"简历写到“{_clean_fact(profile.achievements[0])}”，请补充这个结果的口径、基线和你个人负责的部分。"
        )
    if profile.experience_highlights:
        follow_ups.append(
            f"围绕“{_clean_fact(profile.experience_highlights[0])}”，请说明任务起点、关键动作和最终交付物。"
        )
    if profile.evidence:
        evidence = profile.evidence[0]
        follow_ups.append(
            f"你在“{_clean_fact(evidence.snippet)}”中体现了“{evidence.field.removeprefix('技能:')}”，请展开具体使用场景。"
        )
    for risk in profile.risks[:2]:
        follow_ups.append(f"简历中提示“{risk}”，请用一个具体项目补充背景、职责和可验证结果。")
    for item in assessment.missing_requirements[:2]:
        follow_ups.append(f"JD 要求“{item}”，但简历中缺少直接证据。请说明是否有相关经历，并给出具体案例。")
    if not follow_ups and facts:
        follow_ups.append(
            f"请围绕“{facts[0]}”说明你独立完成了哪些部分，哪些依赖团队协作。"
        )
    while len(follow_ups) < 3:
        focus = _fact_at(facts, len(follow_ups), job.title)
        follow_ups.append(f"请基于“{focus}”补充一个可量化的评价标准，以及当时如何确认结果有效。")
    return list(dict.fromkeys(follow_ups))[:5]


def generate_questions(
    job: JobProfile,
    assessment: CandidateAssessment,
    llm: QwenClient,
    source_text: str = "",
    reliability_guidance: list[str] | None = None,
) -> tuple[list[InterviewQuestion], list[str]]:
    profile = assessment.profile
    matched = assessment.matched_requirements
    primary_skill = matched[0] if matched else (job.required_skills[0] if job.required_skills else "岗位核心能力")
    second_skill = matched[1] if len(matched) > 1 else primary_skill
    facts = _candidate_facts(profile)
    fact_0 = _fact_at(facts, 0, profile.source_file)
    fact_1 = _fact_at(facts, 1, fact_0)
    fact_2 = _fact_at(facts, 2, fact_1)
    achievement = _clean_fact(profile.achievements[0]) if profile.achievements else fact_0
    highlight = _clean_fact(profile.experience_highlights[0]) if profile.experience_highlights else fact_1
    evidence = _clean_fact(profile.evidence[0].snippet) if profile.evidence else fact_2

    questions = [
        _question(
            f"请围绕简历中的“{highlight}”，用 3 分钟说明这段经历为什么能证明你适合“{job.title}”。",
            "岗位动机与经历概括",
            "基础",
            ["说明个人职责", "给出具体行动", "说明结果与岗位关联"],
        ),
        _question(
            f"简历中与“{primary_skill}”相关的证据是“{evidence}”。请详细拆解当时的场景、方法和你的贡献。",
            f"{primary_skill} 实战深度",
            "进阶",
            ["场景真实", "方法清晰", "能说明个人贡献", "有结果或复盘"],
        ),
        _question(
            f"基于你做过的“{fact_1}”，如果两周内接到一个与“{second_skill}”相关的新任务，你会如何迁移经验并规划交付？",
            "任务拆解与执行",
            "进阶",
            ["目标拆解合理", "明确资源与风险", "设置验证指标"],
        ),
        _question(
            f"请结合“{achievement}”说明你当时如何定义指标，并判断方案应该继续、调整还是停止。",
            "数据意识",
            "进阶",
            ["指标与目标一致", "能解释数据变化", "有决策闭环"],
        ),
        _question(
            f"围绕“{fact_2}”，请讲一个你和业务、产品或技术团队需要对齐判断的细节。",
            "跨团队协作",
            "进阶",
            ["能理解不同立场", "沟通方式具体", "结果可验证"],
        ),
        _question(
            f"请从“{fact_0}”中选择一个不确定或结果不完全理想的环节，说明你如何定位原因并复盘。",
            "失败复盘",
            "挑战",
            ["不回避失败", "区分事实与判断", "提出后续改进"],
        ),
        _question(
            f"在“{fact_1}”这类任务中，如果需求模糊或信息不足，你会优先确认哪些问题？",
            "需求澄清",
            "基础",
            ["识别关键约束", "明确交付标准", "能管理不确定性"],
        ),
        _question(
            f"结合“{fact_2}”，当时间有限时，你如何确定优先级，并向相关方解释取舍？",
            "优先级与推动力",
            "进阶",
            ["取舍标准明确", "识别关键路径", "沟通透明"],
        ),
        _question(
            f"参考你简历中的“{fact_0}”，你认为“{job.title}”入职前三个月最应该建立哪些工作机制？",
            "岗位理解",
            "挑战",
            ["理解岗位目标", "建议可执行", "有阶段性指标"],
        ),
        _question(
            f"如果“{fact_1}”相关产出中出现 AI 生成内容看似合理但缺少可靠依据的情况，你会如何验证？",
            "AI 风险意识",
            "挑战",
            ["检查来源", "交叉验证", "说明不确定性", "保留人工复核"],
        ),
    ]
    follow_ups = _fallback_followups(job, assessment, facts)

    task = f"question_refinement:{profile.source_file}"
    payload = llm.call_json(
        QUESTION_REFINEMENT_PROMPT + reliability_prompt(reliability_guidance),
        "<grounded_context>\n"
        f"岗位：{job.model_dump_json(ensure_ascii=False)}\n"
        f"候选人：{profile.model_dump_json(ensure_ascii=False)}\n"
        f"已有题目：{[item.model_dump() for item in questions]}\n"
        "</grounded_context>",
        task=task,
    )
    if payload:
        try:
            used_refinement = False
            refined = [InterviewQuestion.model_validate(item) for item in payload.get("questions", [])]
            refined_followups = [str(item) for item in payload.get("follow_ups", []) if str(item).strip()]
            grounded_questions = [
                item for item in refined
                if question_is_grounded(item, job, profile, source_text)
            ]
            grounded_followups = [
                item for item in refined_followups
                if follow_up_is_grounded(item, job, profile, source_text)
            ]
            unique_questions = list(
                {
                    item.question.strip(): item
                    for item in grounded_questions
                }.values()
            )
            unique_followups = list(dict.fromkeys(grounded_followups))
            if len(unique_questions) >= 10:
                questions = unique_questions[:12]
                used_refinement = True
            if 3 <= len(unique_followups) <= 5:
                follow_ups = unique_followups
                used_refinement = True
            if not used_refinement:
                llm.mark_invalid(
                    task,
                    "模型题目未通过事实依据、数量或去重校验",
                )
        except Exception as exc:
            llm.mark_invalid(task, f"面试题 Schema 校验失败: {exc}")
    return questions, follow_ups
