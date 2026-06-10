from __future__ import annotations

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


def generate_questions(
    job: JobProfile,
    assessment: CandidateAssessment,
    llm: QwenClient,
    source_text: str = "",
    reliability_guidance: list[str] | None = None,
) -> tuple[list[InterviewQuestion], list[str]]:
    profile = assessment.profile
    matched = assessment.matched_requirements
    missing = assessment.missing_requirements
    primary_skill = matched[0] if matched else (job.required_skills[0] if job.required_skills else "岗位核心能力")
    second_skill = matched[1] if len(matched) > 1 else primary_skill

    questions = [
        _question(
            f"请用 3 分钟介绍一段最能证明你适合“{job.title}”的经历。",
            "岗位动机与经历概括",
            "基础",
            ["说明个人职责", "给出具体行动", "说明结果与岗位关联"],
        ),
        _question(
            f"请详细拆解你在“{primary_skill}”方面最有代表性的项目。",
            f"{primary_skill} 实战深度",
            "进阶",
            ["场景真实", "方法清晰", "能说明个人贡献", "有结果或复盘"],
        ),
        _question(
            f"如果让你在两周内完成一个与“{second_skill}”相关的新任务，你会如何规划？",
            "任务拆解与执行",
            "进阶",
            ["目标拆解合理", "明确资源与风险", "设置验证指标"],
        ),
        _question(
            "请举例说明你如何使用数据判断一个方案应该继续、调整还是停止。",
            "数据意识",
            "进阶",
            ["指标与目标一致", "能解释数据变化", "有决策闭环"],
        ),
        _question(
            "请讲一个你与业务、产品或技术团队意见不一致的案例。",
            "跨团队协作",
            "进阶",
            ["能理解不同立场", "沟通方式具体", "结果可验证"],
        ),
        _question(
            "请讲一个结果不理想的项目。你如何定位原因并完成复盘？",
            "失败复盘",
            "挑战",
            ["不回避失败", "区分事实与判断", "提出后续改进"],
        ),
        _question(
            "面对需求模糊、信息不足的任务，你通常先确认哪些问题？",
            "需求澄清",
            "基础",
            ["识别关键约束", "明确交付标准", "能管理不确定性"],
        ),
        _question(
            "当时间有限时，你如何确定任务优先级，并向相关方解释取舍？",
            "优先级与推动力",
            "进阶",
            ["取舍标准明确", "识别关键路径", "沟通透明"],
        ),
        _question(
            f"你认为“{job.title}”在入职前三个月最应该建立哪些工作机制？",
            "岗位理解",
            "挑战",
            ["理解岗位目标", "建议可执行", "有阶段性指标"],
        ),
        _question(
            "如果你发现 AI 生成的内容看起来合理但缺少可靠依据，你会如何验证？",
            "AI 风险意识",
            "挑战",
            ["检查来源", "交叉验证", "说明不确定性", "保留人工复核"],
        ),
    ]
    follow_ups = [
        f"简历中提到“{risk}”，请补充具体背景、你的职责和最终结果。"
        for risk in profile.risks[:3]
    ]
    for item in missing[:2]:
        follow_ups.append(f"JD 要求“{item}”，但简历中缺少直接证据。你是否有相关经历？")
    while len(follow_ups) < 3:
        follow_ups.append("请说明你在代表性项目中的个人贡献比例，以及哪些工作由你独立完成。")
    follow_ups = follow_ups[:5]

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
