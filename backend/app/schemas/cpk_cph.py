from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class CpkCphInputRow(BaseModel):
    plate: str = Field(..., min_length=1, max_length=32)
    cutoff_start_at: str = Field(..., min_length=1)
    cutoff_end_at: str = Field(..., min_length=1)
    km_client: float | None = Field(default=None, ge=0)


class CpkCphPreviewRequest(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    customer_id: int = Field(..., gt=0)
    rows: list[CpkCphInputRow] = Field(default_factory=list)


class CpkCphRowBase(BaseModel):
    plate: str
    cutoff_start_at: str
    cutoff_end_at: str
    cutoff_start_utc: str | None = None
    cutoff_end_utc: str | None = None
    client_name: str | None = None
    database_name: str | None = None
    source_provider: str | None = None
    provider_vehicle_id: str | None = None
    vocacional: bool = False
    km_client: float | None = None
    odo_start: float | None = None
    odo_end: float | None = None
    horo_start: float | None = None
    horo_end: float | None = None
    kms_ecm_geotab: float | None = None
    kms_gps: float | None = None
    hours_ecm: float | None = None
    hours_gps: float | None = None
    fuel_gallons: float | None = None
    geotab_regression_count: int = Field(default=0, ge=0)
    geotab_regression_total_km: float = Field(default=0, ge=0)
    geotab_regression_total_hours: float = Field(default=0, ge=0)
    suggested_adjustment: float = Field(default=0, ge=0)
    km_adjustment: float | None = 0
    hour_adjustment: float | None = 0
    kms_ecm_approved: float | None = None
    hours_ecm_approved: float | None = None
    km_difference: float | None = None
    km_difference_pct: float | None = None
    hour_difference: float | None = None
    hour_difference_pct: float | None = None
    calculation_status: str = "pending"
    warnings: list[str] = Field(default_factory=list)
    correction_note: str | None = None


class CpkCphPreviewRow(CpkCphRowBase):
    row_number: int | None = None


class CpkCphPreviewResponse(BaseModel):
    month: str
    customer_id: int
    rows: list[CpkCphPreviewRow] = Field(default_factory=list)


class CpkCphReportRow(CpkCphRowBase):
    id: int | None = None
    report_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CpkCphReportSummary(BaseModel):
    id: int
    customer_id: int
    customer_name: str
    period_month: str
    status: str
    row_count: int = 0
    created_at: datetime
    updated_at: datetime


class CpkCphReportDetail(CpkCphReportSummary):
    rows: list[CpkCphReportRow] = Field(default_factory=list)


class CpkCphReportListResponse(BaseModel):
    reports: list[CpkCphReportSummary] = Field(default_factory=list)


class CpkCphReportSaveRequest(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-\d{2}$")
    customer_id: int = Field(..., gt=0)
    rows: list[CpkCphPreviewRow] = Field(default_factory=list)


class CpkCphRowPatchRequest(BaseModel):
    cutoff_start_at: str | None = None
    cutoff_end_at: str | None = None
    km_client: float | None = Field(default=None, ge=0)
    km_adjustment: float | None = None
    hour_adjustment: float | None = None
    kms_ecm_approved: float | None = Field(default=None, ge=0)
    hours_ecm_approved: float | None = Field(default=None, ge=0)
    correction_note: str | None = None
