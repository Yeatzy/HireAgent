from __future__ import annotations

from .extraction import canonical_skill_names
from .schemas import CandidateAssessment, CandidateProfile, JobProfile, ScoreBreakdown


EDUCATION_RANK = {"": 0, "大专": 1, "本科": 2, "硕士": 3, "博士": 4}


def skill_keys(value: str) -> set[str]:
    canonical = canonical_skill_names(value)
    return set(canonical or [value])


def _candidate_skill_keys(candidate: CandidateProfile) -> set[str]:
    keys: set[str] = set()
    for skill in candidate.skills:
        keys.update(skill_keys(skill))
    for evidence in candidate.evidence:
        if evidence.field.startswith("技能:"):
            keys.update(skill_keys(evidence.field.removeprefix("技能:")))
    return keys


def _matched_items(requirements: list[str], candidate: CandidateProfile) -> list[str]:
    candidate_keys = _candidate_skill_keys(candidate)
    matched = []
    for requirement in requirements:
        if skill_keys(requirement) & candidate_keys:
            matched.append(requirement)
    return matched


def assess_candidate(job: JobProfile, candidate: CandidateProfile) -> CandidateAssessment:
    required = list(dict.fromkeys(job.required_skills))
    preferred = list(dict.fromkeys(job.preferred_skills))
    matched = _matched_items(required, candidate)
    missing = [item for item in required if item not in matched]

    skills_score = round(40 * len(matched) / max(len(required), 1))
    preferred_bonus = round(5 * len(_matched_items(preferred, candidate)) / max(len(preferred), 1))
    skills_score = min(45, skills_score + preferred_bonus)

    if job.minimum_years <= 0:
        experience_score = 20 if candidate.years_experience > 0 else 12
    else:
        experience_score = min(20, round(20 * candidate.years_experience / job.minimum_years))

    required_education = EDUCATION_RANK.get(job.education, 0)
    candidate_education = EDUCATION_RANK.get(candidate.education, 0)
    education_score = 10 if required_education == 0 or candidate_education >= required_education else 4
    achievement_score = min(15, 5 + len(candidate.achievements) * 2)
    evidence_score = min(10, len(candidate.evidence) * 2)

    breakdown = ScoreBreakdown(
        skills=skills_score,
        experience=experience_score,
        education=education_score,
        achievements=achievement_score,
        evidence_quality=evidence_score,
    )
    total = min(100, sum(breakdown.model_dump().values()))
    recommendation = "建议推进" if total >= 75 else "谨慎推进" if total >= 55 else "暂不推进"

    reasons = []
    if matched:
        reasons.append(f"匹配 {len(matched)} 项核心要求：{'、'.join(matched[:6])}")
    if candidate.achievements:
        reasons.append(f"简历包含 {len(candidate.achievements)} 项可量化成果")
    if missing:
        reasons.append(f"仍需验证：{'、'.join(missing[:5])}")
    if candidate.risks:
        reasons.append(candidate.risks[0])

    return CandidateAssessment(
        profile=candidate,
        score=total,
        recommendation=recommendation,
        breakdown=breakdown,
        matched_requirements=matched,
        missing_requirements=missing,
        reasons=reasons,
    )
