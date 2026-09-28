import os
import asyncio
import sys
import html
from datetime import date
from decimal import Decimal
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from backend.app.db.banking import get_member_detail, get_recent_transactions, mask_account_number, mask_email, mask_phone
from backend.app.db.database import AsyncSessionLocal
from backend.app.db.models import TransactionModel

app = FastAPI(title="APEX Federal Core Banking System v4.2")

# Simulator runtime state
sim_state = {
    "delay": False,
    "dialog": False
}

@app.get("/api/simulators")
async def get_simulators():
    return sim_state

@app.post("/api/simulators")
async def update_simulators(req: Request):
    data = await req.json()
    if "delay" in data:
        sim_state["delay"] = bool(data["delay"])
    if "dialog" in data:
        sim_state["dialog"] = bool(data["dialog"])
    return sim_state


def _money(value: Decimal | int | float) -> str:
    return f"${Decimal(value):,.2f}"


def _member_payload(member, transactions: list[TransactionModel]) -> dict:
    accounts = sorted(member.accounts, key=lambda account: (account.account_type, account.account_id))
    return {
        "member_id": member.member_id,
        "name": f"{member.first_name} {member.last_name}",
        "membership_status": member.membership_status,
        "membership_type": member.membership_type,
        "member_since": member.joined_at.isoformat(),
        "contact": {"email": mask_email(member.email), "phone": mask_phone(member.phone)},
        "address": member.address,
        "accounts": [{
            "account_id": account.account_id,
            "account_type": account.account_type,
            "account_number": mask_account_number(account.account_number),
            "balance": _money(account.balance),
            "currency": account.currency,
            "status": account.account_status,
            "opened_at": account.opened_at.isoformat(),
            "closed_at": account.closed_at.isoformat() if account.closed_at else None,
        } for account in accounts],
        "transactions": [{
            "transaction_reference": transaction.transaction_reference,
            "account_id": transaction.account_id,
            "type": transaction.transaction_type,
            "amount": _money(transaction.amount),
            "currency": transaction.currency,
            "description": transaction.description,
            "status": transaction.transaction_status,
            "date": transaction.transaction_date.isoformat(),
        } for transaction in transactions],
        "cards": [{
            "card_id": card.card_id,
            "number": card.masked_card_number,
            "type": card.card_type,
            "status": card.card_status,
            "expiry_date": card.expiry_date.isoformat(),
            "linked_account_id": card.linked_account_id,
        } for card in member.cards],
        "loans": [{
            "loan_reference": loan.loan_reference,
            "type": loan.loan_type,
            "principal_amount": _money(loan.principal_amount),
            "outstanding_balance": _money(loan.outstanding_balance),
            "interest_rate_percent": str(loan.interest_rate),
            "status": loan.loan_status,
            "next_payment_date": loan.next_payment_date.isoformat() if loan.next_payment_date else None,
            "installment_amount": _money(loan.installment_amount),
        } for loan in member.loans],
        "statements": [{
            "statement_id": statement.statement_id,
            "account_id": statement.account_id,
            "period_start": statement.period_start.isoformat(),
            "period_end": statement.period_end.isoformat(),
            "opening_balance": _money(statement.opening_balance),
            "closing_balance": _money(statement.closing_balance),
            "transaction_count": statement.transaction_count,
        } for account in accounts for statement in account.statements],
    }


@app.get("/api/members/{member_id}")
async def get_member_api(member_id: str):
    if not member_id.isdigit():
        raise HTTPException(status_code=422, detail="member_id must contain digits only")
    async with AsyncSessionLocal() as db:
        member = await get_member_detail(db, int(member_id))
        if not member:
            raise HTTPException(status_code=404, detail={"code": "MEMBER_NOT_FOUND", "message": "The requested member ID does not exist in the synthetic banking database."})
        transactions = await get_recent_transactions(db, int(member_id), 50)
        return _member_payload(member, transactions)

