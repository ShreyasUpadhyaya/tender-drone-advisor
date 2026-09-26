import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class InventoryRecord(Base):
    """A workspace-owned stock record; unlisted items remain solver-ineligible."""

    __tablename__ = "inventory_records"
    __table_args__ = (UniqueConstraint("workspace_id", "sku", name="uq_inventory_workspace_sku"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    workspace_id: Mapped[str] = mapped_column(String(80), index=True)
    catalog_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalog_items.id"), nullable=True, index=True
    )
    sku: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40), index=True)
    manufacturer: Mapped[str] = mapped_column(String(160))
    source_status: Mapped[str] = mapped_column(String(32))
    owner_subject: Mapped[str] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryVersion(Base):
    """Immutable manual count/availability assertion with actor provenance."""

    __tablename__ = "inventory_versions"
    __table_args__ = (
        UniqueConstraint("record_id", "version_number", name="uq_inventory_record_version"),
        UniqueConstraint("identity", name="uq_inventory_version_identity"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    record_id: Mapped[str] = mapped_column(ForeignKey("inventory_records.id"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    identity: Mapped[str] = mapped_column(String(64))
    on_hand_quantity: Mapped[int] = mapped_column(Integer)
    expected_quantity: Mapped[int] = mapped_column(Integer, default=0)
    expected_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    location: Mapped[str] = mapped_column(String(160))
    rationale: Mapped[str] = mapped_column(String(1000))
    extraction_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=True, index=True
    )
    requirement_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    requirement_categories: Mapped[list[str]] = mapped_column(JSON, default=list)
    recorded_by_subject: Mapped[str] = mapped_column(String(160), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
