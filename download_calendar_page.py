# -*- coding: utf-8 -*-
import asyncio
from pydoll.browser.chromium import Chrome
from pydoll.browser.options import ChromiumOptions

# Constants
CALENDAR_URL_CACHE = "calendar_url_cache.txt"
SLEEP_EXTENDED = 3
TAG_TD = "td"

def load_cached_url():
    """Load the cached calendar URL if it exists."""
    try:
        with open(CALENDAR_URL_CACHE, 'r') as f:
            return f.read().strip()
    except:
        return None

async def download_calendar_html():
    """Download and save the calendar page HTML for inspection."""
    cached_url = load_cached_url()
    
    if not cached_url:
        print("No cached URL found. Please run the main script first.")
        return
    
    print(f"Loading calendar from: {cached_url[:80]}...")
    
    options = ChromiumOptions()
    options.headless = False  # Use visible browser for debugging
    
    async with Chrome(options=options) as browser:
        tab = await browser.start()
        await tab.go_to(cached_url)
        await asyncio.sleep(SLEEP_EXTENDED)
        
        # Get the full HTML
        html_result = await tab.execute_script("return document.documentElement.outerHTML")
        if isinstance(html_result, dict) and 'result' in html_result:
            if 'result' in html_result['result']:
                html = html_result['result']['result'].get('value', '')
            else:
                html = html_result['result'].get('value', '')
        else:
            html = str(html_result)
        
        # Save to file
        output_file = "calendar_page.html"
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"\n✓ Saved full HTML to: {output_file}")
        
        # Get all td elements and inspect them
        print("\n" + "="*60)
        print("ANALYZING TD ELEMENTS")
        print("="*60)
        
        all_tds = await tab.find(tag_name=TAG_TD, find_all=True, raise_exc=False)
        print(f"\nTotal <td> elements found: {len(all_tds) if all_tds else 0}")
        
        if all_tds:
            # Analyze first 20 td elements
            print("\nInspecting first 20 td elements:")
            for i, td in enumerate(all_tds[:20]):
                try:
                    # Get classes
                    class_result = await td.execute_script("return this.className")
                    if isinstance(class_result, dict) and 'result' in class_result:
                        if 'result' in class_result['result']:
                            classes = class_result['result']['result'].get('value', '')
                        else:
                            classes = class_result['result'].get('value', '')
                    else:
                        classes = str(class_result)
                    
                    # Get data-join-time
                    attr_result = await td.execute_script("return this.getAttribute('data-join-time')")
                    if isinstance(attr_result, dict) and 'result' in attr_result:
                        if 'result' in attr_result['result']:
                            data_attr = attr_result['result']['result'].get('value', '')
                        else:
                            data_attr = attr_result['result'].get('value', '')
                    else:
                        data_attr = str(attr_result)
                    
                    # Get text content
                    text_result = await td.execute_script("return this.textContent.trim()")
                    if isinstance(text_result, dict) and 'result' in text_result:
                        if 'result' in text_result['result']:
                            text = text_result['result']['result'].get('value', '')
                        else:
                            text = text_result['result'].get('value', '')
                    else:
                        text = str(text_result)
                    
                    # Get outer HTML (first 200 chars)
                    html_result = await td.execute_script("return this.outerHTML.substring(0, 200)")
                    if isinstance(html_result, dict) and 'result' in html_result:
                        if 'result' in html_result['result']:
                            outer_html = html_result['result']['result'].get('value', '')
                        else:
                            outer_html = html_result['result'].get('value', '')
                    else:
                        outer_html = str(html_result)
                    
                    print(f"\n--- TD #{i+1} ---")
                    print(f"Classes: {classes}")
                    print(f"data-join-time: {data_attr}")
                    print(f"Text: {text[:50]}")
                    print(f"HTML snippet: {outer_html}")
                    
                except Exception as e:
                    print(f"\n--- TD #{i+1} --- Error: {e}")
        
        # Specifically look for Wednesday cells
        print("\n" + "="*60)
        print("SEARCHING FOR WEDNESDAY CELLS")
        print("="*60)
        
        wed_cells = await tab.find(tag_name=TAG_TD, class_name="td-wed", find_all=True, raise_exc=False)
        print(f"\nFound {len(wed_cells) if wed_cells else 0} td elements with class 'td-wed'")
        
        if wed_cells:
            for i, cell in enumerate(wed_cells):
                try:
                    # Get all attributes
                    attrs_result = await cell.execute_script("""
                        const attrs = {};
                        for (let attr of this.attributes) {
                            attrs[attr.name] = attr.value;
                        }
                        return JSON.stringify(attrs);
                    """)
                    if isinstance(attrs_result, dict) and 'result' in attrs_result:
                        if 'result' in attrs_result['result']:
                            attrs = attrs_result['result']['result'].get('value', '')
                        else:
                            attrs = attrs_result['result'].get('value', '')
                    else:
                        attrs = str(attrs_result)
                    
                    # Get outer HTML
                    html_result = await cell.execute_script("return this.outerHTML")
                    if isinstance(html_result, dict) and 'result' in html_result:
                        if 'result' in html_result['result']:
                            outer_html = html_result['result']['result'].get('value', '')
                        else:
                            outer_html = html_result['result'].get('value', '')
                    else:
                        outer_html = str(html_result)
                    
                    print(f"\n--- Wednesday Cell #{i+1} ---")
                    print(f"All attributes: {attrs}")
                    print(f"Full HTML:\n{outer_html}")
                    
                except Exception as e:
                    print(f"\n--- Wednesday Cell #{i+1} --- Error: {e}")
        
        print("\n" + "="*60)
        print("Analysis complete!")
        print("="*60)
        
        await asyncio.sleep(5)  # Keep browser open for a bit

if __name__ == "__main__":
    asyncio.run(download_calendar_html())
    print("\nDone!")