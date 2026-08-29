"""
Phase V: Anti-bot and JS-rendered scraping helper via Playwright Async.

Provides reusable, robust browser session management with:
- Realistic desktop User-Agent and viewport configuration
- Disabled automation indicators (navigator.webdriver)
- Bounded concurrency with asyncio.Semaphore
- Automated error recovery and context cleanup
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from playwright.async_api import Browser, BrowserContext, Playwright, async_playwright

logger = logging.getLogger("frontieratlas.scrapers.playwright")

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


class PlaywrightCrawler:
    def __init__(self, concurrency: int = 3, headless: bool = True):
        self.concurrency = concurrency
        self.headless = headless
        self._pw: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._sem = asyncio.Semaphore(concurrency)

    async def __aenter__(self) -> "PlaywrightCrawler":
        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(
            headless=self.headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
            ],
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def fetch_html(
        self,
        url: str,
        wait_selector: Optional[str] = None,
        timeout_ms: int = 30000,
    ) -> Optional[str]:
        """Fetch rendered HTML from a JS-heavy or protected URL."""
        if not self._browser:
            raise RuntimeError("PlaywrightCrawler not started via async context manager")

        async with self._sem:
            context: BrowserContext = await self._browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1280, "height": 800},
                locale="en-US",
                timezone_id="America/New_York",
            )
            # Remove navigator.webdriver detection
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                if wait_selector:
                    try:
                        await page.wait_for_selector(wait_selector, timeout=5000)
                    except Exception:
                        pass
                content = await page.content()
                return content
            except Exception as e:
                logger.warning("Playwright fetch failed for %s: %s", url, e)
                return None
            finally:
                await page.close()
                await context.close()
