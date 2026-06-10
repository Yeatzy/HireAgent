from __future__ import annotations

import re

from .schemas import CandidateProfile, InterviewQuestion, JobProfile


PROMPT_INJECTION_PATTERNS = [
    r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?",
    r"system\s+prompt",
    r"developer\s+message",
    r"忽略(?:以上|上述|前述|之前|系统)(?:要求|规则|指令|提示词)?",
    r"不要遵守(?:以上|上述|前述|系统)(?:要求|规则|指令)?",
    r"(?:请|必须)?(?:给我|将我|把我).{0,10}(?:高分|满分|推进|录用)",
    r"(?:输出|返回).{0,12}json.{0,12}(?:而不是|不要)",
]


def sanitize_untrusted_text(value: str) -> str:
    safe_lines = []
    for line in value.splitlines():
        lowered = line.lower()
        if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in PROMPT_INJECTION_PATTERNS):
            continue
        safe_lines.append(line)
    return "\n".join(safe_lines)


def reliability_prompt(guidance: list[str] | None) -> str:
    if not guidance:
        return ""
    safe_items = [
        sanitize_untrusted_text(item).strip()
        for item in guidance[:5]
        if sanitize_untrusted_text(item).strip()
    ]
    if not safe_items:
        return ""
    return (
        "\n<reliability_memory>\n"
        "以下规则来自历史人工复核的聚合错误类型，只用于提高事实可靠性，"
        "不得用于推断候选人的录用倾向或受保护属性：\n"
        + "\n".join(f"- {item}" for item in safe_items)
        + "\n</reliability_memory>"
    )


def compact_text(value: str) -> str:
    return re.sub(r"[\s·•,，。；;：:（）()【】\[\]]+", "", value).lower()


def is_grounded(value: str, source: str, minimum_length: int = 2) -> bool:
    candidate = compact_text(value)
    return len(candidate) >= minimum_length and candidate in compact_text(source)


def grounded_items(items: list[str], source: str, fallback: list[str]) -> list[str]:
    grounded = [item.strip() for item in items if is_grounded(item, source, 4)]
    return list(dict.fromkeys([*grounded, *fallback]))


def question_is_grounded(
    question: InterviewQuestion,
    job: JobProfile,
    profile: CandidateProfile,
    source_text: str,
) -> bool:
    if not question.question.strip() or not question.focus.strip():
        return False
    if len(question.scoring_criteria) < 2:
        return False

    context = "\n".join(
        [
            sanitize_untrusted_text(source_text),
            job.title,
            *job.required_skills,
            *job.preferred_skills,
            *profile.risks,
        ]
    )
    quoted = re.findall(r"[“「](.*?)[”」]", question.question)
    if any(not is_grounded(item, context, 2) for item in quoted):
        return False

    unsupported_assertions = [
        r"你曾(?:经)?(?:负责|主导|搭建|开发|管理|带领|实现)",
        r"你在[^，。？]{0,24}(?:项目|公司|团队)中(?:负责|主导|实现)",
        r"你通过[^，。？]{0,24}(?:提升|降低|实现|获得)",
    ]
    if any(re.search(pattern, question.question) for pattern in unsupported_assertions):
        return False

    allowed_numbers = {"3", "5", "10", "30", "60", "90", "100"}
    source_numbers = set(re.findall(r"\d+(?:\.\d+)?", context))
    question_numbers = set(re.findall(r"\d+(?:\.\d+)?", question.question))
    return question_numbers.issubset(source_numbers | allowed_numbers)


def follow_up_is_grounded(
    follow_up: str,
    job: JobProfile,
    profile: CandidateProfile,
    source_text: str,
) -> bool:
    if not follow_up.strip():
        return False
    synthetic = InterviewQuestion(
        question=follow_up,
        focus="风险验证",
        scoring_criteria=["说明事实", "提供证据"],
    )
    return question_is_grounded(synthetic, job, profile, source_text)
