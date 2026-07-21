from __future__ import annotations

from app.models import IdentityMaterial, IdentityProfile
from app.schemas.identity import IdentityMaterialRead, IdentityProfileRead


def serialize_identity(identity: IdentityProfile) -> IdentityProfileRead:
    current_primary_material_id = identity.current_primary_material_id
    profile_name = identity.profile_name or identity.name
    sender_name = identity.sender_name or profile_name
    materials = sorted(
        identity.materials,
        key=lambda item: (item.id == current_primary_material_id, item.created_at),
        reverse=True,
    )
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
        outreach_generation_mode=identity.outreach_generation_mode,
        outreach_template_subject=identity.outreach_template_subject,
        outreach_template_body_text=identity.outreach_template_body_text,
        outreach_template_body_html=identity.outreach_template_body_html,
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


def serialize_material(
    material: IdentityMaterial,
    current_primary_material_id: int | None,
) -> IdentityMaterialRead:
    return IdentityMaterialRead(
        id=material.id,
        display_name=material.display_name,
        original_filename=material.original_filename,
        mime_type=material.mime_type,
        size_bytes=material.size_bytes,
        material_type=material.material_type,
        is_primary=material.id == current_primary_material_id,
        created_at=material.created_at,
    )
