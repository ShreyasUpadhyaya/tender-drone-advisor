import uuid
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def new_id() -> str:
    return str(uuid.uuid4())


class CatalogVersion(Base):
    __tablename__ = "catalog_versions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    version: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    source: Mapped[str] = mapped_column(String(255), default="synthetic-demo")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    effective_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class Supplier(Base):
    __tablename__ = "catalog_suppliers"
    __table_args__ = (UniqueConstraint("supplier_code", name="uq_catalog_supplier_code"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    supplier_code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(160))
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    items: Mapped[list["CatalogItem"]] = relationship(back_populates="supplier")


class CatalogItem(Base):
    __tablename__ = "catalog_items"
    __table_args__ = (UniqueConstraint("sku", "item_version", name="uq_catalog_sku_version"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    catalog_version_id: Mapped[str] = mapped_column(ForeignKey("catalog_versions.id"), index=True)
    supplier_id: Mapped[str | None] = mapped_column(
        ForeignKey("catalog_suppliers.id"), nullable=True
    )
    supplier: Mapped[Supplier | None] = relationship(back_populates="items")
    sku: Mapped[str] = mapped_column(String(120), index=True)
    item_version: Mapped[int] = mapped_column(Integer, default=1)
    manufacturer: Mapped[str] = mapped_column(String(160))
    category: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active", index=True)
    weight_kg: Mapped[float] = mapped_column(Float)
    cost_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    inventory_qty: Mapped[int] = mapped_column(Integer, default=0)
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)
    availability: Mapped[str] = mapped_column(String(20), default="in_stock", index=True)
    specs: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class CatalogPrice(Base):
    __tablename__ = "catalog_prices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    amount_paise: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)


class CompatibilityRule(Base):
    __tablename__ = "catalog_compatibility_rules"
    __table_args__ = (
        UniqueConstraint("from_item_id", "to_item_id", "rule_type", name="uq_compatibility_pair"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    from_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    to_item_id: Mapped[str] = mapped_column(ForeignKey("catalog_items.id"), index=True)
    rule_type: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str] = mapped_column(String(500))
    constraints: Mapped[dict] = mapped_column(JSON, default=dict)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
