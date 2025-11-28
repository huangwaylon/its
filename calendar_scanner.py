# -*- coding: utf-8 -*-
"""Calendar scanning and date processing for ITS Calendar Scanner."""

import asyncio
import re
from datetime import datetime
from browser import extract_script_value
from config import (
    TAG_TD,
    TAG_PARAGRAPH,
    TAG_INPUT,
    CLASS_MONTH,
    ATTR_DATA_JOIN_TIME,
    ID_NEXT_MONTH,
    TEXT_NEXT_MONTH,
    TARGET_WEEKDAYS,
    WEEKDAY_NAMES,
    ICON_AVAILABLE,
    STATUS_AVAILABLE,
    STATUS_FULL,
    STATUS_UNKNOWN,
    SLEEP_MONTH_NAV,
    SLEEP_SHORT,
    DEFAULT_TIMEOUT,
    TEXT_TRUNCATE_LENGTH
)


def is_target_weekday(date_string):
    """Check if date matches target weekdays.
    
    Args:
        date_string: Date in format 'YYYY-MM-DD'
        
    Returns:
        tuple: (is_match, weekday_name) or (False, None)
    """
    try:
        date_obj = datetime.strptime(date_string, '%Y-%m-%d')
        weekday = date_obj.weekday()
        
        if weekday in TARGET_WEEKDAYS:
            return True, WEEKDAY_NAMES[weekday]
        return False, None
    except:
        return False, None


async def navigate_to_next_month(tab):
    """Navigate to next month in calendar.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        bool: True if successful
    """
    current_month = None
    try:
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            current_month = extract_script_value(text_result)
    except:
        pass
    
    clicked = False
    try:
        next_button = await tab.find(id=ID_NEXT_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if next_button:
            await next_button.click()
            clicked = True
    except:
        pass
    
    if not clicked:
        try:
            inputs = await tab.find(tag_name=TAG_INPUT, find_all=True, timeout=DEFAULT_TIMEOUT, raise_exc=False)
            if inputs:
                for input_elem in inputs:
                    try:
                        value = await input_elem.get_property("value")
                        if value and TEXT_NEXT_MONTH in value:
                            await input_elem.click()
                            clicked = True
                            break
                    except:
                        pass
        except:
            pass
    
    if not clicked:
        return False
    
    await asyncio.sleep(SLEEP_MONTH_NAV)
    
    # Verify month changed
    if current_month:
        try:
            month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
            if month_element:
                text_result = await month_element.execute_script("return this.textContent")
                new_month = extract_script_value(text_result)
                if new_month != current_month:
                    return True
                else:
                    print(f"⚠ Month unchanged: {new_month}")
                    return False
        except:
            pass
    
    return True


async def scan_month_days(tab):
    """Scan current month for available days on target weekdays.
    
    Args:
        tab: Browser tab instance
        
    Returns:
        list: Available day info dicts
    """
    try:
        month_element = await tab.find(class_name=CLASS_MONTH, timeout=DEFAULT_TIMEOUT, raise_exc=False)
        if month_element:
            text_result = await month_element.execute_script("return this.textContent")
            current_month = extract_script_value(text_result) or STATUS_UNKNOWN
        else:
            current_month = STATUS_UNKNOWN
    except Exception as e:
        current_month = STATUS_UNKNOWN
    
    print(f"→ Scanning: {current_month}")
    
    available_days = []
    
    try:
        all_cells = await tab.find(tag_name=TAG_TD, find_all=True, raise_exc=False)
        if not all_cells:
            print("  No date cells found")
            return available_days
        
        # Filter to cells with data-join-time
        date_cells = []
        for cell in all_cells:
            try:
                attr_result = await cell.execute_script(f"return this.getAttribute('{ATTR_DATA_JOIN_TIME}')")
                date_attr = extract_script_value(attr_result)
                
                if date_attr and date_attr not in ['None', None]:
                    date_cells.append((cell, date_attr))
            except:
                pass
        
        if not date_cells:
            print("  No valid date cells")
            return available_days
        
        # Filter to target weekdays
        target_cells = []
        for cell, date_str in date_cells:
            is_match, day_name = is_target_weekday(date_str)
            if is_match:
                target_cells.append((cell, date_str, day_name))
        
        target_day_names = [WEEKDAY_NAMES[wd] for wd in TARGET_WEEKDAYS]
        days_str = ", ".join(target_day_names)
        print(f"  Found {len(target_cells)} {days_str} date(s)")
        
        # Process each target cell
        for cell, full_date, day_name in target_cells:
            try:
                # Get date text
                date_text = ""
                date_elem = await cell.find(tag_name=TAG_PARAGRAPH, raise_exc=False)
                if date_elem:
                    text_result = await date_elem.execute_script("return this.textContent.trim()")
                    date_text = extract_script_value(text_result) or ''
                
                if not date_text:
                    text_result = await cell.execute_script("return this.textContent.trim()")
                    cell_text = extract_script_value(text_result) or ''
                    match = re.search(r'(\d+)', cell_text)
                    if match:
                        date_text = match.group(1)
                
                # Get availability
                text_result = await cell.execute_script("return this.textContent.trim()")
                cell_text = extract_script_value(text_result) or ''
                
                if ICON_AVAILABLE in cell_text or "○" in cell_text:
                    icon = ICON_AVAILABLE
                    status = STATUS_AVAILABLE
                elif "☓" in cell_text or "×" in cell_text or "X" in cell_text:
                    icon = "×"
                    status = STATUS_FULL
                else:
                    icon = ""
                    status = STATUS_UNKNOWN
                
                print(f"  {date_text}日 ({day_name}): {icon} ({status})")
                
                if icon == ICON_AVAILABLE:
                    available_days.append({
                        'month': current_month,
                        'date': date_text,
                        'day_name': day_name,
                        'full_date': full_date,
                        'icon': icon,
                    })
            except Exception as e:
                print(f"  ✗ Cell error: {str(e)[:TEXT_TRUNCATE_LENGTH]}")
    
    except Exception as e:
        print(f"✗ Scan error: {e}")
    
    return available_days