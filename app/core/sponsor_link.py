"""Sponsor link and channel identifier parsing module."""

from __future__ import annotations

import re

# Regex patterns for t.me links, usernames, and numeric chat IDs.
#
# The leading "+" of a private invite link is kept *inside* the capture group:
# callers distinguish an invite hash from a public username by that prefix
# (see app.admin.sponsor_router.process_sponsor_input), so dropping it would
# make a private link look like a resolvable @username.
TME_PATTERN = re.compile(r"(?:https?://)?t\.me/(?:joinchat/)?(\+?[a-zA-Z0-9_-]+)")
USERNAME_PATTERN = re.compile(r"^@?([a-zA-Z0-9_]{5,32})$")
NUMERIC_ID_PATTERN = re.compile(r"^-?\d+$")


def parse_sponsor_input(user_input: str) -> tuple[str | None, int | None]:
    """Parse user input into (username_or_link_hash, numeric_chat_id).

    Examples:
    - "@mychannel" -> ("mychannel", None)
    - "https://t.me/mychannel" -> ("mychannel", None)
    - "-1001234567890" -> (None, -1001234567890)
    - "https://t.me/+AbCdEf" -> ("+AbCdEf", None)
    """
    raw = user_input.strip()

    if NUMERIC_ID_PATTERN.match(raw):
        try:
            return None, int(raw)
        except ValueError:
            pass

    tme_match = TME_PATTERN.search(raw)
    if tme_match:
        extracted = tme_match.group(1)
        return extracted, None

    usr_match = USERNAME_PATTERN.match(raw)
    if usr_match:
        return usr_match.group(1), None

    return None, None
