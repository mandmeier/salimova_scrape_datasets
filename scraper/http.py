from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

import requests


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 6
    min_sleep_s: float = 0.5
    max_sleep_s: float = 10.0
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)


def make_session(
    *,
    timeout_s: float = 30.0,
    headers: Mapping[str, str] | None = None,
    user_agent: str | None = None,
) -> requests.Session:
    sess = requests.Session()
    base_headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if user_agent:
        base_headers["User-Agent"] = user_agent
    if headers:
        base_headers.update(dict(headers))
    sess.headers.update(base_headers)

    # attach default timeout on the session object
    sess.request = _wrap_request_with_timeout(sess.request, timeout_s)  # type: ignore[method-assign]
    return sess


def _wrap_request_with_timeout(request_fn: Callable[..., requests.Response], timeout_s: float):
    def wrapped(method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", timeout_s)
        return request_fn(method, url, **kwargs)

    return wrapped


def load_cookies_json(sess: requests.Session, cookies_json_path: str) -> None:
    """
    Load cookies exported as a simple JSON dict {name: value}.
    """
    with open(cookies_json_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("cookies json must be an object mapping cookieName -> cookieValue")
    sess.cookies.update({str(k): str(v) for k, v in data.items()})


def request_json_with_retries(
    sess: requests.Session,
    method: str,
    url: str,
    *,
    retry: RetryConfig | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    retry = retry or RetryConfig()
    attempt = 0
    sleep_s = retry.min_sleep_s

    while True:
        attempt += 1
        try:
            resp = sess.request(method, url, **kwargs)
        except requests.RequestException as e:
            if attempt >= retry.max_attempts:
                raise RuntimeError(f"Request failed after {attempt} attempts: {e}") from e
            time.sleep(sleep_s)
            sleep_s = min(retry.max_sleep_s, sleep_s * 2)
            continue

        if resp.status_code in retry.retry_statuses:
            if attempt >= retry.max_attempts:
                raise RuntimeError(
                    f"HTTP {resp.status_code} after {attempt} attempts. Body (first 500 chars): {resp.text[:500]}"
                )
            time.sleep(sleep_s)
            sleep_s = min(retry.max_sleep_s, sleep_s * 2)
            continue

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}. Body (first 500 chars): {resp.text[:500]}")

        try:
            return resp.json()
        except ValueError as e:
            raise RuntimeError(f"Non-JSON response. Body (first 500 chars): {resp.text[:500]}") from e


def request_bytes_with_retries(
    sess: requests.Session,
    method: str,
    url: str,
    *,
    retry: RetryConfig | None = None,
    **kwargs: Any,
) -> bytes:
    retry = retry or RetryConfig()
    attempt = 0
    sleep_s = retry.min_sleep_s

    while True:
        attempt += 1
        try:
            resp = sess.request(method, url, **kwargs)
        except requests.RequestException as e:
            if attempt >= retry.max_attempts:
                raise RuntimeError(f"Request failed after {attempt} attempts: {e}") from e
            time.sleep(sleep_s)
            sleep_s = min(retry.max_sleep_s, sleep_s * 2)
            continue

        if resp.status_code in retry.retry_statuses:
            if attempt >= retry.max_attempts:
                raise RuntimeError(
                    f"HTTP {resp.status_code} after {attempt} attempts. Body (first 200 chars): {resp.text[:200]}"
                )
            time.sleep(sleep_s)
            sleep_s = min(retry.max_sleep_s, sleep_s * 2)
            continue

        if resp.status_code != 200:
            raise RuntimeError(f"HTTP {resp.status_code}. Body (first 200 chars): {resp.text[:200]}")

        return resp.content

