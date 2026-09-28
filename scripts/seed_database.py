"""Seed the local APEX demo banking database with reproducible synthetic data."""

import argparse
import asyncio
import random
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import delete, select

from backend.app.db.database import AsyncSessionLocal, Base, engine
from backend.app.db.models import (
    AccountModel,
    CardModel,
    LoanModel,
    MemberModel,
    StatementModel,
    TransactionModel,
)

SYNTHETIC_BATCH = "apex-synthetic-v1"
SEED = 620260928
AS_OF = date(2026, 9, 28)
DEFAULT_MEMBER_COUNT = 600
FIRST_NAMES = ["Avery", "Jordan", "Taylor", "Morgan", "Riley", "Casey", "Cameron", "Quinn", "Robin", "Jamie", "Drew", "Skyler", "Parker", "Reese", "Alex", "Sam", "Maya", "Noah", "Amira", "Ethan", "Sofia", "Liam", "Priya", "Mateo", "Zoe", "Arjun", "Nina", "Eli", "Leah", "Omar"]
LAST_NAMES = ["Bennett", "Brooks", "Carter", "Chen", "Davis", "Edwards", "Flores", "Garcia", "Gupta", "Harris", "Hughes", "Jackson", "Kim", "Lewis", "Martinez", "Miller", "Nguyen", "Patel", "Reed", "Rivera", "Robinson", "Shah", "Singh", "Turner", "Walker", "Williams", "Wilson", "Young"]
STREETS = ["Maple Street", "Cedar Avenue", "Lakeview Drive", "Willow Lane", "Oak Street", "Sunset Boulevard", "Pine Court", "River Road", "Highland Way", "Meadow Lane"]
CITIES = [("Madison", "WI", "53703"), ("Austin", "TX", "78701"), ("Portland", "OR", "97204"), ("Raleigh", "NC", "27601"), ("Denver", "CO", "80202"), ("Tampa", "FL", "33602"), ("Columbus", "OH", "43215"), ("Phoenix", "AZ", "85004")]


