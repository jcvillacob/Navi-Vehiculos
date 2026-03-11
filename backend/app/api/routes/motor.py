from fastapi import APIRouter, File, Form, HTTPException, Path, UploadFile, status
from fastapi.responses import FileResponse

from app.schemas.vehicle import MotorAttachmentRecord, MotorCatalogRecord, MotorCatalogUpsertRequest
from app.services.motor_catalog import (
    create_motor,
    create_motor_attachment,
    delete_motor_attachment,
    get_motor_attachment_file,
    list_motor_attachments,
    list_motors,
    update_motor_attachment,
)

router = APIRouter(prefix="/motors", tags=["motors"])


@router.get("", response_model=list[MotorCatalogRecord])
def get_motors() -> list[MotorCatalogRecord]:
    return list_motors()


@router.post("", response_model=MotorCatalogRecord, status_code=status.HTTP_201_CREATED)
def create_motor_record(payload: MotorCatalogUpsertRequest) -> MotorCatalogRecord:
    try:
        return create_motor(payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{motor_id}/attachments", response_model=list[MotorAttachmentRecord])
def get_motor_attachments(
    motor_id: int = Path(..., ge=1, description="ID del motor"),
) -> list[MotorAttachmentRecord]:
    return list_motor_attachments(motor_id)


@router.post(
    "/{motor_id}/attachments",
    response_model=MotorAttachmentRecord,
    status_code=status.HTTP_201_CREATED,
)
def upload_motor_attachment(
    motor_id: int = Path(..., ge=1, description="ID del motor"),
    cpl: str = Form(..., min_length=1, description="CPL asociado al adjunto"),
    attachment: UploadFile = File(..., description="Imagen o PDF del motor"),
) -> MotorAttachmentRecord:
    try:
        return create_motor_attachment(
            motor_id,
            cpl=cpl,
            filename=attachment.filename or "",
            content_type=attachment.content_type,
            fileobj=attachment.file,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "no existe" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.put("/attachments/{attachment_id}", response_model=MotorAttachmentRecord)
def edit_motor_attachment(
    attachment_id: int = Path(..., ge=1, description="ID del adjunto"),
    cpl: str = Form(..., min_length=1, description="CPL asociado al adjunto"),
    attachment: UploadFile | None = File(default=None, description="Nuevo archivo opcional"),
) -> MotorAttachmentRecord:
    try:
        return update_motor_attachment(
            attachment_id,
            cpl=cpl,
            filename=attachment.filename if attachment else None,
            content_type=attachment.content_type if attachment else None,
            fileobj=attachment.file if attachment else None,
        )
    except ValueError as exc:
        detail = str(exc)
        status_code = 404 if "no existe" in detail.lower() else 400
        raise HTTPException(status_code=status_code, detail=detail) from exc


@router.delete("/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_motor_attachment(
    attachment_id: int = Path(..., ge=1, description="ID del adjunto"),
) -> None:
    try:
        delete_motor_attachment(attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/attachments/{attachment_id}/download")
def download_motor_attachment(
    attachment_id: int = Path(..., ge=1, description="ID del adjunto"),
) -> FileResponse:
    try:
        attachment, file_path = get_motor_attachment_file(attachment_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return FileResponse(
        path=file_path,
        media_type=attachment.content_type,
        filename=attachment.original_filename,
        content_disposition_type="inline",
    )
