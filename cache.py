# -*- coding: utf-8 -*-
"""Cache and persistence utilities for ITS Calendar Scanner."""

import csv
import json
import os
from datetime import datetime
from config import (
    CALENDAR_URL_CACHE,
    CALENDAR_URL_HISTORY,
    BOOKINGS_FILE,
    LOG_SUCCESS,
    LOG_ERROR,
    LOG_WARNING
)


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
        print(f"{LOG_ERROR} Cache read error: {e}")
        return None


def save_calendar_url(url):
    """Save the calendar URL to cache file and log to history CSV.
    
    Args:
        url: URL to cache
    """
    try:
        # Check if URL is different from cached URL
        cached_url = load_cached_url()
        is_new_url = cached_url != url
        
        # Save to cache file
        with open(CALENDAR_URL_CACHE, 'w') as f:
            f.write(url)
        print(f"{LOG_SUCCESS} Calendar URL cached")
        
        # Append to CSV history only if URL is new
        if is_new_url:
            _append_url_to_history(url)
            
    except Exception as e:
        print(f"{LOG_ERROR} Cache write error: {e}")


def _append_url_to_history(url):
    """Append URL to history CSV file with timestamp.
    
    Args:
        url: URL to log
    """
    try:
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        file_exists = os.path.exists(CALENDAR_URL_HISTORY)
        
        with open(CALENDAR_URL_HISTORY, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            # Write header if file is new
            if not file_exists:
                writer.writerow(['url', 'timestamp'])
            writer.writerow([url, timestamp])
        
        print(f"{LOG_SUCCESS} URL logged: {timestamp}")
    except Exception as e:
        print(f"{LOG_ERROR} History log error: {e}")


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
        print(f"{LOG_ERROR} Bookings load error: {e}")
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
            
            print(f"{LOG_SUCCESS} Booking recorded: {date} - {hotel_name}")
        else:
            print(f"{LOG_WARNING} Already recorded: {date} - {hotel_name}")
    except Exception as e:
        print(f"{LOG_ERROR} Booking save error: {e}")


def get_booked_hotels_for_date(date):
    """Get list of hotels already booked for a date.
    
    Args:
        date: Date string (YYYY-MM-DD)
        
    Returns:
        list: Hotel names
    """
    bookings = load_bookings()
    return bookings.get(date, [])