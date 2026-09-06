from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.agent_api_errors import AgentApiError
from app.core.agent_revisions import revision_for
from app.core.database import get_async_session, get_session_factory
from app.models import IdentityMaterial, IdentityProfile
from app.modules.identities.public import (
    MaterialMutationError,
    build_material_download_name,
    set_primary_material_record,
    upload_identity_material_record,
)
from app.schemas.agent import AgentChangePlanRead, AgentMaterialRead, AgentPage
from app.services.agent_change_plans import create_material_delete_change_plan
from app.services.agent_mutations import execute_agent_mutation

from .support import (
    _project_agent_collection_response,
    _slice_page,
)

router = APIRouter()


@router.get("/materials", response_model=AgentPage[AgentMaterialRead])
async def list_agent_materials(
    identity_id: int | None = Query(default=None, ge=1),
    source_identity_id: int | None = Query(default=None, ge=1),
    target_identity_id: int | None = Query(default=None, ge=1),
    material_type: str | None = Query(default=None),
    material_id: int | None = Query(default=None, ge=1),
    fields: str | None = Query(default=None, max_length=4_000),
    cursor: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_async_session),
) -> AgentPage[AgentMaterialRead] | Response:
    if (
        identity_id is not None
        and source_identity_id is not None
        and identity_id != source_identity_id
    ):
        raise HTTPException(
            status_code=422,
            detail="identity_id 与 source_identity_id 不能指定不同的上传来源身份",
        )
    if (
        target_identity_id is not None
        and await session.scalar(
            select(IdentityProfile.id).where(
                IdentityProfile.id == target_identity_id,
                IdentityProfile.deleted_at.is_(None),
            )
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="未找到身份配置")
    resolved_source_identity_id = (
        source_identity_id if source_identity_id is not None else identity_id
    )
    statement = select(IdentityMaterial).options(
        selectinload(IdentityMaterial.source_identity),
        selectinload(IdentityMaterial.default_for_identities),
    )
    if material_id is not None:
        statement = statement.where(IdentityMaterial.id == material_id)
    if resolved_source_identity_id is not None:
        statement = statement.where(
            IdentityMaterial.identity_id == resolved_source_identity_id,
        )
    if material_type:
        statement = statement.where(
            IdentityMaterial.material_type == material_type.strip().lower()
        )
    materials = list(
        (
            await session.scalars(
                statement.order_by(IdentityMaterial.id.asc())
                .offset(cursor)
                .limit(limit + 1),
            )
        ).unique(),
    )
    page, next_cursor, has_more = _slice_page(materials, cursor=cursor, limit=limit)
    default_context_identity_id = (
        target_identity_id if target_identity_id is not None else identity_id
    )
    response = AgentPage(
        items=[
            _serialize_material(
                material,
                include_text=False,
                target_identity_id=default_context_identity_id,
                default_for_identity_ids=[
                    identity.id for identity in material.default_for_identities
                ],
            )
            for material in page
        ],
        next_cursor=next_cursor,
        has_more=has_more,
    )
    return _project_agent_collection_response(response, fields)


@router.get("/materials/{material_id}", response_model=AgentMaterialRead)
async def read_agent_material(
    material_id: int,
    include_text: bool = Query(default=False),
    target_identity_id: int | None = Query(default=None, ge=1),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    material = await session.scalar(
        select(IdentityMaterial)
        .options(
            selectinload(IdentityMaterial.source_identity),
            selectinload(IdentityMaterial.default_for_identities),
        )
        .where(IdentityMaterial.id == material_id),
    )
    if material is None:
        raise HTTPException(status_code=404, detail="未找到材料")
    if (
        target_identity_id is not None
        and await session.scalar(
            select(IdentityProfile.id).where(
                IdentityProfile.id == target_identity_id,
                IdentityProfile.deleted_at.is_(None),
            )
        )
        is None
    ):
        raise HTTPException(status_code=404, detail="未找到身份配置")
    return _serialize_material(
        material,
        include_text=include_text,
        target_identity_id=(
            target_identity_id
            if target_identity_id is not None
            else material.identity_id
        ),
        default_for_identity_ids=[
            identity.id for identity in material.default_for_identities
        ],
    )


@router.get("/materials/{material_id}/download")
async def download_agent_material(
    material_id: int,
    session: AsyncSession = Depends(get_async_session),
) -> FileResponse:
    material = await session.scalar(
        select(IdentityMaterial).where(IdentityMaterial.id == material_id),
    )
    if material is None:
        raise HTTPException(status_code=404, detail="未找到材料")
    file_path = Path(material.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="材料文件不存在")
    return FileResponse(
        file_path,
        media_type=material.mime_type,
        filename=build_material_download_name(material),
    )


@router.post(
    "/materials",
    response_model=AgentMaterialRead,
    status_code=status.HTTP_201_CREATED,
)
async def upload_agent_material(
    identity_id: int | None = Form(default=None, ge=1),
    file: UploadFile = File(...),
    material_type: str = Form(default="other"),
    display_name: str | None = Form(default=None),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    content = await file.read()
    await file.seek(0)
    request_data = {
        "identity_id": identity_id,
        "filename": file.filename or "upload.bin",
        "content_type": file.content_type,
        "size_bytes": len(content),
        "sha256": sha256(content).hexdigest(),
        "material_type": material_type,
        "display_name": display_name,
    }
    try:
        return await execute_agent_mutation(
            session,
            command="materials.upload",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentMaterialRead,
            mutation=lambda: _upload_agent_material(
                session,
                identity_id,
                file,
                material_type,
                display_name,
            ),
        )
    except MaterialMutationError as exc:
        raise _agent_material_error(exc) from exc


@router.post("/materials/{material_id}/set-primary", response_model=AgentMaterialRead)
async def set_agent_primary_material(
    material_id: int,
    identity_id: int | None = Query(default=None, ge=1),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    session: AsyncSession = Depends(get_async_session),
) -> AgentMaterialRead:
    request_data = {"material_id": material_id}
    if identity_id is not None:
        request_data["identity_id"] = identity_id
    try:
        return await execute_agent_mutation(
            session,
            command="materials.set-primary",
            request_data=request_data,
            idempotency_key=idempotency_key,
            response_type=AgentMaterialRead,
            mutation=lambda: _set_agent_primary_material(
                session,
                material_id,
                identity_id,
            ),
        )
    except MaterialMutationError as exc:
        raise _agent_material_error(exc) from exc


@router.post(
    "/materials/{material_id}/prepare-delete",
    response_model=AgentChangePlanRead,
    status_code=status.HTTP_201_CREATED,
)
async def prepare_agent_material_delete(
    material_id: int,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AgentChangePlanRead:
    return await create_material_delete_change_plan(
        get_session_factory(),
        material_id,
        idempotency_key=idempotency_key,
    )


async def _upload_agent_material(
    session: AsyncSession,
    identity_id: int | None,
    file: UploadFile,
    material_type: str,
    display_name: str | None,
) -> AgentMaterialRead:
    material, primary_material_id = await upload_identity_material_record(
        session,
        identity_id,
        file,
        material_type,
        display_name,
        event_name="agent_cli.material.uploaded",
        actor="agent_cli",
    )
    return _serialize_material(
        material,
        include_text=False,
        primary_material_id=primary_material_id,
        target_identity_id=identity_id,
        default_for_identity_ids=(
            [identity_id]
            if identity_id is not None and primary_material_id == material.id
            else []
        ),
    )


async def _set_agent_primary_material(
    session: AsyncSession,
    material_id: int,
    identity_id: int | None,
) -> AgentMaterialRead:
    material, primary_material_id = await set_primary_material_record(
        session,
        material_id,
        identity_id=identity_id,
        event_name="agent_cli.material.primary_set",
        actor="agent_cli",
    )
    target_identity_id = (
        identity_id if identity_id is not None else material.identity_id
    )
    default_identity_ids = {identity.id for identity in material.default_for_identities}
    if target_identity_id is not None:
        default_identity_ids.add(target_identity_id)
    return _serialize_material(
        material,
        include_text=False,
        primary_material_id=primary_material_id,
        target_identity_id=target_identity_id,
        default_for_identity_ids=sorted(default_identity_ids),
    )


def _agent_material_error(error: MaterialMutationError) -> AgentApiError:
    return AgentApiError(
        status_code=error.status_code,
        code=error.code,
        message=error.message,
        details=error.details or {},
    )


def _serialize_material(
    material: IdentityMaterial,
    *,
    include_text: bool,
    primary_material_id: int | None = None,
    target_identity_id: int | None = None,
    default_for_identity_ids: list[int] | None = None,
) -> AgentMaterialRead:
    resolved_default_identity_ids = sorted(set(default_for_identity_ids or []))
    if primary_material_id == material.id and target_identity_id is not None:
        resolved_default_identity_ids = sorted(
            set(resolved_default_identity_ids) | {target_identity_id},
        )
    is_primary = (
        target_identity_id in resolved_default_identity_ids
        if target_identity_id is not None
        else primary_material_id == material.id or bool(resolved_default_identity_ids)
    )
    result = AgentMaterialRead(
        id=material.id,
        source_identity_id=material.identity_id,
        identity_id=material.identity_id,
        display_name=material.display_name,
        original_filename=material.original_filename,
        mime_type=material.mime_type,
        size_bytes=material.size_bytes,
        material_type=material.material_type,
        is_primary=is_primary,
        default_for_identity_ids=resolved_default_identity_ids,
        has_extracted_text=bool(material.extracted_text),
        extracted_text=material.extracted_text if include_text else None,
        created_at=material.created_at,
    )
    return result.model_copy(update={"revision": revision_for(result)})
