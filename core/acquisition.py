"""Bounded, explicit campaign labels for Telegram advertising links."""
import re


def start_source(text):
    # Only purpose-labelled campaign tokens; never export arbitrary start text.
    match = re.fullmatch(
        r"/start(?:@[A-Za-z0-9_]+)?\s+(src_[A-Za-z0-9_-]{1,60})\s*",
        str(text or ""),
    )
    return match.group(1) if match else None
