from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import Page, sync_playwright


CHROME_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/147.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class AreaOption:
    areaCode: str
    label: str
    layerCodes: list[str]


@dataclass(frozen=True)
class IndicatorOption:
    indicator: str
    label: str


def _load_cookie_dict(path: str) -> dict[str, str]:
    with open(path, "r", encoding="utf-8") as f:
        d = json.load(f)
    return {str(k): str(v) for k, v in d.items() if v is not None}


def _add_cookies_to_context(context, cookies: dict[str, str]) -> None:
    # Use domain cookies for data.carss.cn
    context.add_cookies(
        [{"name": k, "value": v, "domain": "data.carss.cn", "path": "/"} for k, v in cookies.items()]
    )


def _open_dropdown_by_label(page: Page, label_text: str) -> None:
    """
    Open a dropdown by its visible label text.

    The CARSS site uses a component framework where the <label> can be present but
    not "visible" (e.g. floating labels). We therefore:
    - find a label containing the text
    - if it has a `for` attribute, click the associated input (force) and/or its field wrapper
    - fall back to clicking the label itself (force)
    """
    lbl = page.locator("label", has_text=label_text).first
    lbl.wait_for(state="attached", timeout=30_000)
    for_id = (lbl.get_attribute("for") or "").strip()
    def try_open() -> None:
        if for_id:
            inp = page.locator(f"#{for_id}").first
            if inp.count() > 0:
                try:
                    inp.click(force=True, timeout=5_000)
                    return
                except Exception:
                    pass
            # Click the closest field wrapper (Vuetify style)
            try:
                lbl.locator("xpath=ancestor::*[contains(@class,'v-field')][1]").click(force=True, timeout=5_000)
                return
            except Exception:
                pass
        # Fallback: click the label itself
        lbl.click(force=True, timeout=5_000)

    try_open()
    # Ensure the menu overlay opened.
    try:
        page.locator(".v-overlay-container .v-overlay__content").last.wait_for(state="visible", timeout=5_000)
    except Exception:
        # One retry
        try_open()
        page.locator(".v-overlay-container .v-overlay__content").last.wait_for(state="visible", timeout=10_000)


def _collect_visible_options(page: Page) -> list[tuple[str, str]]:
    """
    Return list of (value,label) pairs if possible. If value isn't present, return ("", label).
    Supports Ant Design option DOM.
    """
    # CARSS indicator page uses Vuetify menus (v-list-item) rather than AntD.
    opts = page.locator(".v-list-item")
    opts.first.wait_for(state="visible", timeout=10_000)
    out: list[tuple[str, str]] = []
    for i in range(opts.count()):
        o = opts.nth(i)
        # prefer dedicated title node to avoid extra whitespace
        title = o.locator(".v-list-item-title").first
        label = (title.inner_text() if title.count() else o.inner_text() or "").strip()
        if not label:
            continue
        value = (o.get_attribute("data-value") or o.get_attribute("value") or "").strip()
        out.append((value, label))
    # de-dup by label preserving order
    seen = set()
    uniq = []
    for v, l in out:
        if l in seen:
            continue
        seen.add(l)
        uniq.append((v, l))
    return uniq


def _collect_all_vuetify_options(page: Page, *, max_scrolls: int = 60) -> list[str]:
    """
    Collect all option labels from the currently open Vuetify menu by scrolling it.
    Returns labels in first-seen order.
    """
    # Menu content typically renders inside v-overlay-container
    menu = page.locator(".v-overlay-container .v-overlay__content").last
    menu.wait_for(state="visible", timeout=10_000)

    titles = page.locator(".v-overlay-container .v-list-item-title")
    seen: set[str] = set()
    out: list[str] = []

    def grab() -> None:
        for i in range(titles.count()):
            t = (titles.nth(i).inner_text() or "").strip()
            if t and t not in seen:
                seen.add(t)
                out.append(t)

    grab()

    # Scroll the menu container to reveal more options.
    for _ in range(max_scrolls):
        # scroll by a viewport
        page.evaluate(
            """(el) => { el.scrollTop = el.scrollTop + Math.max(200, el.clientHeight - 50); }""",
            menu.element_handle(),
        )
        page.wait_for_timeout(150)
        before = len(out)
        grab()
        # If no new items were discovered, try to detect end-of-scroll.
        if len(out) == before:
            at_end = page.evaluate(
                """(el) => (el.scrollTop + el.clientHeight) >= (el.scrollHeight - 2)""",
                menu.element_handle(),
            )
            if at_end:
                break

    return out


def _click_vuetify_option(page: Page, label: str, *, max_scrolls: int = 60) -> None:
    """
    Click a Vuetify menu option by label, scrolling the open menu until it appears.
    """
    menu = page.locator(".v-overlay-container .v-overlay__content").last
    menu.wait_for(state="visible", timeout=10_000)

    for _ in range(max_scrolls):
        opt = page.locator(".v-overlay-container .v-list-item-title", has_text=label).first
        if opt.count() > 0:
            opt.click(timeout=10_000)
            return
        # scroll further down
        page.evaluate(
            """(el) => { el.scrollTop = el.scrollTop + Math.max(240, el.clientHeight - 50); }""",
            menu.element_handle(),
        )
        page.wait_for_timeout(120)

    raise RuntimeError(f"Option not found in menu after scrolling: {label}")


