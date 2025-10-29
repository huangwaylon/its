# -*- coding: utf-8 -*-
from DrissionPage import ChromiumPage, ChromiumOptions
import time

CHROME_ARGUMENTS = [
    "-no-first-run",
    "-force-color-profile=srgb",
    "-metrics-recording-only",
    "-password-store=basic",
    "-use-mock-keychain",
    "-export-tagged-pdf",
    "-no-default-browser-check",
    "-disable-background-mode",
    "-enable-features=NetworkService,NetworkServiceInProcess",
    "-disable-features=FlashDeprecationWarning",
    "-deny-permission-prompts",
    "-accept-lang=ja-JP",
    "--disable-usage-stats",
    "--disable-crash-reporter",
]

options = ChromiumOptions()
options.set_browser_path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
for argument in CHROME_ARGUMENTS:
    options.set_argument(argument)

driver = ChromiumPage(addr_or_opts=options)

try:
    # Navigate directly to the calendar page (you need a valid URL)
    test_url = "https://as.its-kenpo.or.jp/calendar_apply/calendar_select?s=PU16TTBVRE8wRWpOM0VUUHpWbWNwQkhlbDlWZW1sbWNsWm5KeDBEWnA5VmV5OTJabFJYWWo5VlpqbG1keVYyYw%3D%3D"
    
    print("Navigating to calendar page...")
    driver.get(test_url)
    time.sleep(5)
    
    available_saturdays = []
    
    # Scan 3 months
    for month_num in range(3):
        print(f"\n{'='*60}")
        print(f"Scanning month {month_num + 1}/3")
        print(f"{'='*60}")
        
        # Get the current month name
        try:
            month_element = driver.ele("css:.month")
            current_month = month_element.text
            print(f"Current month: {current_month}")
        except:
            current_month = "Unknown"
            print("Could not find month name")
        
        # Find all Saturday cells (td-sat class)
        try:
            saturday_cells = driver.eles("css:td.td-sat")
            print(f"Found {len(saturday_cells)} Saturday cells")
            
            for cell in saturday_cells:
                try:
                    # Get the date number
                    date_text = cell.ele("tag:p").text
                    
                    # Get the icon (O or X)
                    icon_element = cell.ele("css:.icon")
                    icon = icon_element.text
                    
                    # Get the full date from data attribute
                    full_date = cell.attr("data-join-time")
                    
                    status = "Available" if icon == "○" else "Full"
                    
                    print(f"  Saturday {date_text}: {icon} ({status}) - {full_date}")
                    
                    if icon == "○":
                        available_saturdays.append({
                            'month': current_month,
                            'date': date_text,
                            'full_date': full_date,
                            'icon': icon
                        })
                        
                except Exception as e:
                    print(f"  Error processing cell: {str(e)[:50]}")
                    
        except Exception as e:
            print(f"Error finding Saturday cells: {str(e)}")
        
        # Click next month button if not the last iteration
        if month_num < 2:
            try:
                print(f"\nClicking '翌月＞' button...")
                # Try multiple methods to find the button
                next_month_btn = None
                try:
                    next_month_btn = driver.ele("@id=nextMonth", timeout=3)
                except:
                    try:
                        next_month_btn = driver.ele("tag:input@value=翌月＞", timeout=3)
                    except:
                        try:
                            next_month_btn = driver.ele("css:.next-month", timeout=3)
                        except:
                            pass
                
                if next_month_btn:
                    next_month_btn.click()
                    time.sleep(3)  # Wait for page to load
                    print("✓ Moved to next month")
                else:
                    print("✗ Could not find next month button")
                    break
            except Exception as e:
                print(f"Error clicking next month button: {str(e)}")
                break
    
    # Print summary
    print(f"\n{'='*60}")
    print(f"SUMMARY: Available Saturdays")
    print(f"{'='*60}")
    
    if available_saturdays:
        for sat in available_saturdays:
            print(f"  ○ {sat['month']} - {sat['date']}日 ({sat['full_date']})")
    else:
        print("  No available Saturdays found in the next 3 months")
    
    print(f"\n{'='*60}")
    
    # Keep browser open to verify
    print("\nBrowser will stay open for 10 seconds...")
    time.sleep(10)

except Exception as e:
    print(f"\nError: {str(e)}")
    import traceback
    traceback.print_exc()

finally:
    print("\nClosing browser...")
    driver.quit()