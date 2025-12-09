# -*- coding: utf-8 -*-
"""Exceptional dates management for ITS Calendar Scanner.

This module provides helper functions to work with exceptional dates
(Japanese national holidays that create extended weekend breaks).
The actual list of dates is configured in config.py.
"""

from config import EXCEPTIONAL_DATES


def is_exceptional_date(date_str: str) -> bool:
    """Check if date is an exceptional date that should be booked.
    
    Args:
        date_str: Date in 'YYYY-MM-DD' format
        
    Returns:
        bool: True if this is an exceptional date to book
    """
    return date_str in EXCEPTIONAL_DATES


def get_exceptional_date_reason(date_str: str) -> str:
    """Get the holiday name for an exceptional date.
    
    Args:
        date_str: Date in 'YYYY-MM-DD' format
        
    Returns:
        str: Holiday name, or empty string if not exceptional
    """
    return EXCEPTIONAL_DATES.get(date_str, '')