@app.get("/", response_class=HTMLResponse)
async def home_page():
    return """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>APEX Federal - Core Banking System v4.2</title>
    <style>
        :root {
            --bg-main: #0B132B;
            --bg-panel: #1C2541;
            --bg-card: #151E38;
            --primary: #00F2FE;
            --primary-hover: #00C6FF;
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --border: #334155;
            --danger: #EF4444;
            --warning: #F59E0B;
            --success: #10B981;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; }
        body { background-color: var(--bg-main); color: var(--text-main); min-height: 100vh; display: flex; flex-direction: column; }
        
        .header {
            background-color: var(--bg-panel);
            border-bottom: 1px solid var(--border);
            padding: 1rem 2rem;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .logo-group { display: flex; align-items: center; gap: 0.75rem; }
        .logo-icon { width: 32px; height: 32px; background: linear-gradient(135deg, var(--primary), #3B82F6); border-radius: 6px; display: grid; place-items: center; font-weight: bold; color: #0B132B; }
        .app-title { font-size: 1.15rem; font-weight: 700; letter-spacing: 0.5px; }
        .app-version { font-size: 0.75rem; color: var(--primary); background: rgba(0,242,254,0.1); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(0,242,254,0.3); }
        
        .simulators { display: flex; align-items: center; gap: 1rem; font-size: 0.85rem; background: rgba(0,0,0,0.2); padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border); }
        .sim-btn { background: var(--bg-main); border: 1px solid var(--border); color: var(--text-muted); padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 0.75rem; transition: all 0.2s; }
        .sim-btn.active { border-color: var(--primary); color: var(--primary); background: rgba(0,242,254,0.1); }

        .container { flex: 1; max-width: 900px; margin: 2rem auto; width: 100%; padding: 0 1.5rem; }
        
        .card { background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px; padding: 2rem; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3); }
        
        .card-title { font-size: 1.4rem; font-weight: 600; margin-bottom: 0.5rem; color: var(--text-main); }
        .card-desc { font-size: 0.9rem; color: var(--text-muted); margin-bottom: 1.5rem; }

        .form-group { margin-bottom: 1.5rem; }
        label { display: block; font-size: 0.85rem; font-weight: 600; text-transform: uppercase; letter-spacing: 0.05em; color: var(--text-muted); margin-bottom: 0.5rem; }
        input[type="text"] {
            width: 100%;
            padding: 0.85rem 1rem;
            background-color: var(--bg-main);
            border: 1px solid var(--border);
            border-radius: 6px;
            color: var(--text-main);
            font-size: 1rem;
            outline: none;
            transition: border-color 0.2s;
        }
        input[type="text"]:focus { border-color: var(--primary); box-shadow: 0 0 0 2px rgba(0,242,254,0.2); }

        .btn-primary {
            width: 100%;
            background: linear-gradient(135deg, #00C6FF, #0072FF);
            color: #ffffff;
            font-weight: 600;
            padding: 0.9rem 1.5rem;
            border: none;
            border-radius: 6px;
            font-size: 1rem;
            cursor: pointer;
            display: flex;
            justify-content: center;
            align-items: center;
            gap: 0.5rem;
            transition: opacity 0.2s;
        }
        .btn-primary:hover { opacity: 0.9; }

        .sample-ids { margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid var(--border); font-size: 0.85rem; color: var(--text-muted); }
        .sample-tag { background: rgba(255,255,255,0.05); color: var(--primary); padding: 2px 8px; border-radius: 4px; margin-left: 4px; font-family: monospace; cursor: pointer; }

        /* Modal / Dialog Simulator Overlay */
        .modal-overlay {
            display: none;
            position: fixed;
            top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0, 0, 0, 0.75);
            backdrop-filter: blur(4px);
            z-index: 9999;
            align-items: center;
            justify-content: center;
        }
        .modal-overlay.active { display: flex; }
        .modal-box {
            background: var(--bg-panel);
            border: 1px solid var(--warning);
            border-radius: 10px;
            width: 90%; max-width: 440px;
            padding: 1.75rem;
            box-shadow: 0 20px 40px rgba(0,0,0,0.6);
        }
        .modal-title { font-size: 1.2rem; font-weight: 700; color: var(--warning); margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem; }
        .modal-body { font-size: 0.95rem; color: var(--text-muted); margin-bottom: 1.5rem; line-height: 1.4; }
        .modal-actions { display: flex; gap: 0.75rem; justify-content: flex-end; }
        .btn-modal-cancel { background: var(--bg-main); border: 1px solid var(--border); color: var(--text-main); padding: 0.6rem 1.2rem; border-radius: 6px; cursor: pointer; }
        .btn-modal-confirm { background: var(--primary); border: none; color: #0B132B; font-weight: 700; padding: 0.6rem 1.2rem; border-radius: 6px; cursor: pointer; }
    </style>
</head>
<body>
    <div class="header">
        <div class="logo-group">
            <div class="logo-icon">AF</div>
            <div>
                <span class="app-title">APEX FEDERAL</span>
                <span class="app-version">Core Banking System v4.2</span>
            </div>
        </div>
        <div class="simulators">
            <span>⚙️ Simulators:</span>
            <button id="sim-delay-btn" class="sim-btn" onclick="toggleSim('delay')">Delay: OFF</button>
            <button id="sim-dialog-btn" class="sim-btn" onclick="toggleSim('dialog')">Dialog: OFF</button>
            <span style="color: var(--primary);">ENV: DEV-LOCAL</span>
        </div>
    </div>

    <div class="container">
        <div class="card">
            <h1 class="card-title">Member Search</h1>
            <p class="card-desc">Enter a Member ID to retrieve profile, accounts, and financial information.</p>
            
            <form id="search-form" onsubmit="handleSearch(event)">
                <div class="form-group">
                    <label for="member-id-input">Member ID</label>
                    <input type="text" id="member-id-input" name="member_id" placeholder="e.g. 1002" required aria-label="Member ID" autocomplete="off" />
                </div>
                <button type="submit" id="search-btn" name="search" class="btn-primary">
                    🔍 Search
                </button>
            </form>

            <div class="sample-ids">
                Demonstration Member IDs: 
                <span class="sample-tag" onclick="setMember('1001')">1001</span>
                <span class="sample-tag" onclick="setMember('1002')">1002</span>
                <span class="sample-tag" onclick="setMember('1003')">1003</span>
                <span class="sample-tag" onclick="setMember('1599')">1599</span>
                <span class="sample-tag" onclick="setMember('99999')">99999 (Not Found)</span>
            </div>
        </div>
    </div>

    <!-- Unexpected Confirmation Dialog (HITL Trigger) -->
    <div id="unexpected-dialog-modal" class="modal-overlay">
        <div class="modal-box">
            <div class="modal-title">⚠️ Confirm Action</div>
            <div class="modal-body">
                This action requires additional verification. Please confirm to continue searching member details.
            </div>
            <div class="modal-actions">
                <button id="cancel-dialog-btn" class="btn-modal-cancel" onclick="resolveDialog(false)">Cancel</button>
                <button id="confirm-dialog-btn" class="btn-modal-confirm" onclick="resolveDialog(true)">Confirm</button>
            </div>
        </div>
    </div>

    <script>
        let simState = { delay: false, dialog: false };

        async function fetchSims() {
            try {
                const res = await fetch('/api/simulators');
                simState = await res.json();
                updateSimUI();
            } catch(e){}
        }

        function updateSimUI() {
            const delayBtn = document.getElementById('sim-delay-btn');
            const dialogBtn = document.getElementById('sim-dialog-btn');
            delayBtn.innerText = 'Delay: ' + (simState.delay ? 'ON' : 'OFF');
            delayBtn.classList.toggle('active', simState.delay);
            dialogBtn.innerText = 'Dialog: ' + (simState.dialog ? 'ON' : 'OFF');
            dialogBtn.classList.toggle('active', simState.dialog);
        }

        async function toggleSim(key) {
            simState[key] = !simState[key];
            await fetch('/api/simulators', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(simState)
            });
            updateSimUI();
        }

        function setMember(id) {
            document.getElementById('member-id-input').value = id;
        }

        let pendingMemberId = null;

        async function handleSearch(e) {
            e.preventDefault();
            const memberId = document.getElementById('member-id-input').value.trim();
            if(!memberId) return;

            if (simState.dialog) {
                pendingMemberId = memberId;
                document.getElementById('unexpected-dialog-modal').classList.add('active');
                return;
            }

            proceedSearch(memberId);
        }

        async function resolveDialog(confirmed) {
            document.getElementById('unexpected-dialog-modal').classList.remove('active');
            if (confirmed && pendingMemberId) {
                proceedSearch(pendingMemberId);
            }
            pendingMemberId = null;
        }

        async function proceedSearch(memberId) {
            if (simState.delay) {
                await new Promise(r => setTimeout(r, 2000));
            }
            window.location.href = '/member/' + encodeURIComponent(memberId);
        }

        fetchSims();
    </script>
</body>
</html>"""

