import os
import asyncio
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page
from backend.app.surfaces.base import ComputerSurface
from backend.app.core.logging import logger
from backend.app.safety.policy import default_safety_policy

class PlaywrightWebSurface(ComputerSurface):
    def __init__(self, headless: bool = True, page: Optional[Page] = None):
        self.headless = headless
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self.page: Optional[Page] = page
        self._is_external_page = page is not None
        self.last_failed_operation: Optional[str] = None
        self.last_operation: Optional[str] = None
        self.blocked_navigation_url: Optional[str] = None

    async def _guard_navigation(self, route, request) -> None:
        """Enforce the target policy on top-level navigations, including redirects."""
        frame = request.frame
        if request.is_navigation_request() and frame == frame.page.main_frame:
            if not default_safety_policy.validate_url(request.url):
                self.blocked_navigation_url = request.url
                await route.abort("blockedbyclient")
                logger.warning("Blocked browser navigation outside safety allowlist", host=urlparse(request.url).hostname or "unknown")
                return
        await route.continue_()

    def _action_allowed(self, action_type: str, selector: str = "", value: str = "") -> bool:
        current_url = self.page.url if self.page else ""
        allowed, reason = default_safety_policy.validate_action(action_type, current_url, selector, value)
        if not allowed:
            logger.warning("Browser surface blocked action by safety policy", action_type=action_type, decision="DENY", reason=reason[:180])
        return allowed

    async def connect(self, target_url: str) -> bool:
        if not default_safety_policy.validate_url(target_url):
            raise PermissionError("Target URL is outside the configured safety allowlist.")
        self.blocked_navigation_url = None
        if self.page and not self.page.is_closed():
            await self.page.context.route("**/*", self._guard_navigation)
            try:
                self.last_operation = "page.goto"
                await self.page.goto(target_url, wait_until="networkidle")
                if self.blocked_navigation_url:
                    raise PermissionError("Navigation redirect was blocked by the configured safety policy.")
            except Exception as exc:
                logger.error("Target navigation failed", operation="page.goto", error_type=type(exc).__name__)
                raise
            return True

        operation = "playwright_driver_start"
        self.last_failed_operation = None
        self.last_operation = operation
        try:
            self._playwright = await async_playwright().start()
            operation = "chromium_launch"
            self.last_operation = operation
            launch_args = [] if os.name == "nt" else ["--no-sandbox", "--disable-setuid-sandbox"]
            self._browser = await self._playwright.chromium.launch(headless=self.headless, args=launch_args)
            operation = "browser_context_create"
            self.last_operation = operation
            self._context = await self._browser.new_context(viewport={"width": 1280, "height": 800})
            self.page = await self._context.new_page()
            await self._context.route("**/*", self._guard_navigation)
            operation = "target_navigation"
            self.last_operation = operation
            await self.page.goto(target_url, wait_until="networkidle")
            if self.blocked_navigation_url:
                raise PermissionError("Navigation redirect was blocked by the configured safety policy.")
        except PermissionError as exc:
            self.last_failed_operation = operation
            logger.error(
                "Playwright startup permission denied",
                operation=operation,
                winerror=getattr(exc, "winerror", None),
                error_type=type(exc).__name__,
            )
            await self.close()
            raise
        except Exception as exc:
            self.last_failed_operation = operation
            logger.error("Playwright connection failed", operation=operation, error_type=type(exc).__name__)
            await self.close()
            raise
        logger.info("Playwright connected to target", url=target_url)
        return True

    async def navigate(self, url: str) -> bool:
        if not self.page:
            return False
        if not default_safety_policy.validate_url(url):
            raise PermissionError("Navigation target is outside the configured safety allowlist.")
        self.blocked_navigation_url = None
        self.last_operation = "page.goto"
        await self.page.goto(url, wait_until="networkidle")
        if self.blocked_navigation_url or not default_safety_policy.validate_url(self.page.url):
            raise PermissionError("Navigation or redirect was blocked by the configured safety policy.")
        return True

    async def click_approved_confirmation(self) -> bool:
        """Execute the sole human-approved simulator confirmation action."""
        if not self.page or not default_safety_policy.validate_url(self.page.url):
            return False
        selector = "#confirm-dialog-btn"
        try:
            self.last_operation = "approved_confirmation.click"
            locator = self.page.locator(selector).first
            await locator.wait_for(state="visible", timeout=1500)
            await locator.click()
            return True
        except Exception as exc:
            logger.warning("Approved confirmation control was unavailable", error_type=type(exc).__name__)
            return False

    async def observe(self) -> Dict[str, Any]:
        if not self.page:
            return {"error": "Page not connected"}

        self.last_operation = "page.title"
        url = self.page.url
        title = await self.page.title()

        # Extract sanitized interactive elements from DOM
        interactive_script = """
        () => {
            const elements = [];
            const sel = 'button, input, select, textarea, a, [role="button"], [id], .card-title, .alert-title, .acc-type, .acc-balance';
            document.querySelectorAll(sel).forEach((el, index) => {
                const rect = el.getBoundingClientRect();
                const visible = rect.width > 0 && rect.height > 0 && getComputedStyle(el).visibility !== 'hidden';
                if (!visible) return;

                const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim();
                const tag = el.tagName.toLowerCase();
                const id = el.id ? '#' + el.id : null;
                const name = el.getAttribute('name') ? `[name="${el.getAttribute('name')}"]` : null;
                const type = el.getAttribute('type');

                elements.push({
                    idx: index,
                    tag: tag,
                    id: el.id || null,
                    name: el.getAttribute('name') || null,
                    type: type || null,
                    role: el.getAttribute('role') || tag,
                    text: text.length > 100 ? text.substring(0, 100) + '...' : text,
                    primary_selector: id || name || (tag + (type ? `[type="${type}"]` : '') + (text ? `:has-text("${text}")` : '')),
                    aria_label: el.getAttribute('aria-label') || null
                });
            });
            return elements;
        }
        """
        self.last_operation = "page.evaluate_interactive_elements"
        elements = await self.page.evaluate(interactive_script)
        self.last_operation = "page.inner_text"
        page_text = await self.page.inner_text("body")

        return {
            "url": url,
            "title": title,
            "interactive_elements": elements,
            "page_text_summary": page_text[:800] if page_text else ""
        }

    async def locate(self, selectors: List[str]) -> Optional[str]:
        if not self.page:
            return None
        for sel in selectors:
            if not sel:
                continue
            try:
                self.last_operation = "locator.resolve"
                elem = self.page.locator(sel).first
                if await elem.count() > 0 and await elem.is_visible():
                    return sel
            except Exception:
                continue
        return None

    async def click(self, selector: str) -> bool:
        if not self.page:
            return False
        if not self._action_allowed("click", selector):
            return False
        try:
            self.last_operation = "locator.click"
            loc = self.page.locator(selector).first
            await loc.wait_for(state="visible", timeout=5000)
            await loc.click()
            await asyncio.sleep(0.5)
            return True
        except Exception as e:
            logger.warning("Click failed", selector=selector, error=str(e))
            return False

    async def fill(self, selector: str, value: str) -> bool:
        if not self.page:
            return False
        if not self._action_allowed("fill", selector, value):
            return False
        try:
            self.last_operation = "locator.fill"
            loc = self.page.locator(selector).first
            await loc.wait_for(state="visible", timeout=5000)
            await loc.fill(value)
            return True
        except Exception as e:
            logger.warning("Fill failed", selector=selector, error=str(e))
            return False

    async def select(self, selector: str, option: str) -> bool:
        if not self.page:
            return False
        if not self._action_allowed("select", selector, option):
            return False
        try:
            loc = self.page.locator(selector).first
            await loc.select_option(option)
            return True
        except Exception as e:
            logger.warning("Select failed", selector=selector, error=str(e))
            return False

    async def extract(self, selector: str, attribute: Optional[str] = None) -> Optional[str]:
        if not self.page:
            return None
        if not self._action_allowed("extract", selector):
            return None
        try:
            self.last_operation = "locator.extract"
            loc = self.page.locator(selector).first
            await loc.wait_for(state="visible", timeout=5000)
            if attribute:
                return await loc.get_attribute(attribute)
            else:
                text = await loc.inner_text()
                return text.strip() if text else None
        except Exception as e:
            logger.warning("Extract failed", selector=selector, error=str(e))
            return None

    async def capture_screenshot(self, filepath: str) -> str:
        if not self.page:
            return ""
        os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
        self.last_operation = "page.screenshot"
        await self.page.screenshot(path=filepath, full_page=False)
        return filepath

    async def current_url(self) -> str:
        return self.page.url if self.page else ""

    async def close(self) -> None:
        if self._is_external_page:
            return  # External page managed by session manager
        for operation, resource, method in (
            ("context_close", self._context, "close"),
            ("browser_close", self._browser, "close"),
            ("playwright_stop", self._playwright, "stop"),
        ):
            if resource is not None:
                try:
                    await getattr(resource, method)()
                except Exception as exc:
                    logger.warning("Playwright resource cleanup failed", operation=operation, error_type=type(exc).__name__)
        self.page = None
        self._context = None
        self._browser = None
        self._playwright = None
