# -*- coding: utf-8 -*-
"""
Interactive manual trial for booking flow.
Saves HTML and state at each step for inspection.
"""
import asyncio
import json
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

CALENDAR_URL = open("calendar_url_cache.txt").read().strip()
TARGET_EMAIL = "waylonh@apple.com"
NUM_GUESTS = 2
STATE_FILE = "booking_state.json"


def save_state(step, data):
    """Save current state to file."""
    try:
        state = {}
        try:
            with open(STATE_FILE, 'r') as f:
                state = json.load(f)
        except:
            pass
        
        state[step] = data
        
        with open(STATE_FILE, 'w') as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
        
        print(f"[Saved state: {step}]")
    except Exception as e:
        print(f"Error saving state: {e}")


async def save_page_html(tab, filename):
    """Save current page HTML."""
    try:
        html_result = await tab.execute_script("return document.documentElement.outerHTML")
        html = html_result['result']['result']['value'] if isinstance(html_result, dict) else str(html_result)
        
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"[Saved HTML: {filename}]")
    except Exception as e:
        print(f"Error saving HTML: {e}")


async def get_current_url(tab):
    """Get current URL reliably."""
    url_response = await tab.execute_script("return window.location.href")
    return url_response['result']['result']['value'] if isinstance(url_response, dict) else url_response


