# -*- coding: utf-8 -*-
"""Cache and persistence utilities for ITS Calendar Scanner."""

import json
import os
from config import CALENDAR_URL_CACHE, BOOKINGS_FILE


def load_cached_url():
    """Load the cached calendar URL if it exists.
    
    Returns:
        Cached URL string or None
    """
    if not os.path.exists(CALENDAR_URL_CACHE):
        return None
    
    try:
        with open(CALENDAR_URL_CACHE, 'r') as f:
            url = f.read().strip()
            return url if url else None
    except Exception as e:
        print(f"✗ Cache read error: {e}")
        return None


def save_calendar_url(url):
    """Save the calendar URL to cache file.
    
    Args:
        url: URL to cache
    """
    try:
        with open(CALENDAR_URL_CACHE, 'w') as f:
            f.write(url)
        print("✓ Calendar URL cached")
    except Exception as e:
        print(f"✗ Cache write error: {e}")


def load_bookings():
    """Load bookings history from JSON file.
    
    Returns:
        dict: {date: [hotel_names]}
    """
    if not os.path.exists(BOOKINGS_FILE):
        return {}
    
    try:
        with open(BOOKINGS_FILE, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except Exception as e:
        print(f"✗ Bookings load error: {e}")
        return {}


def save_booking(date, hotel_name):
    """Record a successful booking.
    
    Args:
        date: Date string (YYYY-MM-DD)
        hotel_name: Hotel name
    """
    try:
        bookings = load_bookings()
        
        if date not in bookings:
            bookings[date] = []
        
        if hotel_name not in bookings[date]:
            bookings[date].append(hotel_name)
            
            with open(BOOKINGS_FILE, 'w', encoding='utf-8') as f:
                json.dump(bookings, f, ensure_ascii=False, indent=2)
            
            print(f"✓ Booking recorded: {date} - {hotel_name}")
        else:
            print(f"⚠ Already recorded: {date} - {hotel_name}")
    except Exception as e:
        print(f"✗ Booking save error: {e}")


def is_already_booked(date, hotel_name):
    """Check if date/hotel is already booked.
    
    Args:
        date: Date string (YYYY-MM-DD)
        hotel_name: Hotel name
        
    Returns:
        bool: True if already booked
    """
    bookings = load_bookings()
    return date in bookings and hotel_name in bookings[date]


def get_booked_hotels_for_date(date):
    """Get list of hotels already booked for a date.
    
    Args:
        date: Date string (YYYY-MM-DD)
        
    Returns:
        list: Hotel names
    """
    bookings = load_bookings()
    return bookings.get(date, [])