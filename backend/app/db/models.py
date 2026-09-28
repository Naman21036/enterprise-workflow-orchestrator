from datetime import datetime, timezone
import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from backend.app.db.database import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class CapabilityModel(Base):
    __tablename__ = "capabilities"

    capability_id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    target_app = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    versions = relationship("CapabilityVersionModel", back_populates="capability", cascade="all, delete-orphan")


class CapabilityVersionModel(Base):
    __tablename__ = "capability_versions"
    __table_args__ = (UniqueConstraint("capability_id", "version", name="uq_capability_version"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    capability_id = Column(String, ForeignKey("capabilities.capability_id"), nullable=False, index=True)
    version = Column(String, nullable=False)
    artifact_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    capability = relationship("CapabilityModel", back_populates="versions")


class RunModel(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_status_created", "status", "created_at"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    goal = Column(Text, nullable=False)
    target_app = Column(String, nullable=False)
    execution_mode = Column(String, nullable=False)
    status = Column(String, nullable=False)
    capability_id = Column(String, nullable=True, index=True)
    capability_version = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    finished_at = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    error_code = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    result_json = Column(JSON, nullable=True)

    steps = relationship("RunStepModel", back_populates="run", cascade="all, delete-orphan")
    handoffs = relationship("HandoffRecordModel", back_populates="run", cascade="all, delete-orphan")
    recording = relationship("DiscoveryRecordingModel", back_populates="run", uselist=False)


class WorkflowIdempotencyModel(Base):
    __tablename__ = "workflow_idempotency"

    key_hash = Column(String(64), primary_key=True)
    request_hash = Column(String(64), nullable=False)
    run_id = Column(String, ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class DiscoveryRecordingModel(Base):
    __tablename__ = "discovery_recordings"

    id = Column(String, primary_key=True)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False, unique=True, index=True)
    goal = Column(Text, nullable=False)
    target_application = Column(String, nullable=False)
    status = Column(String, nullable=False, default="RECORDING")
    started_at = Column(DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)
    actions_json = Column(JSON, nullable=False, default=list)
    checkpoints_json = Column(JSON, nullable=False, default=list)
    artifact_id = Column(String, nullable=True, index=True)
    artifact_version = Column(String, nullable=True)
    linked_replay_runs_json = Column(JSON, nullable=False, default=list)

    run = relationship("RunModel", back_populates="recording")
    events = relationship("RecordingEventModel", back_populates="recording", cascade="all, delete-orphan", order_by="RecordingEventModel.sequence")


class RecordingEventModel(Base):
    __tablename__ = "recording_events"
    __table_args__ = (
        UniqueConstraint("recording_id", "sequence", name="uq_recording_event_sequence"),
        Index("ix_recording_events_recording_time", "recording_id", "created_at"),
    )

    id = Column(String, primary_key=True, default=generate_uuid)
    recording_id = Column(String, ForeignKey("discovery_recordings.id", ondelete="CASCADE"), nullable=False, index=True)
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(48), nullable=False)
    payload_json = Column(JSON, nullable=False)
    evidence_path = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    recording = relationship("DiscoveryRecordingModel", back_populates="events")


class RunStepModel(Base):
    __tablename__ = "run_steps"
    __table_args__ = (UniqueConstraint("run_id", "step_number", name="uq_run_step_number"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    action_type = Column(String, nullable=False)
    target_description = Column(String, nullable=True)
    status = Column(String, nullable=False)
    duration_ms = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    screenshot_path = Column(String, nullable=True)
    observation_json = Column(JSON, nullable=True)

    run = relationship("RunModel", back_populates="steps")


class HandoffRecordModel(Base):
    __tablename__ = "handoff_records"
    __table_args__ = (Index("ix_handoff_status_created", "status", "created_at"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False, index=True)
    status = Column(String, nullable=False)
    reason = Column(Text, nullable=False)
    step_number = Column(Integer, nullable=False)
    screenshot_path = Column(String, nullable=True)
    operator_actions_json = Column(JSON, default=list)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    resolved_at = Column(DateTime, nullable=True)

    run = relationship("RunModel", back_populates="handoffs")


class EvidenceModel(Base):
    __tablename__ = "evidence"
    __table_args__ = (Index("ix_evidence_run_type", "run_id", "evidence_type"),)

    id = Column(String, primary_key=True, default=generate_uuid)
    run_id = Column(String, nullable=False, index=True)
    evidence_type = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    metadata_json = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class MemberModel(Base):
    __tablename__ = "bank_members"
    __table_args__ = (
        CheckConstraint("member_id >= 1000", name="ck_member_id_demo_range"),
        UniqueConstraint("email", name="uq_member_email"),
        Index("ix_bank_members_status", "membership_status"),
    )

    member_id = Column(Integer, primary_key=True)
    first_name = Column(String(80), nullable=False)
    last_name = Column(String(80), nullable=False)
    date_of_birth = Column(Date, nullable=False)
    email = Column(String(160), nullable=False)
    phone = Column(String(24), nullable=False)
    address = Column(String(240), nullable=False)
    membership_status = Column(String(24), nullable=False, default="ACTIVE")
    membership_type = Column(String(24), nullable=False, default="STANDARD")
    joined_at = Column(Date, nullable=False)
    is_synthetic = Column(Boolean, nullable=False, default=False, index=True)
    synthetic_batch = Column(String(48), nullable=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    accounts = relationship("AccountModel", back_populates="member", cascade="all, delete-orphan")
    cards = relationship("CardModel", back_populates="member", cascade="all, delete-orphan")
    loans = relationship("LoanModel", back_populates="member", cascade="all, delete-orphan")


class AccountModel(Base):
    __tablename__ = "bank_accounts"
    __table_args__ = (
        UniqueConstraint("account_number", name="uq_bank_account_number"),
        CheckConstraint("balance >= 0", name="ck_bank_account_balance_nonnegative"),
        Index("ix_bank_accounts_member_type_status", "member_id", "account_type", "account_status"),
    )

    account_id = Column(String(48), primary_key=True)
    member_id = Column(Integer, ForeignKey("bank_members.member_id", ondelete="CASCADE"), nullable=False, index=True)
    account_number = Column(String(24), nullable=False)
    account_type = Column(String(24), nullable=False)
    balance = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(3), nullable=False, default="USD")
    account_status = Column(String(24), nullable=False, default="ACTIVE")
    opened_at = Column(Date, nullable=False)
    closed_at = Column(Date, nullable=True)
    is_synthetic = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    member = relationship("MemberModel", back_populates="accounts")
    transactions = relationship("TransactionModel", back_populates="account", cascade="all, delete-orphan")
    cards = relationship("CardModel", back_populates="linked_account")
    statements = relationship("StatementModel", back_populates="account", cascade="all, delete-orphan")


class TransactionModel(Base):
    __tablename__ = "bank_transactions"
    __table_args__ = (
        UniqueConstraint("transaction_reference", name="uq_bank_transaction_reference"),
        Index("ix_bank_transactions_account_date", "account_id", "transaction_date"),
        Index("ix_bank_transactions_status_date", "transaction_status", "transaction_date"),
    )

    transaction_id = Column(String(48), primary_key=True)
    account_id = Column(String(48), ForeignKey("bank_accounts.account_id", ondelete="CASCADE"), nullable=False, index=True)
    transaction_reference = Column(String(48), nullable=False)
    transaction_type = Column(String(24), nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)  # Credits positive; debits negative.
    currency = Column(String(3), nullable=False, default="USD")
    description = Column(String(180), nullable=False)
    transaction_status = Column(String(24), nullable=False, default="POSTED")
    transaction_date = Column(DateTime, nullable=False, index=True)
    is_synthetic = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    account = relationship("AccountModel", back_populates="transactions")


class CardModel(Base):
    __tablename__ = "bank_cards"
    __table_args__ = (
        UniqueConstraint("masked_card_number", name="uq_bank_masked_card_number"),
        Index("ix_bank_cards_member_status", "member_id", "card_status"),
    )

    card_id = Column(String(48), primary_key=True)
    member_id = Column(Integer, ForeignKey("bank_members.member_id", ondelete="CASCADE"), nullable=False, index=True)
    linked_account_id = Column(String(48), ForeignKey("bank_accounts.account_id", ondelete="SET NULL"), nullable=True, index=True)
    masked_card_number = Column(String(24), nullable=False)
    card_type = Column(String(24), nullable=False)
    card_status = Column(String(24), nullable=False, default="ACTIVE")
    expiry_date = Column(Date, nullable=False)
    issued_at = Column(Date, nullable=False)
    is_synthetic = Column(Boolean, nullable=False, default=False, index=True)

    member = relationship("MemberModel", back_populates="cards")
    linked_account = relationship("AccountModel", back_populates="cards")


class LoanModel(Base):
    __tablename__ = "bank_loans"
    __table_args__ = (
        UniqueConstraint("loan_reference", name="uq_bank_loan_reference"),
        CheckConstraint("principal_amount > 0", name="ck_bank_loan_principal_positive"),
        CheckConstraint("outstanding_balance >= 0", name="ck_bank_loan_balance_nonnegative"),
        Index("ix_bank_loans_member_status", "member_id", "loan_status"),
    )

    loan_id = Column(String(48), primary_key=True)
    member_id = Column(Integer, ForeignKey("bank_members.member_id", ondelete="CASCADE"), nullable=False, index=True)
    loan_reference = Column(String(48), nullable=False)
    loan_type = Column(String(24), nullable=False)
    principal_amount = Column(Numeric(14, 2), nullable=False)
    outstanding_balance = Column(Numeric(14, 2), nullable=False)
    interest_rate = Column(Numeric(5, 3), nullable=False)
    loan_status = Column(String(24), nullable=False, default="CURRENT")
    next_payment_date = Column(Date, nullable=True)
    installment_amount = Column(Numeric(12, 2), nullable=False)
    is_synthetic = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    member = relationship("MemberModel", back_populates="loans")


class StatementModel(Base):
    __tablename__ = "bank_statements"
    __table_args__ = (
        UniqueConstraint("account_id", "period_start", "period_end", name="uq_bank_statement_period"),
        Index("ix_bank_statements_period", "period_start", "period_end"),
    )

    statement_id = Column(String(48), primary_key=True)
    account_id = Column(String(48), ForeignKey("bank_accounts.account_id", ondelete="CASCADE"), nullable=False, index=True)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    opening_balance = Column(Numeric(14, 2), nullable=False)
    closing_balance = Column(Numeric(14, 2), nullable=False)
    transaction_count = Column(Integer, nullable=False, default=0)
    is_synthetic = Column(Boolean, nullable=False, default=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    account = relationship("AccountModel", back_populates="statements")
