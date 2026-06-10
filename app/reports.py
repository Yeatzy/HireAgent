from __future__ import annotations

from datetime import datetime, timezone

from .schemas import AnalysisResult, CandidateAssessment


def _escape_markdown(value: str) -> str:
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _bullet(items: list[str]) -> list[str]:
    if not items:
        return ["- 暂无"]
    return [f"- {_escape_markdown(item)}" for item in items]


def _candidate_section(candidate: CandidateAssessment) -> list[str]:
    profile = candidate.profile
    breakdown = candidate.breakdown
    lines = [
        f"## {profile.name} · {candidate.recommendation}",
        "",
        f"- 简历文件：{profile.source_file}",
        f"- 匹配度：{candidate.score}/100",
        f"- 学历/年限：{profile.education or '未明确'} / {profile.years_experience or 0} 年",
        f"- 评分拆解：技能 {breakdown.skills}/45，经验 {breakdown.experience}/20，"
        f"学历 {breakdown.education}/10，成果 {breakdown.achievements}/15，"
        f"证据 {breakdown.evidence_quality}/10",
        "",
        "### 推荐理由",
        *_bullet(candidate.reasons),
        "",
        "### 已匹配要求",
        *_bullet(candidate.matched_requirements),
        "",
        "### 待验证要求",
        *_bullet(candidate.missing_requirements),
        "",
        "### 风险与信息缺口",
        *_bullet(profile.risks),
        "",
        "### 简历证据",
    ]
    if profile.evidence:
        lines.extend(
            f"- {item.field}：{_escape_markdown(item.snippet)}"
            for item in profile.evidence
        )
    else:
        lines.append("- 暂无可定位证据")

    lines.extend(["", "### 面试题"])
    for index, question in enumerate(candidate.interview_questions, 1):
        lines.extend(
            [
                f"{index}. {question.question}",
                f"   - 考察点：{question.focus}",
                f"   - 难度：{question.difficulty}",
                f"   - 评分标准：{'；'.join(question.scoring_criteria) or '暂无'}",
            ]
        )

    lines.extend(["", "### 动态追问", *_bullet(candidate.follow_up_questions), ""])
    return lines


def build_analysis_report(result: AnalysisResult) -> str:
    generated_at = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
    lines = [
        f"# HireAgent 招聘评估报告：{result.job.title}",
        "",
        f"- 分析 ID：{result.analysis_id}",
        f"- 生成时间：{generated_at}",
        f"- 候选人数：{len(result.candidates)}",
        f"- AI 增强：{'是' if result.ai_enhanced else '否，使用确定性降级闭环'}",
        f"- 本次加载人工复核记忆：{result.feedback_memory_used} 条",
        "",
        "## 候选人排序",
        "",
        "| 排名 | 候选人 | 分数 | 建议 | 关键缺口 |",
        "|---:|---|---:|---|---|",
    ]
    for index, candidate in enumerate(result.candidates, 1):
        missing = "、".join(candidate.missing_requirements[:4]) or "暂无"
        lines.append(
            f"| {index} | {candidate.profile.name} | {candidate.score} | "
            f"{candidate.recommendation} | {missing} |"
        )

    lines.extend(
        [
            "",
            "## 岗位结构化信息",
            "",
            "### 核心要求",
            *_bullet(result.job.required_skills),
            "",
            "### 加分项",
            *_bullet(result.job.preferred_skills),
            "",
            "### 岗位门槛",
            f"- 最低年限：{result.job.minimum_years or '未明确'}",
            f"- 学历要求：{result.job.education or '未明确'}",
            "",
            "### 岗位职责",
            *_bullet(result.job.responsibilities),
            "",
        ]
    )
    for candidate in result.candidates:
        lines.extend(_candidate_section(candidate))

    lines.extend(["## 执行链路与审计", ""])
    lines.extend(
        [
            f"- 质量审计：{'通过' if result.quality_audit.passed else '未通过'}",
            f"- 审计摘要：{result.quality_audit.summary}",
            "",
            "### 质量检查项",
        ]
    )
    if result.quality_audit.checks:
        lines.extend(
            f"- {check.name}：{check.status}，{check.message}"
            for check in result.quality_audit.checks
        )
    else:
        lines.append("- 暂无质量检查项")
    lines.extend(["", "### 工作流轨迹"])
    lines.extend(f"- {event.stage}：{event.message}" for event in result.trace)
    if result.warnings:
        lines.extend(["", "### 运行提示", *_bullet(result.warnings)])
    if result.model_calls:
        lines.extend(["", "### 模型调用状态"])
        lines.extend(
            f"- {call.task}：{call.status}，尝试 {call.attempts} 次"
            + (f"，错误：{call.error}" if call.error else "")
            for call in result.model_calls
        )

    lines.extend(
        [
            "",
            "## 使用建议",
            "",
            "- 本报告用于面试准备和初筛辅助，不应作为自动录用或淘汰的唯一依据。",
            "- 待验证要求应在面试中用追问确认，缺少原文证据的信息默认保持保守判断。",
            "- 人工复核发现的问题应回填到系统，作为下一轮可靠性策略的输入。",
        ]
    )
    return "\n".join(lines) + "\n"
