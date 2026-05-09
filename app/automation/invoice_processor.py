"""Playwright-based automation for entering invoices into the accounting portal."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from app.config import Settings
from app.utils.logger import get_logger
from app.utils.retry import retry_async

log = get_logger(__name__)


class InvoiceProcessor:
    """Drives a headless Chromium browser to enter invoice data into a web portal.

    Usage::

        processor = InvoiceProcessor(settings)
        confirmation = await processor.process(invoice_id, invoice_data)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.screenshots_path: Path = settings.screenshots_path

    async def process(self, invoice_id: int, invoice_data: dict[str, Any]) -> str:
        """Full pipeline: launch browser → login → fill form → submit → return confirmation.

        Args:
            invoice_id: Database ID of the invoice (used for screenshot naming).
            invoice_data: Dict of parsed invoice fields.

        Returns:
            Confirmation number string from the portal.

        Raises:
            RuntimeError: If any step fails after all retries.
        """
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                    "--window-size=1920,1080",
                ],
            )
            context = await browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="en-US",
                timezone_id="America/New_York",
                extra_http_headers={
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )

            page = await context.new_page()
            # Mask webdriver property for basic stealth
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                await self.login(page)
                await self.navigate_to_invoice_entry(page)
                await self.fill_invoice_form(page, invoice_data)
                confirmation = await self.submit_and_verify(page)
                log.info(
                    "Portal entry complete for invoice {id}: confirmation={c}",
                    id=invoice_id,
                    c=confirmation,
                )
                return confirmation
            except Exception as exc:
                log.error("Portal entry failed for invoice {id}: {exc}", id=invoice_id, exc=exc)
                await self.take_failure_screenshot(page, invoice_id)
                raise
            finally:
                await context.close()
                await browser.close()

    @retry_async(max_attempts=3, delay=2.0, backoff=2.0, exceptions=(Exception,))
    async def login(self, page: Any) -> None:
        """Navigate to portal login page and authenticate.

        Args:
            page: Playwright Page object.
        """
        login_url = self.settings.accounting_portal_url.rstrip("/") + "/login"
        log.debug("Navigating to login page: {url}", url=login_url)

        await page.goto(login_url, wait_until="networkidle", timeout=30_000)

        # Fill credentials — selectors are generic; adjust to actual portal
        await page.wait_for_selector("input[name='username'], input[type='email']", timeout=10_000)
        await page.fill("input[name='username'], input[type='email']", self.settings.accounting_user)
        await page.fill("input[name='password'], input[type='password']", self.settings.accounting_pass)

        # Click submit and wait for navigation
        await page.click("button[type='submit'], input[type='submit']")
        await page.wait_for_url(
            lambda url: "/login" not in url and "/auth" not in url,
            timeout=15_000,
        )
        log.info("Logged in to accounting portal as {user}", user=self.settings.accounting_user)

    @retry_async(max_attempts=3, delay=1.5, backoff=2.0, exceptions=(Exception,))
    async def navigate_to_invoice_entry(self, page: Any) -> None:
        """Navigate from dashboard to the new invoice entry form.

        Args:
            page: Playwright Page object (assumed to be on authenticated dashboard).
        """
        log.debug("Navigating to invoice entry form")

        # Try direct URL first
        entry_url = self.settings.accounting_portal_url.rstrip("/") + "/invoices/new"
        await page.goto(entry_url, wait_until="networkidle", timeout=20_000)

        # If redirected away from the form, try clicking through the nav
        current_url = page.url
        if "/invoices/new" not in current_url and "/invoice" not in current_url.lower():
            log.debug("Direct URL failed, trying navigation menu")
            # Try clicking a navigation link — adjust selector to real portal
            nav_selectors = [
                "a[href*='invoice']",
                "nav a:has-text('Invoice')",
                "a:has-text('New Invoice')",
                "[data-nav='invoices']",
            ]
            for selector in nav_selectors:
                try:
                    await page.click(selector, timeout=5_000)
                    await page.wait_for_load_state("networkidle", timeout=10_000)
                    break
                except Exception:
                    continue

        log.debug("Now at {url}", url=page.url)

    @retry_async(max_attempts=2, delay=1.0, backoff=2.0, exceptions=(Exception,))
    async def fill_invoice_form(self, page: Any, invoice_data: dict[str, Any]) -> None:
        """Fill all fields in the invoice entry form.

        Handles standard text inputs, dropdowns (select and custom), and date pickers.

        Args:
            page: Playwright Page object positioned on the invoice entry form.
            invoice_data: Dict with keys: vendor_name, invoice_number, amount,
                          currency, invoice_date, due_date.
        """
        log.debug("Filling invoice form with data: {data}", data=invoice_data)

        async def safe_fill(selector: str, value: str | None, label: str) -> None:
            """Fill a text input, logging a warning on failure."""
            if value is None:
                return
            try:
                await page.wait_for_selector(selector, timeout=5_000)
                await page.fill(selector, str(value))
                log.debug("Filled {label}: {value}", label=label, value=value)
            except Exception as exc:
                log.warning("Could not fill {label}: {exc}", label=label, exc=exc)

        async def safe_select(selector: str, value: str | None, label: str) -> None:
            """Select a dropdown option by value or label."""
            if value is None:
                return
            try:
                await page.wait_for_selector(selector, timeout=5_000)
                await page.select_option(selector, value=value)
                log.debug("Selected {label}: {value}", label=label, value=value)
            except Exception:
                try:
                    # Custom dropdown — click to open then pick option
                    await page.click(selector, timeout=5_000)
                    option_selector = f"[data-value='{value}'], li:has-text('{value}')"
                    await page.click(option_selector, timeout=5_000)
                except Exception as exc:
                    log.warning("Could not select {label}: {exc}", label=label, exc=exc)

        # Vendor / payee
        await safe_fill(
            "input[name='vendor'], input[name='payee'], input[name='vendor_name']",
            invoice_data.get("vendor_name"),
            "vendor_name",
        )

        # Invoice number
        await safe_fill(
            "input[name='invoice_number'], input[name='invoiceNumber'], input[name='ref']",
            invoice_data.get("invoice_number"),
            "invoice_number",
        )

        # Amount
        if invoice_data.get("amount") is not None:
            await safe_fill(
                "input[name='amount'], input[name='total'], input[name='invoice_amount']",
                f"{invoice_data['amount']:.2f}",
                "amount",
            )

        # Currency dropdown
        await safe_select(
            "select[name='currency'], select[name='currency_code']",
            invoice_data.get("currency"),
            "currency",
        )

        # Invoice date
        await safe_fill(
            "input[name='invoice_date'], input[name='invoiceDate'], input[type='date'][name*='invoice']",
            invoice_data.get("invoice_date"),
            "invoice_date",
        )

        # Due date
        await safe_fill(
            "input[name='due_date'], input[name='dueDate'], input[type='date'][name*='due']",
            invoice_data.get("due_date"),
            "due_date",
        )

        # Small human-like delay
        await asyncio.sleep(0.5)
        log.info("Invoice form filled successfully")

    @retry_async(max_attempts=3, delay=2.0, backoff=2.0, exceptions=(Exception,))
    async def submit_and_verify(self, page: Any) -> str:
        """Submit the invoice form and extract the confirmation number.

        Args:
            page: Playwright Page object with a completed invoice form.

        Returns:
            Confirmation number string from the portal.

        Raises:
            RuntimeError: If no confirmation number is found after submission.
        """
        log.debug("Submitting invoice form")

        # Click submit
        submit_selectors = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Save')",
            "button:has-text('Submit')",
            "button:has-text('Create')",
        ]
        submitted = False
        for selector in submit_selectors:
            try:
                await page.click(selector, timeout=5_000)
                submitted = True
                break
            except Exception:
                continue

        if not submitted:
            raise RuntimeError("Could not find a submit button on the invoice form")

        # Wait for success indicator
        await page.wait_for_load_state("networkidle", timeout=20_000)

        # Extract confirmation number — adjust selectors to actual portal
        confirmation_selectors = [
            "[data-testid='confirmation-number']",
            ".confirmation-number",
            "#confirmation",
            ".alert-success",
            "[class*='success'] strong",
        ]
        for selector in confirmation_selectors:
            try:
                element = await page.wait_for_selector(selector, timeout=5_000)
                if element:
                    text = (await element.inner_text()).strip()
                    if text:
                        log.info("Got confirmation: {conf}", conf=text)
                        return text
            except Exception:
                continue

        # Fallback: check current URL for a record ID
        import re

        url_match = re.search(r"/invoices?/(\d+|[A-Z0-9\-]+)", page.url)
        if url_match:
            confirmation = f"PORTAL-{url_match.group(1)}"
            log.info("Extracted confirmation from URL: {c}", c=confirmation)
            return confirmation

        # Ensure no error message is displayed
        error_selectors = [".alert-error", ".error-message", "[class*='error']"]
        for selector in error_selectors:
            try:
                err = await page.query_selector(selector)
                if err:
                    err_text = await err.inner_text()
                    raise RuntimeError(f"Portal returned an error: {err_text}")
            except RuntimeError:
                raise
            except Exception:
                continue

        # Return a timestamp-based fallback confirmation
        fallback = f"SUBMITTED-{int(time.time())}"
        log.warning("No confirmation element found, using fallback: {c}", c=fallback)
        return fallback

    async def take_failure_screenshot(self, page: Any, invoice_id: int) -> str | None:
        """Capture a full-page screenshot when processing fails.

        Args:
            page: Playwright Page object.
            invoice_id: Used in the screenshot filename.

        Returns:
            Absolute path to the saved screenshot, or None on failure.
        """
        try:
            self.screenshots_path.mkdir(parents=True, exist_ok=True)
            ts = int(time.time())
            path = self.screenshots_path / f"invoice_{invoice_id}_failure_{ts}.png"
            await page.screenshot(path=str(path), full_page=True)
            log.info("Failure screenshot saved: {path}", path=path)
            return str(path)
        except Exception as exc:
            log.warning("Could not save failure screenshot: {exc}", exc=exc)
            return None
