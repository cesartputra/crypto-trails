"""Validated FNA handoff, PAKD trail and institution-owned decision contracts."""
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Money = Decimal
DecisionStatus = Literal["NOT_SENT", "SENT_WAITING_DECISION", "APPROVED_FREEZE_BY_INSTITUTION", "PARTIAL_FREEZE_BY_INSTITUTION", "REJECTED_BY_INSTITUTION"]
FinalDecisionStatus = Literal["APPROVED_FREEZE_BY_INSTITUTION", "PARTIAL_FREEZE_BY_INSTITUTION", "REJECTED_BY_INSTITUTION"]

class FNAMoneyTrailHandoffInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    iasc_ticket_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.\-/]+$")
    fna_trail_id: str = Field(min_length=1, max_length=80, pattern=r"^[\w.\-/]+$")
    victim_pujk_name: str = Field(min_length=1, max_length=120)
    victim_account_number: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    victim_account_name: str = Field(min_length=1, max_length=120)
    last_fna_pujk_name: str = Field(min_length=1, max_length=120)
    last_fna_account_number: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    pakd_gateway_va_or_account: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9\-]+$")
    fiat_transfer_amount_idr: Money = Field(gt=0, le=Decimal("1000000000000"), max_digits=15, decimal_places=2)
    fiat_transfer_timestamp: datetime
    rrn_reference_number: str = Field(min_length=6, max_length=80, pattern=r"^[A-Za-z0-9\-]+$")
    scenario_id: str | None = Field(default=None, max_length=40)

    @field_validator("fiat_transfer_timestamp")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Waktu transfer handoff wajib menyertakan zona waktu, contoh +07:00")
        return value

