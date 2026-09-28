import os
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.core.config import settings
from backend.app.db.database import get_db

router = APIRouter()


def _mistral_configured() -> bool:
    key = (settings.MISTRAL_API_KEY or "").strip()
    return bool(key and "your_mistral_api_key" not in key.lower())


def _chromium_installation_present() -> bool:
    configured_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured_path and configured_path != "0":
        browser_root = Path(configured_path).expanduser()
    elif os.name == "nt":
        browser_root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "ms-playwright"
    else:
        browser_root = Path.home() / ".cache" / "ms-playwright"
    try:
        return any(browser_root.glob("chromium-*/chrome-win*/chrome.exe")) or any(browser_root.glob("chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe")) or any(browser_root.glob("chromium-*/chrome-linux/chrome"))
    except OSError:
        return False


async def _readiness(db: AsyncSession) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        database_status = "healthy"
    except Exception:
        database_status = "unhealthy"

    target_status = "offline"
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(settings.TARGET_APP_URL)
        if response.status_code < 500:
            target_status = "online"
    except httpx.HTTPError:
        pass

    chromium_installed = _chromium_installation_present()
    core_ready = database_status == "healthy" and target_status == "online" and chromium_installed
    mistral_configured = _mistral_configured()
    return {
        "status": "ready" if core_ready else "degraded",
        "api_process": "live",
        "database": database_status,
        "target_application": {"url": settings.TARGET_APP_URL, "status": target_status},
        "browser_automation": {
            "engine": "Playwright Chromium",
            "status": "installed" if chromium_installed else "missing",
            "launch_permission": "not_probed",
        },
        "mistral_ai": {
            "configured": mistral_configured,
            "status": "configured" if mistral_configured else "configuration_required_for_discovery",
            "model": settings.MISTRAL_MODEL,
        },
    }


@router.get("/health/live")
async def check_liveness():
    return {"status": "live", "api_process": "running"}


@router.get("/health/ready")
async def check_readiness(db: AsyncSession = Depends(get_db)):
    return await _readiness(db)


@router.get("/health")
async def check_health(db: AsyncSession = Depends(get_db)):
    details = await _readiness(db)
    return {
        **details,
        "system": "APEX Automation Computer Use Engine",
        "version": "1.0.0",
        # Browser installation and service connectivity are required for replay;
        # Mistral is only required for new discovery.
        "status": "healthy" if details["status"] == "ready" else "degraded",
    }
