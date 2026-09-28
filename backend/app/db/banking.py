"""Read-only repository functions for the synthetic banking application."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from backend.app.db.models import AccountModel, MemberModel, TransactionModel


async def get_member_detail(db: AsyncSession, member_id: int) -> MemberModel | None:
    result = await db.execute(
        select(MemberModel)
        .where(MemberModel.member_id == member_id)
        .options(
            selectinload(MemberModel.accounts).selectinload(AccountModel.transactions),
            selectinload(MemberModel.accounts).selectinload(AccountModel.statements),
            selectinload(MemberModel.cards),
            selectinload(MemberModel.loans),
        )
    )
    return result.scalar_one_or_none()


async def get_recent_transactions(db: AsyncSession, member_id: int, limit: int = 3) -> list[TransactionModel]:
    result = await db.execute(
        select(TransactionModel)
        .join(AccountModel, TransactionModel.account_id == AccountModel.account_id)
        .where(AccountModel.member_id == member_id)
        .order_by(TransactionModel.transaction_date.desc())
        .limit(max(1, min(limit, 50)))
    )
    return list(result.scalars().all())


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[:1]}***@{domain}" if domain else "***"


def mask_phone(phone: str) -> str:
    digits = "".join(character for character in phone if character.isdigit())
    return f"***-***-{digits[-4:]}" if len(digits) >= 4 else "***"


def mask_account_number(account_number: str) -> str:
    return f"**** {account_number[-4:]}"
