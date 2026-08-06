from __future__ import annotations

import re
from hashlib import sha256

from app.models.identity_profile import IdentityProfile
from app.models.professor import Professor


def estimate_match_score(
    identity: IdentityProfile,
    professor: Professor,
) -> tuple[int, str]:
    primary_material_text = (
        identity.current_primary_material.extracted_text
        if getattr(identity, "current_primary_material", None) is not None
        else None
    )
    identity_terms = _extract_terms(
        "\n".join(
            filter(
                None,
                [
                    identity.name,
                    primary_material_text,
                ],
            ),
        ),
    )
    professor_terms = _extract_terms(
        "\n".join(
            filter(
                None,
                [
                    professor.name,
                    professor.title,
                    professor.university,
                    professor.school,
                    professor.department,
                    professor.research_direction,
                    " ".join(professor.recent_papers or []),
                ],
            ),
        ),
    )

    overlap = sorted(identity_terms & professor_terms)
    overlap_ratio = len(overlap) / max(len(professor_terms), 1)
    seed = sha256(
        f"{identity.email_address}:{professor.email or professor.name}".encode("utf-8"),
    ).hexdigest()
    fallback = int(seed[:2], 16) % 12
    score = min(98, 58 + int(overlap_ratio * 30) + fallback)

    if overlap:
        reason = f"检测到关键词重合：{', '.join(overlap[:4])}"
    else:
        reason = "未检测到明显关键词重合，按基础画像给出保守匹配分数"
    return score, reason


def build_draft_email(
    identity: IdentityProfile,
    professor: Professor,
    custom_subject: str | None = None,
    custom_body: str | None = None,
) -> tuple[str, str]:
    subject = custom_subject or f"申请与{professor.name}老师交流科研方向"
    if custom_body:
        return subject, custom_body

    research = professor.research_direction or "相关研究"
    body = (
        f"{professor.name}老师，您好：\n\n"
        f"我是{identity.name}，正在关注您在{research}方向上的工作。"
        "我希望能进一步了解课题组的研究机会，并向您请教后续交流方式。\n\n"
        f"{identity.name}"
    )
    return subject, body


def _extract_terms(content: str | None) -> set[str]:
    if not content:
        return set()

    raw_terms = re.split(r"[\s,，。；;、/|()（）:：]+", content.lower())
    return {
        term.strip()
        for term in raw_terms
        if len(term.strip()) >= 2
    }
