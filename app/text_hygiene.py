from __future__ import annotations

import re
from typing import Any


UNICODE_ESCAPE_PATTERN = re.compile(
    r"\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})"
)
MOJIBAKE_MARKERS = ("Ã", "Â", "â", "ä¸", "æ", "å", "ç")
MOJIBAKE_RUN_PATTERN = re.compile(r"[\u00a0-\u00ff\u2010-\u2026]+")
CHINESE_SECTION_PATTERN = re.compile(
    r"[\u4e00-\u9fff]{2,4}\s*(?:邮箱|手机|电话)"
    r"|教育背景|教育经历|实习及商赛经历|实习经历|科研经历|项目经历|社会工作|技能及其他|专业技能"
)


def decode_text_bytes(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _decode_unicode_escapes(value: str) -> str:
    if not UNICODE_ESCAPE_PATTERN.search(value):
        return value

    def replace(match: re.Match[str]) -> str:
        codepoint = match.group(1) or match.group(2)
        return chr(int(codepoint, 16))

    decoded = UNICODE_ESCAPE_PATTERN.sub(replace, value)
    try:
        return decoded.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        return decoded


def _badness(value: str) -> int:
    marker_count = sum(value.count(marker) for marker in MOJIBAKE_MARKERS)
    return marker_count + value.count("\ufffd") * 3


def _cjk_count(value: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", value))


def _repair_mojibake(value: str) -> str:
    if not any(marker in value for marker in MOJIBAKE_MARKERS):
        return value

    def repair_segment(segment: str) -> str:
        if not any(marker in segment for marker in MOJIBAKE_MARKERS):
            return segment
        original_score = (_cjk_count(segment), -_badness(segment))
        best = segment
        best_score = original_score
        for encoding in ("cp1252", "latin1"):
            try:
                candidate = segment.encode(encoding).decode("utf-8")
            except UnicodeError:
                continue
            score = (_cjk_count(candidate), -_badness(candidate))
            if score > best_score:
                best = candidate
                best_score = score
        return best

    best = repair_segment(value)
    if best != value:
        return best

    return MOJIBAKE_RUN_PATTERN.sub(
        lambda match: repair_segment(match.group(0)),
        value,
    )


def clean_text_encoding(value: str) -> str:
    value = _decode_unicode_escapes(value)
    value = _repair_mojibake(value)
    return value


def prefer_chinese_duplicate_text(value: str) -> str:
    total_cjk = _cjk_count(value)
    if total_cjk < 80:
        return value

    for match in CHINESE_SECTION_PATTERN.finditer(value):
        start = match.start()
        prefix = value[:start]
        suffix = value[start:]
        if (
            len(prefix) >= 300
            and _cjk_count(prefix) <= max(20, total_cjk // 10)
            and _cjk_count(suffix) >= 80
        ):
            return suffix.strip()

    first_cjk = re.search(r"[\u4e00-\u9fff]", value)
    if first_cjk and first_cjk.start() >= 500:
        suffix = value[first_cjk.start():]
        if _cjk_count(suffix) >= 80:
            return suffix.strip()
    return value


def normalize_payload_text(value: Any) -> Any:
    if isinstance(value, str):
        return clean_text_encoding(value)
    if isinstance(value, list):
        return [normalize_payload_text(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_payload_text(item) for key, item in value.items()}
    return value
