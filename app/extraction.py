from __future__ import annotations

import re
from collections import Counter
from datetime import date

from .guardrails import (
    grounded_items,
    is_grounded,
    reliability_prompt,
    sanitize_untrusted_text,
)
from .llm import QwenClient
from .prompts import JD_EXTRACTION_PROMPT, RESUME_EXTRACTION_PROMPT
from .schemas import CandidateProfile, Evidence, JobProfile


SKILL_ALIASES = {
    "Python": ["python"],
    "SQL": ["sql"],
    "大模型": ["大模型", "llm", "chatgpt", "claude", "通义千问", "qwen"],
    "Prompt": ["prompt", "提示词"],
    "Agent": ["agent", "智能体"],
    "LangChain": ["langchain"],
    "LangGraph": ["langgraph"],
    "RAG": ["rag", "知识库", "向量检索"],
    "FastAPI": ["fastapi"],
    "Vue3": ["vue3", "vue"],
    "Milvus": ["milvus", "向量数据库"],
    "PostgreSQL": ["postgresql", "postgres", "pg"],
    "数据分析": ["数据分析", "数据驱动", "复盘"],
    "内容策划": ["内容策划", "选题", "内容运营"],
    "小红书": ["小红书"],
    "抖音": ["抖音"],
    "短视频": ["短视频", "视频制作", "剪辑"],
    "Midjourney": ["midjourney"],
    "Runway": ["runway"],
    "自动化": ["自动化", "工作流", "脚本"],
    "B2B": ["b2b", "供应链"],
    "项目管理": ["项目管理", "跨部门", "协作"],
    "Harness": ["harness", "guardrail", "guardrails", "证据校验", "格式校验", "可靠性"],
    "OCR": ["ocr", "扫描件", "文字识别"],
}

NAME_STOPWORDS = {
    "深度实战版",
    "实战版",
    "个人简历",
    "简历",
    "候选人",
    "求职简历",
    "专业技能",
    "教育经历",
    "项目经验",
}

LLM_CONTEXT_KEYWORDS = {
    "姓名", "name", "教育", "学历", "本科", "硕士", "博士", "专业", "学校",
    "实习", "项目", "职责", "负责", "主导", "参与", "推动",
    "技能", "能力", "熟悉", "掌握", "使用", "开发", "搭建", "落地",
    "成果", "业绩", "提升", "增长", "降低", "转化", "粉丝", "播放", "数据",
    "python", "sql", "llm", "qwen", "agent", "prompt", "rag", "langchain",
    "小红书", "抖音", "短视频", "自动化", "运营", "内容", "ai",
}


def _sentences(text: str) -> list[str]:
    values = re.split(r"[\n。；;]+", text)
    return [item.strip(" -•\t") for item in values if len(item.strip()) >= 4]


def _llm_context_limit(llm: QwenClient) -> int:
    settings = getattr(llm, "settings", None)
    return max(100, int(getattr(settings, "llm_input_char_limit", 6000)))


def _join_limited(lines: list[str], limit: int) -> str:
    selected: list[str] = []
    total = 0
    for line in lines:
        next_total = total + len(line) + (1 if selected else 0)
        if next_total > limit:
            remaining = limit - total - (1 if selected else 0)
            if remaining >= 30:
                selected.append(line[:remaining])
            break
        selected.append(line)
        total = next_total
    return "\n".join(selected)


