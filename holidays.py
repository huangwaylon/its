# -*- coding: utf-8 -*-
"""Exceptional dates management for ITS Calendar Scanner.

This module contains the hardcoded list of exceptional dates to target for booking,
based on Japanese national holidays that create extended weekend breaks.

Booking Strategy:
- Friday holidays → Book the Friday (creates Fri-Sat-Sun break)
- Monday holidays → Book the Sunday before (creates Sun-Mon break)
- Tuesday holidays → Book the Monday before (creates Mon-Tue break, if Mon is not a holiday)
"""


# Exceptional dates to book for 2026
# These are manually curated dates that create extended weekend breaks
EXCEPTIONAL_DATES = {
    '2026-01-02': 'Apple Holiday Shutdown',
    '2026-01-11': 'Coming of Age Day',
    '2026-02-22': "Emperor's Birthday",
    '2026-03-20': 'Spring Equinox',
    '2026-05-03': 'Greenery Day',
    '2026-05-04': 'Childrens Day',
    '2026-05-05': 'Constitution Day',
    '2026-07-19': 'Marine Day',
    '2026-09-20': 'Respect-for-the-Aged Day',
    '2026-09-21': 'National Day',
    '2026-09-22': 'Autumn Equinox',
    '2026-10-11': 'Sports Day',
    '2026-11-22': 'Labour Thanksgiving Day',
}


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