async def manual_trial():
    """Interactive manual trial."""
    print("="*70)
    print("MANUAL BOOKING TRIAL - STEP BY STEP")
    print("="*70 + "\n")
    
    options = ChromiumOptions()
    options.start_timeout = 30
    
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        
        # STEP 1: Load calendar
        print("\n" + "="*70)
        print("STEP 1: LOADING CALENDAR")
        print("="*70)
        await tab.go_to(CALENDAR_URL)
        await asyncio.sleep(3)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step1_calendar.html")
        save_state("step1", {"url": url, "page": "calendar"})
        
        # Find available Sundays
        print("\nFinding available Sundays...")
        day_cells = await tab.find(tag_name="td", class_name="td-sun", find_all=True, raise_exc=False)
        
        available_sundays = []
        if day_cells:
            for i, cell in enumerate(day_cells):
                try:
                    date_elem = await cell.find(tag_name="p", raise_exc=False)
                    if date_elem:
                        text_result = await date_elem.execute_script("return this.textContent")
                        date_text = text_result['result']['result']['value']
                    else:
                        date_text = "?"
                    
                    icon_elem = await cell.find(class_name="icon", raise_exc=False)
                    if icon_elem:
                        text_result = await icon_elem.execute_script("return this.textContent")
                        icon = text_result['result']['result']['value']
                    else:
                        icon = "?"
                    
                    attr_result = await cell.execute_script("return this.getAttribute('data-join-time')")
                    full_date = attr_result['result']['result']['value']
                    
                    if icon == "○":
                        available_sundays.append({
                            'index': i,
                            'date': date_text,
                            'full_date': full_date,
                            'cell': cell
                        })
                        print(f"  [{len(available_sundays)-1}] {date_text}日 AVAILABLE - {full_date}")
                except:
                    pass
        
        if not available_sundays:
            print("No available Sundays found!")
            return
        
        choice = input(f"\nSelect Sunday to book [0-{len(available_sundays)-1}]: ")
        selected = available_sundays[int(choice)]
        
        # Save state without the cell object (not JSON serializable)
        save_state("selected_date", {
            'index': selected['index'],
            'date': selected['date'],
            'full_date': selected['full_date']
        })
        
        # STEP 2: Click date
        print("\n" + "="*70)
        print(f"STEP 2: CLICKING DATE {selected['date']}日")
        print("="*70)
        
        # Click using JavaScript to avoid pydoll visibility check issues
        cell = selected['cell']
        await cell.execute_script("this.click()")
        print("Clicked date cell via JavaScript")
        await asyncio.sleep(3)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step2_service_group_select.html")
        save_state("step2", {"url": url, "page": "service_group_select"})
        
        # List all links
        print("\nAll links on this page:")
        links = await tab.find(tag_name="a", find_all=True, timeout=5, raise_exc=False)
        
        link_info = []
        if links:
            for i, link in enumerate(links):
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value']
                    
                    href_result = await link.execute_script("return this.href")
                    href = href_result['result']['result']['value']
                    
                    link_info.append({"index": i, "text": link_text, "href": href})
                    print(f"  [{i}] '{link_text[:60]}'")
                    print(f"      {href}")
                except:
                    pass
        
        save_state("step2_links", link_info)
        
        choice = input(f"\nSelect hotel link [0-{len(links)-1}]: ")
        selected_link = links[int(choice)]
        
        # STEP 3: Click hotel
        print("\n" + "="*70)
        print("STEP 3: CLICKING HOTEL LINK")
        print("="*70)
        await selected_link.scroll_into_view()
        await asyncio.sleep(1)
        await selected_link.click()
        await asyncio.sleep(3)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step3_apply_service_select.html")
        save_state("step3", {"url": url, "page": "apply_service_select"})
        
        # List all links
        print("\nAll links on this page:")
        links = await tab.find(tag_name="a", find_all=True, timeout=5, raise_exc=False)
        
        link_info = []
        if links:
            for i, link in enumerate(links):
                try:
                    text_result = await link.execute_script("return this.textContent.trim()")
                    link_text = text_result['result']['result']['value']
                    
                    href_result = await link.execute_script("return this.href")
                    href = href_result['result']['result']['value']
                    
                    link_info.append({"index": i, "text": link_text, "href": href})
                    print(f"  [{i}] '{link_text[:60]}'")
                    print(f"      {href}")
                except:
                    pass
        
        save_state("step3_links", link_info)
        
        choice = input(f"\nSelect service link [0-{len(links)-1}]: ")
        selected_link = links[int(choice)]
        
        # STEP 4: Click service
        print("\n" + "="*70)
        print("STEP 4: CLICKING SERVICE LINK")
        print("="*70)
        await selected_link.scroll_into_view()
        await asyncio.sleep(1)
        await selected_link.click()
        await asyncio.sleep(3)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step4_empty_new.html")
        save_state("step4", {"url": url, "page": "empty_new"})
        
        # STEP 5: Fill form
        print("\n" + "="*70)
        print("STEP 5: FILLING BOOKING FORM")
        print("="*70)
        
        # List all inputs
        print("\nAll input fields on this page:")
        inputs = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        input_info = []
        if inputs:
            for i, inp in enumerate(inputs):
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = type_result['result']['result']['value']
                    
                    name_result = await inp.execute_script("return this.name || 'no-name'")
                    input_name = name_result['result']['result']['value']
                    
                    value_result = await inp.execute_script("return this.value")
                    input_value = value_result['result']['result']['value']
                    
                    input_info.append({"index": i, "type": input_type, "name": input_name, "value": input_value})
                    print(f"  [{i}] type={input_type}, name={input_name}, value={input_value}")
                except:
                    pass
        
        save_state("step5_inputs", input_info)
        
        # Auto-fill guest count
        choice = input(f"\nWhich input for guest count? [0-{len(inputs)-1}] (or press Enter to skip): ")
        if choice.strip():
            inp = inputs[int(choice)]
            await inp.scroll_into_view()
            await asyncio.sleep(0.5)
            await inp.execute_script("this.value = ''")
            await inp.insert_text(str(NUM_GUESTS))
            print(f"Filled: {NUM_GUESTS}")
            await asyncio.sleep(1)
        
        # Find search button
        print("\nAll buttons on this page:")
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        button_info = []
        if buttons:
            for i, btn in enumerate(buttons):
                try:
                    type_result = await btn.execute_script("return this.type")
                    btn_type = type_result['result']['result']['value']
                    
                    value_result = await btn.execute_script("return this.value")
                    btn_value = value_result['result']['result']['value']
                    
                    if btn_type in ["button", "submit"]:
                        button_info.append({"index": i, "type": btn_type, "value": btn_value})
                        print(f"  [{i}] {btn_value}")
                except:
                    pass
        
        save_state("step5_buttons", button_info)
        
        choice = input(f"\nWhich button to click for search? [0-{len(buttons)-1}]: ")
        search_btn = buttons[int(choice)]
        
        print("\n" + "="*70)
        print("STEP 6: CLICKING SEARCH BUTTON")
        print("="*70)
        await search_btn.scroll_into_view()
        await asyncio.sleep(1)
        await search_btn.click()
        await asyncio.sleep(4)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step6_search_results.html")
        save_state("step6", {"url": url, "page": "search_results"})
        
        # STEP 7: Select room checkbox
        print("\n" + "="*70)
        print("STEP 7: SELECTING ROOM CHECKBOX")
        print("="*70)
        
        print("\nAll checkboxes on this page:")
        all_inputs = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        checkbox_info = []
        checkboxes = []
        if all_inputs:
            for i, inp in enumerate(all_inputs):
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = type_result['result']['result']['value']
                    
                    if input_type == "checkbox":
                        name_result = await inp.execute_script("return this.name || 'no-name'")
                        input_name = name_result['result']['result']['value']
                        
                        value_result = await inp.execute_script("return this.value || ''")
                        input_value = value_result['result']['result']['value']
                        
                        disabled_result = await inp.execute_script("return this.disabled")
                        is_disabled = disabled_result['result']['result']['value']
                        
                        checked_result = await inp.execute_script("return this.checked")
                        is_checked = checked_result['result']['result']['value']
                        
                        checkboxes.append(inp)
                        checkbox_info.append({
                            "index": len(checkboxes)-1,
                            "original_index": i,
                            "name": input_name,
                            "value": input_value,
                            "disabled": is_disabled,
                            "checked": is_checked
                        })
                        
                        status = "DISABLED" if is_disabled else ("CHECKED" if is_checked else "ENABLED")
                        print(f"  [{len(checkboxes)-1}] name={input_name}, value={input_value}, {status}")
                except:
                    pass
        
        save_state("step7_checkboxes", checkbox_info)
        
        if not checkboxes:
            print("No checkboxes found!")
            return
        
        choice = input(f"\nSelect room checkbox (avoid #0 if possible) [0-{len(checkboxes)-1}]: ")
        checkbox = checkboxes[int(choice)]
        
        print(f"\nClicking checkbox #{choice}...")
        await checkbox.scroll_into_view()
        await asyncio.sleep(1)
        await checkbox.execute_script("this.click()")
        print("Clicked checkbox via JavaScript")
        await asyncio.sleep(1)
        
        await save_page_html(tab, "step7_checkbox_selected.html")
        save_state("step7_selected", {"checkbox_index": int(choice)})
        
        # Find and click 予約手続きに進む button
        print("\nLooking for 予約手続きに進む button...")
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        proceed_info = []
        if buttons:
            for i, button in enumerate(buttons):
                try:
                    value_result = await button.execute_script("return this.value")
                    button_value = value_result['result']['result']['value']
                    
                    type_result = await button.execute_script("return this.type")
                    button_type = type_result['result']['result']['value']
                    
                    if button_type in ["button", "submit"]:
                        proceed_info.append({"index": i, "type": button_type, "value": button_value})
                        print(f"  [{i}] {button_value}")
                        
                        if "予約手続き" in button_value and "進む" in button_value:
                            print(f"\nClicking button #{i}: {button_value}")
                            await button.scroll_into_view()
                            await asyncio.sleep(1)
                            await button.click()
                            print("Clicked 予約手続きに進む button")
                            await asyncio.sleep(3)
                            break
                except:
                    pass
        
        save_state("step7_proceed_buttons", proceed_info)
        
        # STEP 8: Rule page - agree
        print("\n" + "="*70)
        print("STEP 8: RULE PAGE - CLICKING 同意する")
        print("="*70)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step8_rule.html")
        save_state("step8", {"url": url, "page": "rule"})
        
        if "apply/rule" not in url:
            print(f"Warning: Not on expected rule page!")
        
        # Find all buttons
        print("\nAll buttons on rule page:")
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        rule_buttons = []
        if buttons:
            for i, button in enumerate(buttons):
                try:
                    type_result = await button.execute_script("return this.type")
                    btn_type = type_result['result']['result']['value']
                    
                    value_result = await button.execute_script("return this.value || ''")
                    btn_value = value_result['result']['result']['value']
                    
                    if btn_type in ["button", "submit"]:
                        rule_buttons.append({"index": i, "type": btn_type, "value": btn_value})
                        print(f"  [{i}] {btn_value}")
                        
                        if "同意" in btn_value:
                            print(f"\nClicking button #{i}: {btn_value}")
                            await button.scroll_into_view()
                            await asyncio.sleep(1)
                            await button.click()
                            print("Clicked 同意する button")
                            await asyncio.sleep(3)
                            break
                except:
                    pass
        
        save_state("step8_buttons", rule_buttons)
        
        # STEP 9: Email input
        print("\n" + "="*70)
        print("STEP 9: EMAIL INPUT PAGE")
        print("="*70)
        
        url = await get_current_url(tab)
        print(f"URL: {url}")
        await save_page_html(tab, "step9_email_input.html")
        save_state("step9", {"url": url, "page": "email_input"})
        
        if "apply/email_input" not in url:
            print(f"Warning: Not on expected email_input page!")
        
        # Find all inputs
        print("\nAll input fields on email page:")
        inputs = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        email_inputs = []
        if inputs:
            for i, inp in enumerate(inputs):
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = type_result['result']['result']['value']
                    
                    name_result = await inp.execute_script("return this.name || 'no-name'")
                    input_name = name_result['result']['result']['value']
                    
                    placeholder_result = await inp.execute_script("return this.placeholder || ''")
                    placeholder = placeholder_result['result']['result']['value']
                    
                    email_inputs.append({
                        "index": i,
                        "type": input_type,
                        "name": input_name,
                        "placeholder": placeholder
                    })
                    print(f"  [{i}] type={input_type}, name={input_name}, placeholder={placeholder}")
                except:
                    pass
        
        save_state("step9_inputs", email_inputs)
        
        # Auto-fill email if found
        email_filled = False
        if inputs:
            for inp in inputs:
                try:
                    type_result = await inp.execute_script("return this.type")
                    input_type = type_result['result']['result']['value']
                    
                    if input_type in ["email", "text"]:
                        name_result = await inp.execute_script("return this.name")
                        input_name = name_result['result']['result']['value']
                        
                        # Look for email-related input
                        if "email" in input_name.lower() or "mail" in input_name.lower():
                            print(f"\nFilling email in: {input_name}")
                            await inp.scroll_into_view()
                            await asyncio.sleep(0.5)
                            await inp.execute_script("this.value = ''")
                            await inp.insert_text(TARGET_EMAIL)
                            print(f"Filled: {TARGET_EMAIL}")
                            email_filled = True
                            await asyncio.sleep(1)
                            break
                except:
                    pass
        
        if email_filled:
            await save_page_html(tab, "step9_email_filled.html")
        
        # STEP 10: Click 送信 button
        print("\n" + "="*70)
        print("STEP 10: CLICKING 送信 (SUBMIT) BUTTON")
        print("="*70)
        
        print("\nLooking for 送信 button...")
        buttons = await tab.find(tag_name="input", find_all=True, timeout=5, raise_exc=False)
        
        submit_buttons = []
        if buttons:
            for i, button in enumerate(buttons):
                try:
                    type_result = await button.execute_script("return this.type")
                    btn_type = type_result['result']['result']['value']
                    
                    value_result = await button.execute_script("return this.value || ''")
                    btn_value = value_result['result']['result']['value']
                    
                    name_result = await button.execute_script("return this.name || 'no-name'")
                    btn_name = name_result['result']['result']['value']
                    
                    if btn_type in ["button", "submit"]:
                        submit_buttons.append({"index": i, "type": btn_type, "name": btn_name, "value": btn_value})
                        print(f"  [{i}] type={btn_type}, name={btn_name}, value={btn_value}")
                except:
                    pass
        
        save_state("step10_buttons", submit_buttons)
        
        if not submit_buttons:
            print("No buttons found!")
            return
        
        choice = input(f"\nSelect 送信 button [0-{len(buttons)-1}]: ")
        submit_btn = buttons[int(choice)]
        
        print(f"\nPreparing to click 送信 button...")
        print("Note: This will trigger a JavaScript confirmation dialog")
        
        # Set up dialog handling
        from pydoll.protocol.page.events import PageEvent
        
        dialog_handled = False
        dialog_message = ""
        
        async def handle_dialog(event):
            """Auto-accept confirmation dialog."""
            nonlocal dialog_handled, dialog_message
            try:
                dialog_type = event['params']['type']
                dialog_message = event['params']['message']
                print(f"\n[DIALOG] Type: {dialog_type}, Message: {dialog_message}")
                
                # Accept dialog (click OK)
                await tab.handle_dialog(accept=True)
                print("[DIALOG] ✓ Accepted (clicked OK)")
                dialog_handled = True
            except Exception as e:
                print(f"[DIALOG ERROR] {e}")
        
        # Enable page events and register dialog handler
        print("Enabling page events and dialog handler...")
        await tab.enable_page_events()
        callback_id = await tab.on(PageEvent.JAVASCRIPT_DIALOG_OPENING, handle_dialog)
        await asyncio.sleep(1)
        
        # Click button
        print("Clicking 送信 button...")
        await submit_btn.scroll_into_view()
        await asyncio.sleep(1)
        
        # Use form submit instead of button click to avoid timeout
        try:
            await tab.execute_script("document.querySelector('form').submit();")
            print("Submitted form")
        except Exception as e:
            print(f"Submit: {e}")
        
        # Wait for dialog and navigation
        print("Waiting for dialog...")
        await asyncio.sleep(5)
        
        if dialog_handled:
            print(f"✓ Dialog handled! Message: '{dialog_message}'")
        else:
            print("⚠ Dialog not detected via event handler")
            print("Trying manual approach...")
            # Try to accept any pending dialog
            try:
                await tab.handle_dialog(accept=True)
                print("Attempted to accept dialog manually")
            except:
                pass
        
        await asyncio.sleep(3)
        
        # Get final URL
        url = await get_current_url(tab)
        print(f"\nFinal URL: {url}")
        await save_page_html(tab, "step10_after_submit.html")
        save_state("step10", {"url": url, "dialog_handled": dialog_handled, "dialog_message": dialog_message})
        
        print("\n" + "="*70)
        print("✅ COMPLETE BOOKING FLOW TRIAL FINISHED!")
        print("="*70)
        print("\nState saved to:", STATE_FILE)
        print("HTML files saved: step1-11.html")
        print("\nManually inspect the browser for confirmation dialog if present.")
        print("Keeping browser open for 60 seconds...")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(manual_trial())
    print("Done!")