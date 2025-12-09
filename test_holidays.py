#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test script for holiday booking logic."""

from holidays import (
    EXCEPTIONAL_DATES,
    is_exceptional_date,
    get_exceptional_date_reason
)


def test_exceptional_dates_structure():
    """Test that exceptional dates are properly built."""
    print("\n" + "="*60)
    print("Testing Exceptional Dates Structure")
    print("="*60)
    
    # Should have exactly 11 exceptional dates
    expected_count = 11
    actual_count = len(EXCEPTIONAL_DATES)
    
    print(f"  Total exceptional dates: {actual_count}")
    print(f"  Expected: {expected_count}")
    print(f"  Match: {'✓' if actual_count == expected_count else '✗'}")
    
    assert actual_count == expected_count, f"Expected {expected_count} dates, got {actual_count}"
    print("  ✓ Structure test passed")


def test_friday_exceptional_dates():
    """Test Friday holidays are in exceptional dates."""
    print("\n" + "="*60)
    print("Testing Friday Exceptional Dates")
    print("="*60)
    
    # These Fridays should be in exceptional dates
    friday_dates = [
        ('2026-01-02', 'Apple Holiday Shutdown'),
        ('2026-03-20', 'Spring Equinox'),
    ]
    
    for date, expected_name in friday_dates:
        is_exceptional = is_exceptional_date(date)
        holiday_name = get_exceptional_date_reason(date)
        print(f"  {date}: {is_exceptional} - {holiday_name} {'✓' if is_exceptional else '✗'}")
        assert is_exceptional, f"{date} should be exceptional"
        assert holiday_name == expected_name, f"Expected '{expected_name}', got '{holiday_name}'"
    
    print("  ✓ All Friday exceptional dates passed")


def test_sunday_monday_exceptional_dates():
    """Test Sundays and Mondays before Monday/Tuesday holidays are in exceptional dates."""
    print("\n" + "="*60)
    print("Testing Sunday/Monday Exceptional Dates")
    print("="*60)
    
    # These dates (before Monday or Tuesday holidays) should be in exceptional dates
    dates_to_test = [
        ('2026-01-11', 'Coming of Age Day'),
        ('2026-02-22', "Emperor's Birthday"),
        ('2026-05-03', 'Greenery Day / Constitution Day'),
        ('2026-07-19', 'Marine Day'),
        ('2026-08-10', 'Mountain Day'),
        ('2026-09-20', 'Respect-for-the-Aged Day'),
        ('2026-10-11', 'Sports Day'),
        ('2026-11-02', 'Culture Day'),
        ('2026-11-22', 'Labour Thanksgiving Day'),
    ]
    
    for date, expected_name in dates_to_test:
        is_exceptional = is_exceptional_date(date)
        holiday_name = get_exceptional_date_reason(date)
        print(f"  {date}: {is_exceptional} - {holiday_name} {'✓' if is_exceptional else '✗'}")
        assert is_exceptional, f"{date} should be exceptional"
        assert holiday_name == expected_name, f"Expected '{expected_name}', got '{holiday_name}'"
    
    print("  ✓ All Sunday/Monday exceptional dates passed")


def test_non_exceptional_dates():
    """Test that non-exceptional dates return correct values."""
    print("\n" + "="*60)
    print("Testing Non-Exceptional Dates")
    print("="*60)
    
    non_exceptional = [
        '2026-01-15',  # Random Thursday
        '2026-06-20',  # Random Saturday
        '2026-01-12',  # Monday holiday itself (not Sunday before)
    ]
    
    for date in non_exceptional:
        is_exceptional = is_exceptional_date(date)
        holiday_name = get_exceptional_date_reason(date)
        print(f"  {date}: {is_exceptional} - '{holiday_name}' {'✓' if not is_exceptional else '✗'}")
        assert not is_exceptional, f"{date} should not be exceptional"
        assert holiday_name == '', f"Should return empty string, got '{holiday_name}'"
    
    print("  ✓ All non-exceptional date tests passed")


def test_display_all_exceptional_dates():
    """Display all exceptional dates in a nice format."""
    print("\n" + "="*60)
    print("All Exceptional Dates for 2026")
    print("="*60)
    
    print(f"\n  Total exceptional dates: {len(EXCEPTIONAL_DATES)}")
    print("\n  Date        | Holiday")
    print("  " + "-"*70)
    
    for date in sorted(EXCEPTIONAL_DATES.keys()):
        holiday_name = EXCEPTIONAL_DATES[date]
        print(f"  {date} | {holiday_name}")
    
    print(f"\n  ✓ Total: {len(EXCEPTIONAL_DATES)} exceptional dates")


def main():
    """Run all tests."""
    print("\n" + "="*60)
    print("EXCEPTIONAL DATES TEST SUITE")
    print("="*60)
    
    try:
        test_exceptional_dates_structure()
        test_friday_exceptional_dates()
        test_sunday_monday_exceptional_dates()
        test_non_exceptional_dates()
        test_display_all_exceptional_dates()
        
        print("\n" + "="*60)
        print("✓✓✓ ALL TESTS PASSED ✓✓✓")
        print("="*60)
        print("\nSummary:")
        print("  - Exceptional dates hardcoded and ready")
        print("  - Friday holidays included directly")
        print("  - Sundays before Monday holidays included")
        print("  - Mondays before Tuesday holidays included")
        print("  - Simple API: just check if date is exceptional")
        print(f"  - {len(EXCEPTIONAL_DATES)} total exceptional dates for 2026")
        print("="*60 + "\n")
        
    except AssertionError as e:
        print(f"\n✗✗✗ TEST FAILED: {e} ✗✗✗\n")
        raise


if __name__ == "__main__":
    main()