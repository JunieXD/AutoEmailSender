from __future__ import annotations

from app.models import IdentityProfile

from ..materials.public import serialize_material
from .schemas import IdentityProfileRead


def serialize_identity(identity: IdentityProfile) -> IdentityProfileRead:
    current_primary_material_id = identity.current_primary_material_id
    profile_name = identity.profile_name or identity.name
    sender_name = identity.sender_name or profile_name
    materials = sorted(
        identity.materials,
        key=lambda item: (item.id == current_primary_material_id, item.created_at),
        reverse=True,
    )
    default_template = identity.default_outreach_template
    if default_template is not None and default_template.archived_at is None:
        outreach_generation_mode = default_template.recommended_generation_mode
        outreach_template_subject = default_template.subject
        outreach_template_body_text = default_template.body_text
        outreach_template_body_html = default_template.body_html
        default_outreach_template_id = default_template.id
    else:
        outreach_generation_mode = identity.outreach_generation_mode
        outreach_template_subject = identity.outreach_template_subject
        outreach_template_body_text = identity.outreach_template_body_text
        outreach_template_body_html = identity.outreach_template_body_html
        default_outreach_template_id = None
    return IdentityProfileRead(
        id=identity.id,
        name=profile_name,
        profile_name=profile_name,
        sender_name=sender_name,
        communication_group_id=identity.communication_group_id,
        email_address=identity.email_address,
        smtp_host=identity.smtp_host,
        smtp_port=identity.smtp_port,
        smtp_username=identity.smtp_username,
        smtp_password=identity.smtp_password,
        imap_host=identity.imap_host,
        imap_port=identity.imap_port,
        imap_username=identity.imap_username,
        imap_password=identity.imap_password,
        default_language=identity.default_language,
        outreach_generation_mode=outreach_generation_mode,
        outreach_template_subject=outreach_template_subject,
        outreach_template_body_text=outreach_template_body_text,
        outreach_template_body_html=outreach_template_body_html,
        default_outreach_template_id=default_outreach_template_id,
        match_threshold=identity.match_threshold,
        daily_send_limit=identity.daily_send_limit,
        send_interval_min=identity.send_interval_min,
        send_interval_max=identity.send_interval_max,
        same_domain_cooldown_minutes=identity.same_domain_cooldown_minutes,
        is_default=identity.is_default,
        current_primary_material_id=current_primary_material_id,
        current_primary_material=(
            serialize_material(identity.current_primary_material, current_primary_material_id)
            if identity.current_primary_material is not None
            else None
        ),
        materials=[
            serialize_material(material, current_primary_material_id)
            for material in materials
        ],
        created_at=identity.created_at,
        updated_at=identity.updated_at,
    )
