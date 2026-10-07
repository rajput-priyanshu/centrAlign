"""Thin wrapper around Playwright giving the agent a small, robust action surface:
navigate / click / type / read / screenshot. Click and type resolve human-readable
descriptions (e.g. "Submit invoice" or "Invoice number") against the real DOM using
several heuristics, the same way a human would locate things by what they say, not
by CSS selector.
"""
import base64
import os
import time

from playwright.sync_api import sync_playwright


class ToolError(Exception):
    """Raised when a browser action cannot be carried out - fed back to the agent
    as a tool error so it can decide whether to retry, try an alternative, or ask
    for help, rather than crashing the whole run."""


class BrowserSession:
    def __init__(self, evidence_dir, headless=True):
        self.evidence_dir = evidence_dir
        os.makedirs(evidence_dir, exist_ok=True)
        self._playwright = sync_playwright().start()
        self.browser = self._playwright.chromium.launch(headless=headless)
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.page.set_default_timeout(5000)
        self._shot_count = 0

    def close(self):
        try:
            self.browser.close()
        finally:
            self._playwright.stop()

    # -- actions -----------------------------------------------------------

    def navigate(self, url):
        try:
            self.page.goto(url, wait_until="load")
        except Exception as e:
            raise ToolError(f"Failed to navigate to {url}: {e}")

    def click(self, description):
        locators = [
            lambda: self.page.get_by_role("link", name=description, exact=False),
            lambda: self.page.get_by_role("button", name=description, exact=False),
            lambda: self.page.get_by_text(description, exact=False),
        ]
        for build in locators:
            try:
                loc = build()
                if loc.count() > 0:
                    loc.first.scroll_into_view_if_needed()
                    loc.first.click(timeout=4000)
                    self.page.wait_for_load_state("load", timeout=4000)
                    return
            except Exception:
                continue
        raise ToolError(
            f"Could not find a clickable link/button/text matching {description!r} "
            f"on the current page. Use browser_read to see what is actually on the page."
        )

    def type(self, field, text, submit_label=None):
        locators = [
            lambda: self.page.get_by_label(field, exact=False),
            lambda: self.page.get_by_placeholder(field, exact=False),
            lambda: self.page.locator(f"[name='{field}']"),
            lambda: self.page.locator(f"#{field}"),
        ]
        filled = False
        for build in locators:
            try:
                loc = build()
                if loc.count() > 0:
                    loc.first.fill(text, timeout=4000)
                    filled = True
                    break
            except Exception:
                continue
        if not filled:
            raise ToolError(
                f"Could not find an input/textarea matching field {field!r}. "
                f"Use browser_read to see the actual field labels/names on the page."
            )
        if submit_label:
            self.click(submit_label)

    def observe(self, max_chars=3500):
        """Returns a text snapshot of the page (visible text + interactive elements)
        plus a screenshot, so the agent can decide its next action."""
        time.sleep(0.15)
        text = self.page.inner_text("body") if self.page.locator("body").count() else ""
        text = " ".join(text.split())
        if len(text) > max_chars:
            text = text[:max_chars] + " …[truncated]"

        links = self.page.locator("a").all_inner_texts()
        buttons = self.page.locator("button").all_inner_texts()
        inputs = self.page.locator("input, textarea").evaluate_all(
            "els => els.map(e => ({name: e.name, placeholder: e.placeholder, type: e.type}))"
        )

        shot_path = self._screenshot()
        with open(shot_path, "rb") as f:
            shot_b64 = base64.b64encode(f.read()).decode("ascii")

        return {
            "url": self.page.url,
            "title": self.page.title(),
            "visible_text": text,
            "links": [l.strip() for l in links if l.strip()][:30],
            "buttons": [b.strip() for b in buttons if b.strip()][:20],
            "input_fields": inputs[:20],
            "screenshot_path": shot_path,
            "screenshot_b64": shot_b64,
        }

    def _screenshot(self):
        self._shot_count += 1
        path = os.path.join(self.evidence_dir, f"{self._shot_count:03d}.png")
        self.page.screenshot(path=path)
        return path
