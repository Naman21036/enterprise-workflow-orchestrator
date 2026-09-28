from decimal import Decimal

from scripts.seed_database import DEFAULT_MEMBER_COUNT, _build_rows


def test_default_seed_has_six_hundred_sequential_members_and_varied_data():
    assert DEFAULT_MEMBER_COUNT == 600
    members, accounts, transactions, cards, loans, statements = _build_rows(DEFAULT_MEMBER_COUNT, 1000)

    assert len(members) == 600
    assert [member.member_id for member in members] == list(range(1000, 1600))
    assert len(accounts) > 600
    assert len(transactions) > len(accounts)
    assert cards and loans and statements

    member_ids = {member.member_id for member in members}
    account_ids = {account.account_id for account in accounts}
    assert all(account.member_id in member_ids for account in accounts)
    assert all(transaction.account_id in account_ids for transaction in transactions)
    assert all(card.member_id in member_ids for card in cards)
    assert all(card.linked_account_id is None or card.linked_account_id in account_ids for card in cards)
    assert all(loan.member_id in member_ids for loan in loans)
    assert all(statement.account_id in account_ids for statement in statements)

    assert len({account.account_number for account in accounts}) == len(accounts)
    assert len({transaction.transaction_reference for transaction in transactions}) == len(transactions)
    assert len({card.masked_card_number for card in cards}) == len(cards)
    assert len({loan.loan_reference for loan in loans}) == len(loans)
    assert any(not any(account.member_id == member.member_id for account in accounts) for member in members)
    assert any(sum(account.member_id == member.member_id and account.account_type == "SAVINGS" for account in accounts) > 1 for member in members)
    assert all(transaction.amount > 0 if transaction.transaction_type == "DEPOSIT" else transaction.amount < 0 for transaction in transactions)
    assert all(loan.outstanding_balance == Decimal("0.00") for loan in loans if loan.loan_status == "PAID")


def test_seed_generation_is_reproducible_and_keeps_demo_replay_fixture():
    first = _build_rows(5, 1000)
    second = _build_rows(5, 1000)
    first_members, first_accounts, first_transactions, *_ = first
    second_members, second_accounts, second_transactions, *_ = second

    assert [(item.member_id, item.first_name, item.last_name, item.email) for item in first_members] == [
        (item.member_id, item.first_name, item.last_name, item.email) for item in second_members
    ]
    assert [(item.account_id, item.account_number, item.balance) for item in first_accounts] == [
        (item.account_id, item.account_number, item.balance) for item in second_accounts
    ]
    assert [(item.transaction_reference, item.amount, item.transaction_date) for item in first_transactions] == [
        (item.transaction_reference, item.amount, item.transaction_date) for item in second_transactions
    ]
    assert (first_members[2].first_name, first_members[2].last_name) == ("John", "Doe")
    savings = next(account for account in first_accounts if account.member_id == 1002 and account.account_type == "SAVINGS")
    assert savings.balance == Decimal("7250.00")