def _money(value: int | Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.01"))


def _build_rows(member_count: int, start_id: int) -> tuple[list[MemberModel], list[AccountModel], list[TransactionModel], list[CardModel], list[LoanModel], list[StatementModel]]:
    rng = random.Random(SEED)
    members: list[MemberModel] = []
    accounts: list[AccountModel] = []
    transactions: list[TransactionModel] = []
    cards: list[CardModel] = []
    loans: list[LoanModel] = []
    statements: list[StatementModel] = []
    card_sequence = 1

    for offset in range(member_count):
        member_id = start_id + offset
        sample_names = {1001: ("Alice", "Smith"), 1002: ("John", "Doe"), 1003: ("Jane", "Miller")}
        first, last = sample_names[member_id] if member_id in sample_names else (
            FIRST_NAMES[(offset * 7 + rng.randrange(len(FIRST_NAMES))) % len(FIRST_NAMES)],
            LAST_NAMES[(offset * 11 + rng.randrange(len(LAST_NAMES))) % len(LAST_NAMES)],
        )
        city, state, postal = CITIES[offset % len(CITIES)]
        joined = date(2008 + offset % 18, 1 + (offset * 5) % 12, 1 + (offset * 7) % 27)
        member_status = "CLOSED" if offset > 0 and offset % 41 == 0 else ("REVIEW" if offset > 0 and offset % 29 == 0 else "ACTIVE")
        members.append(MemberModel(
            member_id=member_id,
            first_name=first,
            last_name=last,
            date_of_birth=date(1952 + offset % 51, 1 + (offset * 3) % 12, 1 + (offset * 13) % 27),
            email=f"member{member_id}@example.test",
            phone=f"+1-555-{(offset * 37 + 200) % 900 + 100:03d}-{(offset * 83 + 1000) % 9000 + 1000:04d}",
            address=f"{100 + offset % 8900} {STREETS[offset % len(STREETS)]}, {city}, {state} {postal}",
            membership_status=member_status,
            membership_type=["STANDARD", "PLUS", "PREMIER"][offset % 3],
            joined_at=joined,
            is_synthetic=True,
            synthetic_batch=SYNTHETIC_BATCH,
        ))

        # Deliberate edge cases: some members have no deposit accounts, most have
        # checking and savings, and a smaller group has a second savings account.
        if offset > 0 and offset % 31 == 0:
            account_types: list[str] = []
        elif offset > 0 and offset % 13 == 0:
            account_types = ["CHECKING", "SAVINGS", "SAVINGS"]
        elif offset % 7 == 0:
            account_types = ["SAVINGS"]
        else:
            account_types = ["CHECKING", "SAVINGS"]

        member_accounts: list[AccountModel] = []
        for account_index, account_type in enumerate(account_types, start=1):
            account_id = f"acct-{member_id}-{account_index}"
            opened = joined + timedelta(days=15 * account_index)
            closed = AS_OF - timedelta(days=30) if member_status == "CLOSED" or (offset > 0 and offset % 37 == 0) else None
            status = "CLOSED" if closed else ("FROZEN" if offset > 0 and offset % 17 == 0 else "ACTIVE")
            sample_balances = {
                (1001, "CHECKING"): Decimal("3120.50"), (1001, "SAVINGS"): Decimal("15800.00"),
                (1002, "CHECKING"): Decimal("1450.00"), (1002, "SAVINGS"): Decimal("7250.00"),
                (1003, "CHECKING"): Decimal("890.00"), (1003, "SAVINGS"): Decimal("2450.00"),
            }
            balance = sample_balances.get((member_id, account_type), _money(Decimal(rng.randrange(12500, 980000)) / 100))
            if closed:
                balance = Decimal("0.00")
            account = AccountModel(
                account_id=account_id,
                member_id=member_id,
                account_number=f"D{member_id:06d}{account_index:02d}{rng.randrange(100, 999):03d}",
                account_type=account_type,
                balance=balance,
                currency="USD",
                account_status=status,
                opened_at=opened,
                closed_at=closed,
                is_synthetic=True,
            )
            accounts.append(account)
            member_accounts.append(account)

            # A fixed historical sample, not a ledger used to reconstruct snapshot balances.
            for tx_index in range(12):
                txn_type = ["DEPOSIT", "WITHDRAWAL", "TRANSFER", "FEE"][((tx_index + offset) % 4)]
                magnitude = Decimal(rng.randrange(1250, 155000)).scaleb(-2).quantize(Decimal("0.01"))
                amount = magnitude if txn_type == "DEPOSIT" else -magnitude
                transaction_date = datetime.combine(AS_OF - timedelta(days=tx_index * 23 + rng.randrange(0, 18)), datetime.min.time(), tzinfo=timezone.utc)
                transactions.append(TransactionModel(
                    transaction_id=f"txn-{member_id}-{account_index}-{tx_index + 1:02d}",
                    account_id=account_id,
                    transaction_reference=f"APX{member_id:06d}{account_index:02d}{tx_index + 1:03d}",
                    transaction_type=txn_type,
                    amount=amount,
                    currency="USD",
                    description={"DEPOSIT": "Payroll deposit", "WITHDRAWAL": "Point-of-sale purchase", "TRANSFER": "Scheduled transfer", "FEE": "Monthly service fee"}[txn_type],
                    transaction_status="PENDING" if tx_index == 0 and offset % 9 == 0 else "POSTED",
                    transaction_date=transaction_date,
                    is_synthetic=True,
                ))

            # Three quarter summaries make statement retrieval demonstrable without
            # suggesting that historical synthetic transactions reconcile balances.
            for period in range(1, 4):
                period_end = date(2026, 3 * period, (date(2026, 3 * period + 1, 1) - timedelta(days=1)).day)
                period_start = date(2026, 3 * period - 2, 1)
                statements.append(StatementModel(
                    statement_id=f"stmt-{member_id}-{account_index}-{period}",
                    account_id=account_id,
                    period_start=period_start,
                    period_end=period_end,
                    opening_balance=_money(rng.randrange(10000, 800000) / 100),
                    closing_balance=balance,
                    transaction_count=4 + (offset + period) % 17,
                    is_synthetic=True,
                ))

        if offset % 5 != 0:
            card_count = 2 if offset % 11 == 0 else 1
            for card_index in range(card_count):
                linked = member_accounts[card_index % len(member_accounts)] if member_accounts and offset % 8 != 0 else None
                cards.append(CardModel(
                    card_id=f"card-{member_id}-{card_index + 1}",
                    member_id=member_id,
                    linked_account_id=linked.account_id if linked else None,
                    masked_card_number=f"**** **** **** {card_sequence % 10000:04d}",
                    card_type="DEBIT" if card_index == 0 else "CREDIT",
                    card_status="EXPIRED" if offset % 19 == 0 else ("FROZEN" if offset % 23 == 0 else "ACTIVE"),
                    expiry_date=date(2025, 1 + offset % 12, 1) if offset % 19 == 0 else date(2026 + offset % 5, 1 + offset % 12, 1),
                    issued_at=joined + timedelta(days=60),
                    is_synthetic=True,
                ))
                card_sequence += 1

        if offset % 4 == 0:
            loan_count = 2 if offset % 20 == 0 else 1
            for loan_index in range(loan_count):
                principal = _money(rng.randrange(250000, 25000000) / 100)
                outstanding = (principal * Decimal(rng.randrange(15, 96)) / Decimal(100)).quantize(Decimal("0.01"))
                loan_status = "PAID" if offset % 16 == 0 else ("DELINQUENT" if offset % 21 == 0 else "CURRENT")
                if loan_status == "PAID":
                    outstanding = Decimal("0.00")
                loans.append(LoanModel(
                    loan_id=f"loan-{member_id}-{loan_index + 1}",
                    member_id=member_id,
                    loan_reference=f"LN{member_id:06d}{loan_index + 1:02d}",
                    loan_type=["AUTO", "PERSONAL", "HOME"][((offset // 4) + loan_index) % 3],
                    principal_amount=principal,
                    outstanding_balance=outstanding,
                    interest_rate=Decimal(rng.randrange(275, 925)) / Decimal(100),
                    loan_status=loan_status,
                    next_payment_date=None if loan_status == "PAID" else AS_OF + timedelta(days=1 + offset % 28),
                    installment_amount=_money(rng.randrange(12500, 160000) / 100),
                    is_synthetic=True,
                ))

    return members, accounts, transactions, cards, loans, statements


async def _reset_demo_data() -> None:
    async with AsyncSessionLocal() as session, session.begin():
        member_ids = select(MemberModel.member_id).where(MemberModel.synthetic_batch == SYNTHETIC_BATCH)
        account_ids = select(AccountModel.account_id).where(
            AccountModel.is_synthetic.is_(True), AccountModel.member_id.in_(member_ids)
        )
        await session.execute(delete(StatementModel).where(StatementModel.account_id.in_(account_ids)))
        await session.execute(delete(TransactionModel).where(
            TransactionModel.is_synthetic.is_(True), TransactionModel.account_id.in_(account_ids)
        ))
        await session.execute(delete(CardModel).where(
            CardModel.is_synthetic.is_(True), CardModel.member_id.in_(member_ids)
        ))
        await session.execute(delete(LoanModel).where(
            LoanModel.is_synthetic.is_(True), LoanModel.member_id.in_(member_ids)
        ))
        await session.execute(delete(AccountModel).where(
            AccountModel.is_synthetic.is_(True), AccountModel.member_id.in_(member_ids)
        ))
        await session.execute(delete(MemberModel).where(MemberModel.synthetic_batch == SYNTHETIC_BATCH))


async def seed(member_count: int, start_id: int, reset_demo_data: bool) -> None:
    if member_count <= 0 or start_id < 1000:
        raise ValueError("members must be positive and start-id must be at least 1000")
    if reset_demo_data:
        await _reset_demo_data()

    members, accounts, transactions, cards, loans, statements = _build_rows(member_count, start_id)
    async with AsyncSessionLocal() as session, session.begin():
        existing = await session.execute(select(MemberModel.member_id).where(MemberModel.member_id.between(start_id, start_id + member_count - 1)))
        collisions = existing.scalars().all()
        if collisions:
            raise ValueError(f"Member IDs already exist: {collisions[:10]}. Use --reset-demo-data only to replace the tagged synthetic demo batch.")
        session.add_all(members + accounts + transactions + cards + loans + statements)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--members", type=int, default=DEFAULT_MEMBER_COUNT)
    parser.add_argument("--start-id", type=int, default=1000)
    parser.add_argument("--reset-demo-data", action="store_true", help="Remove only rows tagged as the APEX synthetic demo dataset before seeding")
    parser.add_argument("--confirm-demo-reset", action="store_true", help="Required with --reset-demo-data as an explicit destructive-operation confirmation")
    args = parser.parse_args()
    if args.reset_demo_data and not args.confirm_demo_reset:
        parser.error("--reset-demo-data also requires --confirm-demo-reset")
    if args.confirm_demo_reset and not args.reset_demo_data:
        parser.error("--confirm-demo-reset requires --reset-demo-data")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    await seed(args.members, args.start_id, args.reset_demo_data)
    print(f"Seeded {args.members} synthetic members, IDs {args.start_id}-{args.start_id + args.members - 1}; batch={SYNTHETIC_BATCH}; seed={SEED}.")
    print("Transaction amounts are synthetic historical samples; current account balances are independent snapshots.")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
