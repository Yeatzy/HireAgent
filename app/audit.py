from __future__ import annotations

from .scoring import skill_keys
from .schemas import CandidateAssessment, ModelCallTrace, QualityAudit, QualityCheck


def _check(name: str, status: str, message: str) -> QualityCheck:
    return QualityCheck(name=name, status=status, message=message)


def _summarize_warnings(warnings: list[str]) -> str:
    if not warnings:
        return "未发现需要额外提示的运行事项"
    ocr_count = sum("OCR" in warning or "扫描版" in warning for warning in warnings)
    model_count = sum("模型调用" in warning or "确定性结果降级" in warning for warning in warnings)
    other_count = len(warnings) - ocr_count - model_count
    parts = []
    if ocr_count:
        parts.append(f"{ocr_count} 份扫描件已自动 OCR，建议抽样核验")
    if model_count:
        parts.append(f"{model_count} 条模型降级提示，已使用确定性结果")
    if other_count:
        parts.append(f"{other_count} 条其他运行提示")
    return "；".join(parts)


def build_quality_audit(
    candidates: list[CandidateAssessment],
    warnings: list[str],
    model_calls: list[ModelCallTrace],
) -> QualityAudit:
    checks: list[QualityCheck] = []

    checks.append(
        _check(
            "核心闭环",
            "pass" if candidates else "fail",
            f"已输出 {len(candidates)} 位候选人的评估结果"
            if candidates
            else "没有候选人评估结果",
        )
    )

    scores = [candidate.score for candidate in candidates]
    checks.append(
        _check(
            "候选人排序",
            "pass" if scores == sorted(scores, reverse=True) else "fail",
            "候选人已按匹配分从高到低排序",
        )
    )

    question_gaps = [
        f"{candidate.profile.name} {len(candidate.interview_questions)} 道"
        for candidate in candidates
        if len(candidate.interview_questions) < 10
    ]
    checks.append(
        _check(
            "面试题数量",
            "pass" if not question_gaps else "fail",
            "每位候选人均生成至少 10 道面试题"
            if not question_gaps
            else "面试题不足：" + "；".join(question_gaps),
        )
    )

    followup_gaps = [
        f"{candidate.profile.name} {len(candidate.follow_up_questions)} 道"
        for candidate in candidates
        if not 3 <= len(candidate.follow_up_questions) <= 5
    ]
    checks.append(
        _check(
            "追问数量",
            "pass" if not followup_gaps else "fail",
            "每位候选人均生成 3-5 道动态追问"
            if not followup_gaps
            else "追问数量异常：" + "；".join(followup_gaps),
        )
    )

    evidence_gaps: list[str] = []
    for candidate in candidates:
        grounded = {
            key
            for evidence in candidate.profile.evidence
            if evidence.field.startswith("技能:")
            for key in skill_keys(evidence.field.removeprefix("技能:"))
        }
        unsupported = [
            skill for skill in candidate.matched_requirements
            if not (skill_keys(skill) & grounded)
        ]
        if unsupported:
            evidence_gaps.append(f"{candidate.profile.name}: {'、'.join(unsupported)}")
    checks.append(
        _check(
            "证据一致性",
            "pass" if not evidence_gaps else "fail",
            "匹配要求均有简历原文证据"
            if not evidence_gaps
            else "存在缺少证据的匹配项：" + "；".join(evidence_gaps),
        )
    )

    fallback_count = sum(call.status == "fallback" for call in model_calls)
    success_count = sum(call.status == "success" for call in model_calls)
    if fallback_count:
        checks.append(
            _check(
                "模型降级",
                "warn",
                f"{fallback_count} 次模型调用失败，已熔断并使用确定性结果",
            )
        )
    elif success_count:
        checks.append(
            _check(
                "模型增强",
                "pass",
                f"{success_count} 次模型调用通过 JSON 和事实校验",
            )
        )
    else:
        checks.append(
            _check(
                "离线可运行",
                "pass",
                "未使用外部模型，规则引擎完成完整闭环",
            )
        )

    checks.append(
        _check(
            "运行提示",
            "warn" if warnings else "pass",
            _summarize_warnings(warnings),
        )
    )

    failed = sum(check.status == "fail" for check in checks)
    warned = sum(check.status == "warn" for check in checks)
    passed = sum(check.status == "pass" for check in checks)
    return QualityAudit(
        passed=failed == 0,
        summary=f"{passed} 项通过，{warned} 项提示，{failed} 项失败",
        checks=checks,
    )
