# -*- coding: utf-8 -*-
"""Browser utilities and helpers for ITS Calendar Scanner."""

from pathlib import Path
from pydoll.browser.options import ChromiumOptions
from config import CHROME_ARGUMENTS, BROWSER_START_TIMEOUT


def extract_script_value(result):
    """Extract value from script execution result.

    Handles both nested dict format and direct values.

    Args:
        result: Script execution result

    Returns:
        Extracted value or None
    """
    if isinstance(result, dict) and "result" in result:
        nested = result["result"]
        if isinstance(nested, dict) and "result" in nested:
            return nested["result"].get("value")
        return nested.get("value")
    return str(result) if result else None


def create_browser_options(headless=False, fast=False):
    """Create ChromiumOptions with specified configuration.

    Args:
        headless: Whether to run in headless mode
        fast: If True, add performance optimizations (disable images)

    Returns:
        ChromiumOptions instance
    """
    options = ChromiumOptions()
    options.headless = headless

    for argument in CHROME_ARGUMENTS:
        options.add_argument(argument)

    if fast:
        # Disable GPU compositing and unnecessary features for speed
        options.add_argument("--disable-gpu")
        options.add_argument("--disable-software-rasterizer")

    options.start_timeout = BROWSER_START_TIMEOUT

    return options


async def setup_network_blocking(tab):
    """Block non-essential resource URLs via CDP Network.setBlockedURLs.

    Unlike Fetch domain interception, this does NOT pause requests or
    interfere with page load events. Blocked URLs silently fail.

    Args:
        tab: Browser tab instance
    """
    from pydoll.protocol.base import Command

    # Enable Network domain first
    await tab._execute_command(Command(method="Network.enable"))

    # Block common non-essential URL patterns
    await tab._execute_command(
        Command(
            method="Network.setBlockedURLs",
            params={
                "urls": [
                    "*.woff",
                    "*.woff2",
                    "*.ttf",
                    "*.eot",
                    "*.svg",
                    "*.ico",
                    "*google-analytics*",
                    "*googletagmanager*",
                    "*facebook*",
                    "*doubleclick*",
                ]
            },
        )
    )