def discover_options_via_ui(*, cookies_path: str) -> tuple[list[AreaOption], list[IndicatorOption]]:
    """
    Uses Playwright to load /publish/indicator and scrape dropdown option lists.
    Also captures a request payload to map:
    - area option -> areaCode + layerCode[]
    - indicator option -> indicator code
    """
    cookies = _load_cookie_dict(cookies_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=CHROME_UA,
            locale="en-US",
            viewport={"width": 1400, "height": 900},
        )
        _add_cookies_to_context(context, cookies)
        page = context.new_page()
        page.set_default_navigation_timeout(180_000)

        captured: list[dict[str, Any]] = []

        def on_request(req):
            if "/Api/Indicator/GetAll" in req.url and req.method.lower() == "post":
                try:
                    captured.append(req.post_data_json() or {})
                except Exception:
                    pass

        page.on("request", on_request)

        page.goto("https://data.carss.cn/publish/indicator", wait_until="load", timeout=180_000)
        page.wait_for_timeout(8_000)

        # If we landed on a WAF/challenge page, try a one-time reload after a short wait.
        try:
            html = page.content()
        except Exception:
            html = ""
        if "safeline" in html.lower() or "slg-title" in html.lower():
            page.wait_for_timeout(5_000)
            page.reload(wait_until="load", timeout=180_000)
            page.wait_for_timeout(5_000)

        # Discover indicator options (code) by selecting each visible label and reading the payload.
        _open_dropdown_by_label(page, "选择指标")
        ind_labels = _collect_all_vuetify_options(page)
        indicators: list[IndicatorOption] = []
        # Choose a stable area first (national)
        try:
            _open_dropdown_by_label(page, "选择地区")
            raw_areas = _collect_visible_options(page)
            # Click first area option to set context
            page.locator(".ant-select-item-option").first.click()
        except Exception:
            raw_areas = []

        for ind_label in ind_labels:
            # reopen dropdown each time
            _open_dropdown_by_label(page, "选择指标")
            # click option matching label
            _click_vuetify_option(page, ind_label)
            page.wait_for_timeout(500)
            # find last captured payload with indicator
            code = ""
            for pl in reversed(captured):
                if pl.get("indicator"):
                    code = str(pl.get("indicator"))
                    break
            if not code:
                continue
            indicators.append(IndicatorOption(indicator=code, label=ind_label))

        # Discover area options and their layerCode list by selecting each and reading payload.
        areas: list[AreaOption] = []
        if not raw_areas:
            _open_dropdown_by_label(page, "选择地区")
            area_labels = _collect_all_vuetify_options(page)
            raw_areas = [("", x) for x in area_labels]

        # Pick first indicator to ensure payload includes layerCode
        if indicators:
            _open_dropdown_by_label(page, "选择指标")
            page.get_by_text(indicators[0].label, exact=True).click()
            page.wait_for_timeout(500)

        for _, area_label in raw_areas:
            _open_dropdown_by_label(page, "选择地区")
            _click_vuetify_option(page, area_label)
            page.wait_for_timeout(800)
            ac = ""
            layer_codes: list[str] = []
            for pl in reversed(captured):
                if pl.get("areaCode") is not None and pl.get("layerCode") is not None:
                    ac = str(pl.get("areaCode"))
                    layer_codes = list(pl.get("layerCode") or [])
                    break
            if not ac:
                continue
            # Normalize layer codes to strings
            layer_codes = [str(x) for x in layer_codes if str(x)]
            # Dedup preserve
            seen = set()
            layer_codes = [c for c in layer_codes if not (c in seen or seen.add(c))]
            areas.append(AreaOption(areaCode=ac, label=area_label, layerCodes=layer_codes))

        # de-dup indicator codes
        ind_seen = set()
        indicators = [i for i in indicators if not (i.indicator in ind_seen or ind_seen.add(i.indicator))]
        area_seen = set()
        areas = [a for a in areas if not (a.areaCode in area_seen or area_seen.add(a.areaCode))]

        browser.close()

    # Some UIs may return empty values in data-value; ensure we have at least one.
    return areas, indicators


def post_indicator_getall_via_browser(
    *,
    cookies_path: str,
    payload: dict[str, Any],
    referer: str = "https://data.carss.cn/publish/indicator",
) -> dict[str, Any]:
    """
    Call /Api/Indicator/GetAll using Playwright's browser context request API.
    This reliably bypasses the Safeline WAF that blocks plain requests.
    """
    cookies = _load_cookie_dict(cookies_path)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            user_agent=CHROME_UA,
            locale="en-US",
            viewport={"width": 1400, "height": 900},
        )
        _add_cookies_to_context(context, cookies)
        page = context.new_page()
        page.set_default_navigation_timeout(180_000)

        # Visit the app once to let any WAF/session JS settle.
        page.goto("https://data.carss.cn/publish/indicator", wait_until="domcontentloaded", timeout=180_000)
        page.wait_for_timeout(2_000)

        # Now call API via the browser context.
        req = context.request
        resp = req.post(
            "https://data.carss.cn/Api/Indicator/GetAll",
            data=json.dumps(payload, ensure_ascii=False),
            headers={
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json",
                "Origin": "https://data.carss.cn",
                "Referer": referer,
                "apptype": "publish",
            },
        )
        if not resp.ok:
            raise RuntimeError(f"Indicator/GetAll failed HTTP {resp.status}: {resp.text()[:200]}")
        data = resp.json()
        browser.close()
        return data