def _compact_for_llm(text: str, limit: int = 6000) -> str:
    source = sanitize_untrusted_text(text)
    if len(source) <= limit:
        return source

    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if not lines:
        return source[:limit]

    seen: set[str] = set()

    def add_unique(target: list[str], line: str) -> None:
        normalized = re.sub(r"\s+", " ", line).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            target.append(normalized)

    head_lines: list[str] = []
    for line in lines[:18]:
        add_unique(head_lines, line)

    signal_lines: list[str] = []
    numeric_pattern = re.compile(r"\d+%|20\d{2}|[0-9]+(?:\.[0-9]+)?\s*年|[0-9]+[万千百+]")
    for line in lines:
        lowered = line.lower()
        if (
            any(keyword in lowered for keyword in LLM_CONTEXT_KEYWORDS)
            or numeric_pattern.search(line)
        ):
            add_unique(signal_lines, line)

    head_budget = min(1600, max(800, limit // 3))
    if limit < 1000:
        head_budget = max(60, limit // 3)
    head_text = _join_limited(head_lines, head_budget)
    remaining = max(0, limit - len(head_text) - 24)
    signal_text = _join_limited(signal_lines, remaining)
    compact = f"【文档开头】\n{head_text}\n【关键信号行】\n{signal_text}".strip()
    if len(compact) <= limit:
        return compact
    return compact[:limit]


def _sentence_has_term(sentence: str, term: str) -> bool:
    aliases = [term, *SKILL_ALIASES.get(term, [])]
    lowered = sentence.lower()
    for alias in aliases:
        candidate = alias.lower()
        if re.fullmatch(r"[a-z0-9+#.-]+", candidate):
            if re.search(rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])", lowered):
                return True
        elif candidate in lowered:
            return True
    return False


def _is_negated(sentence: str) -> bool:
    markers = ["没有", "无相关", "缺少", "欠缺", "未掌握", "未使用", "未参与", "较少", "不熟悉"]
    return any(marker in sentence for marker in markers)


def _find_skills(text: str) -> list[str]:
    found = []
    sentences = _sentences(text)
    for canonical in SKILL_ALIASES:
        positive_mentions = [
            sentence for sentence in sentences
            if _sentence_has_term(sentence, canonical) and not _is_negated(sentence)
        ]
        if positive_mentions:
            found.append(canonical)
    return found


def canonical_skill_names(text: str) -> list[str]:
    return [
        canonical for canonical in SKILL_ALIASES
        if _sentence_has_term(text, canonical) and not _is_negated(text)
    ]


def normalize_skill_items(items: list[str], source: str = "") -> list[str]:
    normalized: list[str] = []
    source_lower = source.lower()
    for raw_item in items:
        item = str(raw_item).strip()
        if not item:
            continue
        canonical_items = canonical_skill_names(item)
        if canonical_items:
            normalized.extend(canonical_items)
            continue
        if source and item.lower() not in source_lower:
            continue
        if len(item) <= 24:
            normalized.append(item)
    return list(dict.fromkeys(normalized))


def _find_evidence(text: str, term: str) -> str:
    for sentence in _sentences(text):
        if _sentence_has_term(sentence, term) and not _is_negated(sentence):
            return sentence[:180]
    return ""


def _extract_years(text: str) -> float:
    explicit_patterns = [
        r"(\d{1,2}(?:\.\d+)?)\s*年(?:以上)?(?:工作|相关|从业|实习)?经验",
        r"(?:工作|相关|从业|实习)?经验[：:\s]*(\d{1,2}(?:\.\d+)?)\s*年",
    ]
    explicit = [
        float(value)
        for pattern in explicit_patterns
        for value in re.findall(pattern, text, flags=re.IGNORECASE)
    ]
    if explicit:
        return min(max(explicit), 30)

    months: set[tuple[int, int]] = set()
    range_pattern = re.compile(
        r"(20\d{2})[./年-](\d{1,2})月?\s*(?:-|–|—|至|~)\s*"
        r"(?:(20\d{2})[./年-](\d{1,2})月?|至今|现在)"
    )
    for line in text.splitlines():
        if any(marker in line for marker in ["教育背景", "本科", "硕士", "博士", "大学", "学院"]):
            continue
        for match in range_pattern.finditer(line):
            start_year, start_month = int(match.group(1)), int(match.group(2))
            if match.group(3):
                end_year, end_month = int(match.group(3)), int(match.group(4))
            else:
                today = date.today()
                end_year, end_month = today.year, today.month
            start_index = start_year * 12 + start_month - 1
            end_index = end_year * 12 + end_month - 1
            if 0 <= end_index - start_index <= 360:
                for month_index in range(start_index, end_index + 1):
                    months.add(divmod(month_index, 12))
    return round(min(len(months) / 12, 30), 1)


def _extract_education(text: str) -> str:
    for level in ["博士", "硕士", "本科", "大专"]:
        if level in text:
            return level
    return ""


def _is_plausible_name(value: str) -> bool:
    name = value.strip()
    if name in NAME_STOPWORDS or any(word in name for word in NAME_STOPWORDS):
        return False
    return bool(re.fullmatch(r"[\u4e00-\u9fff]{2,4}", name))


def _extract_name_from_filename(filename: str) -> str:
    stem = filename.rsplit(".", 1)[0]
    first_part = re.split(r"[_\-\s]+", stem, maxsplit=1)[0].strip()
    if _is_plausible_name(first_part):
        return first_part
    match = re.search(r"([\u4e00-\u9fff]{2,4})(?:[_\-\s]|$)", stem)
    if match and _is_plausible_name(match.group(1)):
        return match.group(1)
    cleaned = re.sub(r"[_\-]+", " ", stem).strip()
    return cleaned[:30] or "候选人"


def _extract_candidate_name(filename: str, text: str) -> str:
    for line in text.splitlines()[:20]:
        match = re.search(r"(?:姓名|Name)\s*[:：]\s*([\u4e00-\u9fff]{2,4})", line, flags=re.IGNORECASE)
        if match and _is_plausible_name(match.group(1)):
            return match.group(1)
    filename_name = _extract_name_from_filename(filename)
    if _is_plausible_name(filename_name):
        return filename_name
    for line in text.splitlines()[:8]:
        candidate = line.strip(" -•\t:：|")
        if _is_plausible_name(candidate):
            return candidate
    return filename_name


def _top_keywords(text: str, limit: int = 12) -> list[str]:
    tokens = re.findall(r"[A-Za-z][A-Za-z0-9+#.-]{1,20}|[\u4e00-\u9fff]{2,8}", text)
    stop = {
        "负责", "要求", "工作", "能力", "相关", "优先", "熟悉", "具备", "进行", "以及",
        "岗位", "核心", "通过", "完成", "经验", "以上", "能够", "内容", "项目",
    }
    counts = Counter(token for token in tokens if token.lower() not in stop)
    return [item for item, _ in counts.most_common(limit)]


def extract_job_profile(
    text: str,
    llm: QwenClient,
    reliability_guidance: list[str] | None = None,
) -> JobProfile:
    task = "jd_extraction"
    grounded_source = sanitize_untrusted_text(text)
    preferred_section = re.split(r"加分项[:：]?", grounded_source, maxsplit=1)
    required_text = preferred_section[0]
    preferred_text = preferred_section[1] if len(preferred_section) > 1 else ""
    required_skills = _find_skills(required_text)
    preferred_skills = _find_skills(preferred_text)
    fallback = JobProfile(
        title=_sentences(text)[0][:40] if _sentences(text) else "未命名岗位",
        responsibilities=[item for item in _sentences(text) if any(k in item for k in ["负责", "工作", "职责"])][:8],
        required_skills=required_skills,
        preferred_skills=preferred_skills,
        minimum_years=_extract_years(grounded_source),
        education=_extract_education(grounded_source),
        keywords=_top_keywords(grounded_source),
    )
    payload = llm.call_json(
        JD_EXTRACTION_PROMPT + reliability_prompt(reliability_guidance),
        f"<source_document>\n{_compact_for_llm(grounded_source, _llm_context_limit(llm))}\n</source_document>",
        task=task,
    )
    if not payload:
        return fallback
    try:
        profile = JobProfile.model_validate(payload)
    except Exception as exc:
        llm.mark_invalid(task, f"JD Schema 校验失败: {exc}")
        return fallback
    allowed = set(required_skills + preferred_skills)
    profile.required_skills = normalize_skill_items(
        [*profile.required_skills, *required_skills],
        grounded_source,
    )
    profile.preferred_skills = normalize_skill_items(
        [*profile.preferred_skills, *preferred_skills],
        grounded_source,
    )
    profile.title = profile.title if is_grounded(profile.title, grounded_source) else fallback.title
    profile.responsibilities = grounded_items(profile.responsibilities, grounded_source, fallback.responsibilities)[:8]
    profile.minimum_years = fallback.minimum_years
    profile.education = profile.education if profile.education and profile.education in grounded_source else fallback.education
    profile.keywords = grounded_items(profile.keywords, grounded_source, fallback.keywords)[:16]
    return profile


def extract_candidate_profile(
    candidate_id: str,
    filename: str,
    text: str,
    llm: QwenClient,
    reliability_guidance: list[str] | None = None,
) -> CandidateProfile:
    task = f"resume_extraction:{filename}"
    grounded_source = sanitize_untrusted_text(text)
    sentences = _sentences(grounded_source)
    fallback_name = _extract_candidate_name(filename, grounded_source)
    skills = _find_skills(grounded_source)
    evidence = [
        Evidence(field=f"技能:{skill}", snippet=snippet)
        for skill in skills
        if (snippet := _find_evidence(grounded_source, skill))
    ]
    achievements = [
        item for item in sentences
        if re.search(r"\d+%|\d+[万千百+]|提升|增长|降低|粉丝|播放|转化", item)
    ][:8]
    fallback = CandidateProfile(
        candidate_id=candidate_id,
        source_file=filename,
        name=fallback_name,
        years_experience=_extract_years(grounded_source),
        education=_extract_education(grounded_source),
        skills=skills,
        experience_highlights=sentences[:8],
        achievements=achievements,
        risks=_detect_risks(grounded_source, skills, achievements),
        evidence=evidence,
    )
    payload = llm.call_json(
        RESUME_EXTRACTION_PROMPT + reliability_prompt(reliability_guidance),
        f"<source_document>\n{_compact_for_llm(grounded_source, _llm_context_limit(llm))}\n</source_document>",
        task=task,
    )
    if not payload:
        return fallback
    payload["candidate_id"] = candidate_id
    payload["source_file"] = filename
    try:
        profile = CandidateProfile.model_validate(payload)
    except Exception as exc:
        llm.mark_invalid(task, f"简历 Schema 校验失败: {exc}")
        return fallback
    profile.name = (
        profile.name
        if _is_plausible_name(profile.name) and is_grounded(profile.name, grounded_source)
        else fallback.name
    )
    profile.years_experience = fallback.years_experience
    profile.education = profile.education if profile.education and profile.education in grounded_source else fallback.education
    profile.skills = normalize_skill_items(profile.skills, grounded_source)
    profile.skills = [skill for skill in profile.skills if _find_evidence(grounded_source, skill)]
    profile.skills = list(dict.fromkeys([*profile.skills, *fallback.skills]))
    profile.experience_highlights = grounded_items(
        profile.experience_highlights,
        grounded_source,
        fallback.experience_highlights,
    )[:8]
    profile.achievements = grounded_items(
        profile.achievements,
        grounded_source,
        fallback.achievements,
    )[:8]
    profile.evidence = [
        Evidence(field=f"技能:{skill}", snippet=snippet)
        for skill in profile.skills
        if (snippet := _find_evidence(grounded_source, skill))
    ]
    profile.risks = list(dict.fromkeys(profile.risks + fallback.risks))[:8]
    return profile


def _detect_risks(text: str, skills: list[str], achievements: list[str]) -> list[str]:
    risks = []
    if not achievements:
        risks.append("项目成果缺少量化数据")
    if len(skills) < 3:
        risks.append("技能信息较少，需要面试进一步确认")
    if not re.search(r"\d+(?:\.\d+)?\s*年", text):
        risks.append("工作年限表述不明确")
    if "负责" in text and not any(word in text for word in ["结果", "提升", "增长", "降低"]):
        risks.append("职责描述较多，个人贡献边界不清晰")
    return risks


class PathLikeName:
    @staticmethod
    def from_filename(filename: str) -> str:
        return _extract_name_from_filename(filename)
