"""Instagram cookie parsing, normalization, and health check service."""

from __future__ import annotations

import requests

from app.core.logging import get_logger

log = get_logger(__name__)

WARP_PROXY = "socks5h://warp:1080"
IG_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def parse_and_normalize_cookies(raw_content: str) -> tuple[str, dict[str, str]]:
    """Parse cookie text (Netscape format or key-value) and return (netscape_text, cookie_dict)."""
    cookie_dict: dict[str, str] = {}
    is_already_netscape = False

    lines = raw_content.strip().splitlines()
    for line in lines:
        line_s = line.strip()
        if line_s.startswith("# Netscape") or line_s.startswith("# HTTP Cookie"):
            is_already_netscape = True
            continue
        if not line_s or line_s.startswith("#"):
            continue

        parts = line.split("\t")
        if len(parts) >= 7:
            is_already_netscape = True
            name = parts[5].strip()
            val = parts[6].strip()
            if name:
                cookie_dict[name] = val
        elif "=" in line_s:
            # Handle key=val; format
            for segment in line_s.split(";"):
                if "=" in segment:
                    k, v = segment.split("=", 1)
                    k_clean = k.strip()
                    v_clean = v.strip()
                    if k_clean:
                        cookie_dict[k_clean] = v_clean

    if is_already_netscape and "sessionid" in cookie_dict:
        # Already good Netscape format, ensure header is clean
        netscape_lines = ["# Netscape HTTP Cookie File", "# https://curl.se/docs/http-cookies.html"]
        for line in lines:
            if line.strip() and not line.strip().startswith("#"):
                netscape_lines.append(line)
        return "\n".join(netscape_lines) + "\n", cookie_dict

    # Construct clean Netscape format from dictionary
    netscape_lines = [
        "# Netscape HTTP Cookie File",
        "# https://curl.se/docs/http-cookies.html",
    ]
    for k, v in cookie_dict.items():
        domain = ".instagram.com"
        netscape_lines.append(f"{domain}\tTRUE\t/\tTRUE\t1999999999\t{k}\t{v}")

    return "\n".join(netscape_lines) + "\n", cookie_dict


def check_instagram_cookie_health_sync(
    cookie_dict: dict[str, str],
) -> tuple[bool, str | None, str | None]:
    """Synchronous health check for Instagram cookies.

    Returns (is_valid, account_id, error_message).
    """
    if "sessionid" not in cookie_dict:
        return (
            False,
            None,
            "کوکی فاقد پارامتر sessionid (شناسه ورود) است.",
        )

    account_id = cookie_dict.get("ds_user_id")

    proxies = {"https": WARP_PROXY, "http": WARP_PROXY}
    headers = {
        "User-Agent": IG_USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        r = requests.get(
            "https://www.instagram.com/",
            headers=headers,
            cookies=cookie_dict,
            proxies=proxies,
            allow_redirects=False,
            timeout=10,
        )
        set_cookie = r.headers.get("Set-Cookie", "")
        # If Instagram responds with sessionid=deleted, the session is dead
        if "sessionid=deleted" in set_cookie:
            return (
                False,
                account_id,
                "نشست اینستاگرام منقضی شده است (sessionid نامعتبر).",
            )

        if r.status_code == 302:
            loc = r.headers.get("Location", "")
            if "accounts/login" in loc.lower():
                return (
                    False,
                    account_id,
                    "هدایت به صفحه ورود (نیاز به لاگین مجدد).",
                )

        if r.status_code == 429:
            return (
                False,
                account_id,
                "محدودیت موقت نرخ درخواست (Rate Limit 429).",
            )

        return (True, account_id, None)

    except requests.exceptions.RequestException as exc:
        log.warning("ig_cookie_check_failed", error=str(exc))
        return (False, account_id, f"خطای ارتباط با اینستاگرام: {str(exc)[:60]}")