@app.get("/member/{member_id}", response_class=HTMLResponse)
async def member_details_page(member_id: str):
    if not member_id.isdigit():
        raise HTTPException(status_code=422, detail="member_id must contain digits only")
    async with AsyncSessionLocal() as db:
        record = await get_member_detail(db, int(member_id))
        recent_transactions = await get_recent_transactions(db, int(member_id), 20) if record else []
        payload = _member_payload(record, recent_transactions) if record else None
    if payload:
        member = {
            "member_id": str(payload["member_id"]),
            "name": payload["name"],
            "status": payload["membership_status"].replace("_", " ").title(),
            "member_since": date.fromisoformat(payload["member_since"]).strftime("%b %d, %Y"),
            "membership_type": payload["membership_type"].title(),
            "email": payload["contact"]["email"],
            "phone": payload["contact"]["phone"],
            "address": payload["address"],
            "accounts": [{
                "type": f"{item['account_type'].title()} Account",
                "account_type": item["account_type"],
                "number": item["account_number"],
                "balance": item["balance"],
                "status": item["status"].title(),
                "opened_at": date.fromisoformat(item["opened_at"]).strftime("%b %d, %Y"),
            } for item in payload["accounts"]],
            "transactions": payload["transactions"],
            "cards": payload["cards"],
            "loans": payload["loans"],
            "statements": payload["statements"],
        }
    else:
        member = None
    if not member:
        # Return Business Outcome: Member Not Found page
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Member Not Found - APEX Federal</title>
    <style>
        :root {{
            --bg-main: #0B132B;
            --bg-panel: #1C2541;
            --border: #334155;
            --danger: #EF4444;
            --warning: #F59E0B;
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --primary: #00F2FE;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }}
        body {{ background-color: var(--bg-main); color: var(--text-main); min-height: 100vh; }}
        .header {{ background-color: var(--bg-panel); border-bottom: 1px solid var(--border); padding: 1rem 2rem; display: flex; align-items: center; justify-content: space-between; }}
        .logo-group {{ display: flex; align-items: center; gap: 0.75rem; }}
        .logo-icon {{ width: 32px; height: 32px; background: linear-gradient(135deg, var(--primary), #3B82F6); border-radius: 6px; display: grid; place-items: center; font-weight: bold; color: #0B132B; }}
        .container {{ max-width: 800px; margin: 3rem auto; padding: 0 1.5rem; }}
        .back-link {{ display: inline-flex; align-items: center; gap: 0.5rem; color: var(--primary); text-decoration: none; margin-bottom: 1.5rem; font-size: 0.9rem; }}
        .alert-card {{ background: rgba(245, 158, 11, 0.08); border: 1px solid var(--warning); border-radius: 10px; padding: 2rem; text-align: left; }}
        .alert-title {{ font-size: 1.4rem; font-weight: 700; color: var(--warning); margin-bottom: 0.5rem; display: flex; align-items: center; gap: 0.5rem; }}
        .alert-subtitle {{ color: var(--text-muted); font-size: 1rem; margin-bottom: 1.5rem; }}
        .details-box {{ background: var(--bg-panel); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem; font-size: 0.9rem; line-height: 1.6; color: var(--text-main); }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo-group">
            <div class="logo-icon">AF</div>
            <span style="font-weight:700;">APEX FEDERAL - Core Banking System v4.2</span>
        </div>
    </div>
    <div class="container">
        <a href="/" class="back-link">← Back to Search</a>
        
        <div id="member-not-found-alert" class="alert-card">
            <div class="alert-title">
                ⚠️ Member not found
            </div>
            <div class="alert-subtitle">
                The requested member ID does not exist in the system.
            </div>
            
            <div class="details-box">
                <div><strong>Input Member ID:</strong> <span id="searched-member-id">{html.escape(member_id)}</span></div>
                <div><strong>Status Code:</strong> 404_MEMBER_NOT_FOUND</div>
                <div><strong>Message:</strong> No matching active record found for specified institution tenant.</div>
            </div>
        </div>
    </div>
</body>
</html>"""

    # Render only data persisted in the synthetic banking database.
    accounts_html = ""
    active_savings_count = sum(item["account_type"] == "SAVINGS" and item["status"] == "Active" for item in member["accounts"])
    savings_note = (
        '<div class="details-box" id="ambiguous-savings-accounts">Multiple active savings accounts are linked; specify an account to continue.</div>'
        if active_savings_count > 1 else
        '<div class="details-box" id="savings-account-unavailable">No active savings account is linked to this member.</div>'
        if active_savings_count == 0 else ""
    )
    savings_balance_added = False
    for acc in sorted(member["accounts"], key=lambda item: (item["status"] != "Active", item["account_type"], item["number"])):
        is_savings = acc["account_type"] == "SAVINGS" and active_savings_count == 1 and acc["status"] == "Active" and not savings_balance_added
        val_id = 'id="savings-balance-val"' if is_savings else ''
        savings_balance_added = savings_balance_added or is_savings
        accounts_html += f"""
        <div class="account-card">
            <div class="acc-info">
                <div class="acc-type">{html.escape(acc["type"])}</div>
                <div class="acc-num">{html.escape(acc["number"])} · Opened {html.escape(acc["opened_at"])}</div>
            </div>
            <div><div class="acc-balance" {val_id}>{html.escape(acc["balance"])}</div><div class="acc-num">{html.escape(acc["status"])}</div></div>
        </div>
        """
    if not accounts_html:
        accounts_html = '<div class="details-box" id="accounts-empty">No deposit accounts are linked to this member.</div>'

    def table_rows(rows: list[dict], columns: list[tuple[str, str]]) -> str:
        if not rows:
            return '<tr><td colspan="8">No records available for this member.</td></tr>'
        return "".join("<tr>" + "".join(f"<td>{html.escape(str(row.get(key) or "—"))}</td>" for key, _ in columns) + "</tr>" for row in rows)

    transaction_columns = [("date", "Date"), ("type", "Type"), ("description", "Description"), ("amount", "Amount"), ("status", "Status"), ("transaction_reference", "Reference")]
    card_columns = [("number", "Card"), ("type", "Type"), ("status", "Status"), ("expiry_date", "Expires"), ("linked_account_id", "Linked account")]
    loan_columns = [("loan_reference", "Loan"), ("type", "Type"), ("status", "Status"), ("outstanding_balance", "Balance"), ("installment_amount", "Installment"), ("next_payment_date", "Next payment")]
    statement_columns = [("period_start", "From"), ("period_end", "Through"), ("opening_balance", "Opening balance"), ("closing_balance", "Closing balance"), ("transaction_count", "Transactions")]

    def table_markup(rows: list[dict], columns: list[tuple[str, str]]) -> str:
        header = "".join(f"<th>{label}</th>" for _, label in columns)
        return f'<div class="details-box" style="overflow-x:auto"><table><thead><tr>{header}</tr></thead><tbody>{table_rows(rows, columns)}</tbody></table></div>'

    transaction_markup = table_markup(member["transactions"], transaction_columns)
    cards_markup = table_markup(member["cards"], card_columns)
    loans_markup = table_markup(member["loans"], loan_columns)
    statements_markup = table_markup(member["statements"], statement_columns)
    latest_deposit = next((item for item in member["transactions"] if item["type"] == "DEPOSIT"), None)
    latest_deposit_markup = (
        f'<div class="details-box" id="latest-deposit">Latest deposit: {html.escape(latest_deposit["amount"])} · {html.escape(latest_deposit["date"])} · {html.escape(latest_deposit["description"])}</div>'
        if latest_deposit else '<div class="details-box" id="latest-deposit">No posted deposits found.</div>'
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Member Details - {html.escape(member["name"])} - APEX Federal</title>
    <style>
        :root {{
            --bg-main: #0B132B;
            --bg-panel: #1C2541;
            --bg-card: #151E38;
            --border: #334155;
            --primary: #00F2FE;
            --text-main: #F8FAFC;
            --text-muted: #94A3B8;
            --success: #10B981;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Segoe UI', system-ui, sans-serif; }}
        body {{ background-color: var(--bg-main); color: var(--text-main); min-height: 100vh; }}
        .header {{ background-color: var(--bg-panel); border-bottom: 1px solid var(--border); padding: 1rem 2rem; display: flex; align-items: center; justify-content: space-between; }}
        .logo-group {{ display: flex; align-items: center; gap: 0.75rem; }}
        .logo-icon {{ width: 32px; height: 32px; background: linear-gradient(135deg, var(--primary), #3B82F6); border-radius: 6px; display: grid; place-items: center; font-weight: bold; color: #0B132B; }}
        
        .container {{ max-width: 900px; margin: 2rem auto; padding: 0 1.5rem; }}
        .back-link {{ display: inline-flex; align-items: center; gap: 0.5rem; color: var(--primary); text-decoration: none; margin-bottom: 1.5rem; font-size: 0.9rem; }}
        .back-link:hover {{ text-decoration: underline; }}
        
        .profile-card {{ background: var(--bg-panel); border: 1px solid var(--border); border-radius: 10px; padding: 1.75rem; margin-bottom: 1.5rem; }}
        .profile-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 1rem; }}
        .member-name {{ font-size: 1.5rem; font-weight: 700; color: var(--text-main); }}
        .member-id-badge {{ font-size: 0.85rem; color: var(--text-muted); margin-top: 0.25rem; font-family: monospace; }}
        .status-badge {{ background: rgba(16, 185, 129, 0.15); color: var(--success); border: 1px solid rgba(16, 185, 129, 0.3); padding: 4px 12px; border-radius: 20px; font-size: 0.8rem; font-weight: 600; }}

        .meta-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; padding-top: 1rem; border-top: 1px solid var(--border); margin-top: 1rem; }}
        .meta-item label {{ display: block; font-size: 0.75rem; color: var(--text-muted); text-transform: uppercase; margin-bottom: 0.25rem; }}
        .meta-item span {{ font-size: 0.95rem; font-weight: 600; }}

        .tabs {{ display: flex; gap: 0.5rem; border-bottom: 1px solid var(--border); margin-bottom: 1.5rem; }}
        .tab-btn {{ padding: 0.75rem 1.25rem; background: none; border: none; border-bottom: 2px solid transparent; color: var(--text-muted); font-weight: 600; cursor: pointer; font-size: 0.95rem; }}
        .tab-btn.active {{ color: var(--primary); border-bottom-color: var(--primary); }}

        .accounts-list {{ display: flex; flex-direction: column; gap: 1rem; }}
        .account-card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 1.25rem 1.5rem; display: flex; justify-content: space-between; align-items: center; transition: border-color 0.2s; }}
        .account-card:hover {{ border-color: rgba(0,242,254,0.4); }}
        .acc-type {{ font-size: 1.05rem; font-weight: 600; color: var(--text-main); }}
        .acc-num {{ font-size: 0.85rem; color: var(--text-muted); font-family: monospace; margin-top: 0.25rem; }}
        .acc-balance {{ font-size: 1.3rem; font-weight: 700; color: var(--primary); font-family: 'Consolas', monospace; }}
        .data-panel[hidden] {{ display: none; }}
        .data-table {{ width: 100%; border-collapse: collapse; text-align: left; }}
        .data-table th, .data-table td {{ padding: 0.7rem; border-bottom: 1px solid var(--border); font-size: 0.85rem; }}
        .data-table th {{ color: var(--text-muted); text-transform: uppercase; font-size: 0.72rem; }}
        .profile-lines {{ display: grid; gap: 0.8rem; }}
    </style>
</head>
<body>
    <div class="header">
        <div class="logo-group">
            <div class="logo-icon">AF</div>
            <span style="font-weight:700;">APEX FEDERAL - Core Banking System v4.2</span>
        </div>
    </div>

    <div class="container">
        <a href="/" class="back-link">← Back to Search</a>

        <div class="profile-card">
            <div class="profile-header">
                <div>
                    <h1 class="member-name" id="member-name-val">{html.escape(member["name"])}</h1>
                    <div class="member-id-badge">Member ID: <span id="member-id-val">{html.escape(member["member_id"])}</span></div>
                </div>
                <span class="status-badge">{html.escape(member["status"])}</span>
            </div>
            
            <div class="meta-grid">
                <div class="meta-item">
                    <label>Institution</label>
                    <span>APEX Federal CU</span>
                </div>
                <div class="meta-item">
                    <label>Member Since</label>
                    <span>{html.escape(member["member_since"])}</span>
                </div>
                <div class="meta-item">
                    <label>Membership type</label>
                    <span style="color: var(--success);">{html.escape(member["membership_type"])}</span>
                </div>
            </div>
        </div>

        <div class="tabs" role="tablist" aria-label="Member banking details">
            <button class="tab-btn active" role="tab" aria-selected="true" data-panel-target="accounts-panel">Accounts</button>
            <button class="tab-btn" role="tab" aria-selected="false" data-panel-target="profile-panel">Profile</button>
            <button class="tab-btn" role="tab" aria-selected="false" data-panel-target="transactions-panel">Transactions</button>
            <button class="tab-btn" role="tab" aria-selected="false" data-panel-target="cards-panel">Cards</button>
            <button class="tab-btn" role="tab" aria-selected="false" data-panel-target="loans-panel">Loans</button>
            <button class="tab-btn" role="tab" aria-selected="false" data-panel-target="statements-panel">Statements</button>
        </div>

        <section id="accounts-panel" class="data-panel" role="tabpanel">
            {savings_note}
            <div class="accounts-list">{accounts_html}</div>
        </section>
        <section id="profile-panel" class="data-panel" role="tabpanel" hidden>
            <div class="details-box profile-lines">
                <div><strong>Membership status:</strong> <span id="membership-status">{html.escape(member["status"])}</span></div>
                <div><strong>Membership type:</strong> {html.escape(member["membership_type"])}</div>
                <div><strong>Email:</strong> <span id="member-email">{html.escape(member["email"])}</span></div>
                <div><strong>Phone:</strong> <span id="member-phone">{html.escape(member["phone"])}</span></div>
                <div><strong>Mailing address:</strong> <span id="member-address">{html.escape(member["address"])}</span></div>
            </div>
        </section>
        <section id="transactions-panel" class="data-panel" role="tabpanel" hidden>
            <h2 class="card-title">Recent transactions</h2>
            {latest_deposit_markup}
            {transaction_markup}
        </section>
        <section id="cards-panel" class="data-panel" role="tabpanel" hidden>
            <h2 class="card-title">Member cards</h2>
            {cards_markup}
        </section>
        <section id="loans-panel" class="data-panel" role="tabpanel" hidden>
            <h2 class="card-title">Member loans</h2>
            {loans_markup}
        </section>
        <section id="statements-panel" class="data-panel" role="tabpanel" hidden>
            <h2 class="card-title">Statement summaries</h2>
            {statements_markup}
        </section>
    </div>
    <script>
        document.querySelectorAll('.tab-btn').forEach(button => button.addEventListener('click', () => {{
            document.querySelectorAll('.tab-btn').forEach(tab => {{ tab.classList.remove('active'); tab.setAttribute('aria-selected', 'false'); }});
            document.querySelectorAll('.data-panel').forEach(panel => panel.hidden = true);
            button.classList.add('active');
            button.setAttribute('aria-selected', 'true');
            document.getElementById(button.dataset.panelTarget).hidden = false;
        }}));
    </script>
</body>
</html>"""

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=3001)