class PAKDInternalTrailRecord(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    record_id: str
    target_node_id: str
    pakd_name: str
    intake_mechanism: Literal["DIRECT_VA_DEPOSIT", "P2P_ON_RAMP_ESCROW", "P2P_OFF_RAMP_SETTLEMENT", "ONCHAIN_DEPOSIT"]
    pakd_user_uid: str
    kyc_nik_masked: str = Field(pattern=r"^[0-9]{4}\*{8}[0-9]{4}$")
    fiat_in_idr: Money = Field(ge=0)
    crypto_purchased_token: Literal["USDT", "USDC", "BTC", "ETH", "SOL"]
    crypto_purchased_amount: Decimal = Field(ge=0)
    remaining_pakd_balance_idr: Money = Field(ge=0)
    withdrawal_status: Literal["STILL_IN_PAKD_WALLET", "WITHDRAWN_ONCHAIN", "SOLD_VIA_P2P_OFFRAMP"]
    withdrawal_tx_hash: str | None = None
    blockchain_network: str | None = None
    initial_wallet_address: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

class InstitutionFreezeDecision(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    request_id: str
    target_node_id: str
    institution_category: Literal["PUJK", "PAKD", "STABLECOIN_ISSUER_OR_GLOBAL_EXCHANGE"]
    institution_name: str
    request_status: DecisionStatus = "NOT_SENT"
    requested_amount_idr: Money = Field(ge=0)
    approved_frozen_amount_idr: Money = Field(default=Decimal(0), ge=0)
    institution_decision_notes: str = "Belum ada keputusan institusi."
    decided_by_unit: str = ""
    decision_timestamp: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

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
    chain_id: int | None = None
    is_bridge_node: bool = False
    bridge_protocol_name: str | None = None
    bridged_from_chain: str | None = None
    bridged_to_chain: str | None = None
    explorer_url: str = ""
    chain_family: str | None = None
    scanner_provider: str | None = None
    chain_branch: str | None = None
    address: str | None = None
    account: str | None = None
    taint_score_pct: Decimal | None = Field(default=None, ge=0, le=100)
    balance_crypto: Decimal = Field(default=Decimal(0), ge=0)
    status: str = "TRACED"
    hop: int = Field(default=0, ge=0)
    institution_decision: InstitutionFreezeDecision | None = None
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
    chain: str | None = None
    chain_id: int | None = None
    is_bridge_node: bool = False
    bridge_protocol_name: str | None = None
    bridged_from_chain: str | None = None
    bridged_to_chain: str | None = None
    explorer_url: str = ""
    chain_family: str | None = None
    scanner_provider: str | None = None
    chain_branch: str | None = None
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

class PAKDAPICallLog(BaseModel):
    endpoint_url: str
    request_payload: dict[str, Any] = Field(default_factory=dict)
    response_status_code: int
    latency_ms: float = Field(ge=0)
    scoped_fields_returned: list[str] = Field(default_factory=list)
    response_payload: dict[str, Any] = Field(default_factory=dict)
    pakd_code: str = ""
    transport_mode: str = "IN_PROCESS_ASGI_HTTP_DEMO"

class ChainDispersal(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    withdrawal_id: str
    chain: str
    chain_family: str
    scanner_provider: str
    token_symbol: str
    crypto_amount: Decimal = Field(ge=0)
    fiat_value_idr: Money = Field(ge=0)
    initial_wallet_address: str
    withdrawal_tx_hash: str
    withdrawal_timestamp: datetime
    initial_node_id: str | None = None
    endpoint_node_id: str | None = None
    endpoint_status: str = "TRACED"

class CrossRailGraphResponse(BaseModel):
    trace_id: str
    iasc_ticket_id: str
    fna_trail_id: str
    scenario_id: str | None
    simulation: bool = True
    fna_handoff: FNAMoneyTrailHandoffInput | None = None
    nodes: list[TrailNode]
    edges: list[TrailEdge]
    pakd_trails: list[PAKDInternalTrailRecord] = Field(default_factory=list)
    p2p_detections: list[P2PEscrowMatchDetail] = Field(default_factory=list)
    total_scam_idr: Money
    total_traced_idr: Money
    recoverable_amount_idr: Money
    confirmed_frozen_amount_idr: Money = Decimal(0)
    recovery_probability_pct: Decimal
    total_traced_usdt: Decimal = Decimal(0)
    hop_count: int = 0
    route_badges: list[str] = Field(default_factory=list)
    governance_requests: list[InstitutionFreezeDecision] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    live_metadata: dict[str, Any] = Field(default_factory=dict)
    pakd_api_exchange_logs: list[PAKDAPICallLog] = Field(default_factory=list)
    chains_used: list[str] = Field(default_factory=list)
    chain_dispersals: list[ChainDispersal] = Field(default_factory=list)

class LiveWalletTrailInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    wallet_address: str = Field(min_length=20, max_length=100)
    etherscan_api_key: str | None = Field(default=None, max_length=256, repr=False)
    max_hops: int = Field(default=2, ge=1, le=2, strict=True)
    token_filter: Literal["ALL", "ETH", "USDT", "USDC", "BTC", "SOL"] = "ALL"
    direction: Literal["OUTBOUND", "BOTH"] = "OUTBOUND"
    simulate_fna_and_mirror_origin: bool = False
    source_chain: Literal["BTC", "SOLANA", "ETHEREUM", "BSC", "POLYGON", "ARBITRUM", "OPTIMISM", "BASE"] = "ETHEREUM"
    auto_scan_cross_chain: bool = False

    @model_validator(mode="after")
    def address_matches_chain(self):
        from services.chains import is_valid_address
        if not is_valid_address(self.source_chain, self.wallet_address):
            raise ValueError("Alamat wallet tidak valid untuk chain yang dipilih")
        if self.source_chain == "BTC" and self.wallet_address.lower().startswith("bc1"):
            self.wallet_address = self.wallet_address.lower()
        allowed = {"BTC": {"ALL", "ETH", "BTC"}, "SOLANA": {"ALL", "ETH", "SOL"}}
        if self.source_chain in allowed and self.token_filter not in allowed[self.source_chain]:
            raise ValueError("Filter aset tidak sesuai scanner native yang dipilih")
        if self.source_chain not in allowed and self.token_filter in {"BTC", "SOL"}:
            raise ValueError("Aset BTC/SOL tidak didukung scanner EVM")
        return self

    @field_validator("etherscan_api_key")
    @classmethod
    def key_has_no_control_characters(cls, value):
        if value and any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("API key mengandung karakter tidak valid")
        return value

class SendFreezeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trace_id: str = Field(min_length=1, max_length=80)
    request_id: str = Field(min_length=1, max_length=100)

class SimulateInstitutionDecisionInput(SendFreezeRequest):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)
    decision: FinalDecisionStatus
    approved_frozen_amount_idr: Money | None = Field(default=None, ge=0, max_digits=15, decimal_places=2)
    institution_decision_notes: str | None = Field(default=None, min_length=1, max_length=1000)
