"""Validated public API contracts. All fixture identities are synthetic."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

Money = Decimal

class IASCTransactionReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    report_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.\-/]+$")
    nama_penyelenggara_korban: str = Field(min_length=1, max_length=120)
    nomor_rekening_korban: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    nama_pemilik_rekening_korban: str = Field(min_length=1, max_length=120)
    nama_penyelenggara_terlapor: str = Field(min_length=1, max_length=120)
    nomor_rekening_terlapor: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    nama_pemilik_rekening_terlapor: str = Field(min_length=1, max_length=120)
    waktu_transaksi: datetime
    nominal_transaksi: Money = Field(gt=0, le=Decimal("1000000000000"), max_digits=15, decimal_places=2)
    scenario_id: str | None = Field(default=None, max_length=40)

    @field_validator("waktu_transaksi")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Waktu transaksi wajib menyertakan zona waktu, contoh +07:00")
        return value

class TrailNode(BaseModel):
    id: str
    label: str
    node_zone: Literal["FIAT_BANK", "CRYPTO_EXCHANGE_BRIDGE", "ONCHAIN_WALLET"]
    node_type: str
    entity_label: str = ""
    amount_idr: Money = Decimal(0)
    crypto_amount: Decimal = Decimal(0)
    asset: str | None = None
    chain: str | None = None
    address: str | None = None
    account: str | None = None
    taint_score_pct: Decimal | None = Field(default=Decimal(100), ge=0, le=100)
    balance_crypto: Decimal = Field(default=Decimal(0), ge=0)
    status: str = "TRACED"
    hop: int = Field(default=0, ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

class TrailEdge(BaseModel):
    id: str
    source: str
    target: str
    edge_type: str
    label: str
    amount_idr: Money = Decimal(0)
    crypto_amount: Decimal = Decimal(0)
    asset: str | None = None
    tx_hash: str | None = None
    timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class P2PEscrowMatchDetail(BaseModel):
    p2p_direction: Literal["ON_RAMP_DEPOSIT", "OFF_RAMP_WITHDRAWAL"]
    platform_name: str
    order_id: str
    merchant_id: str
    merchant_bank_account: str
    scammer_uid: str
    fiat_amount_idr: Money
    crypto_amount_usdt: Decimal
    escrow_release_timestamp: datetime
    linked_onchain_address: str | None = None
    linked_final_bank_account: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)

class RecoveryAction(BaseModel):
    id: str
    action_type: Literal["FREEZE_BANK", "FREEZE_PAKD", "TARGETED_ESCROW_HOLD", "ISSUER_BLACKLIST_REQUEST"]
    node_id: str
    label: str
    amount_idr: Money = Field(ge=0)
    asset: str | None = None
    crypto_amount: Decimal = Decimal(0)
    status: str = "READY"
    reason: str
    metadata: dict[str, Any] = Field(default_factory=dict)

class CrossRailGraphResponse(BaseModel):
    trace_id: str
    report_id: str
    scenario_id: str | None
    simulation: bool = True
    nodes: list[TrailNode]
    edges: list[TrailEdge]
    p2p_detections: list[P2PEscrowMatchDetail] = Field(default_factory=list)
    total_scam_idr: Money
    total_traced_idr: Money
    recoverable_amount_idr: Money
    recovery_probability_pct: Decimal
    total_traced_usdt: Decimal = Decimal(0)
    hop_count: int = 0
    route_badges: list[str] = Field(default_factory=list)
    recovery_actions: list[RecoveryAction] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

class FreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str = Field(min_length=1, max_length=80)
    action_id: str = Field(min_length=1, max_length=80)
