# -*- coding: utf-8 -*-
"""Browser utilities and helpers for ITS Calendar Scanner."""

from pydoll.browser.options import ChromiumOptions
from config import (
    CHROME_ARGUMENTS,
    BROWSER_START_TIMEOUT
)


def extract_script_value(result):
    """Extract value from script execution result.
    
    Handles both nested dict format and direct values.
    
    Args:
        result: Script execution result
        
    Returns:
        Extracted value or None
    """
    if isinstance(result, dict) and 'result' in result:
        nested = result['result']
        if isinstance(nested, dict) and 'result' in nested:
            return nested['result'].get('value')
        return nested.get('value')
    return str(result) if result else None


def create_browser_options(headless=False):
    """Create ChromiumOptions with specified configuration.
    
    Args:
        headless: Whether to run in headless mode
        
    Returns:
        ChromiumOptions instance
    """
    options = ChromiumOptions()
    
    if headless:
        options.headless = True
        print("→ Browser mode: Headless")
    else:
        print("→ Browser mode: Visible")
    
    for argument in CHROME_ARGUMENTS:
        options.add_argument(argument)
    
    options.start_timeout = BROWSER_START_TIMEOUT
    
    return options