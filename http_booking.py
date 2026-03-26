# -*- coding: utf-8 -*-
"""Direct HTTP booking bypass for ITS Calendar Scanner.

Replaces browser automation with pure HTTP requests for maximum speed.
Each booking step is a single HTTP POST (~50ms) vs browser page load (~300ms).

The ITS site uses Rails-standard CSRF protection:
- Each page contains an authenticity_token in a meta tag or hidden input
- Each POST must include the token from the previous page's response
- The session parameter `s` changes at certain steps

Flow:
  GET  calendar_select     → extract token
  POST service_group_select → extract token + hotel list
  POST apply_service_select → extract token + service IDs
  POST check_apply_service_coma → redirects to empty_new
  GET  empty_new            → extract token + new s param
  POST empty_new            → search rooms (XHR)
  POST empty_create         → select room
  GET  rule                 → extract token + new s param
  POST email_input          → agree + submit
"""

import re
import asyncio
from urllib.parse import urlencode, urlparse, parse_qs

import aiohttp

from config import (
    NUM_GUESTS,
    TARGET_EMAIL,
    SKIP_HOTELS,
    LOG_ARROW,
    LOG_SUCCESS,
    LOG_ERROR,
    LOG_WARNING,
    LOG_SKIP,
    LOG_SEPARATOR,
    LOG_EQUALS,
    SEPARATOR_WIDTH,
)
from cache import save_booking, get_booked_hotels_for_date

BASE_URL = "https://as.its-kenpo.or.jp"

# Regex patterns for extracting data from HTML responses
TOKEN_RE = re.compile(
    r'name="authenticity_token"[^>]*value="([^"]+)"'
)
META_TOKEN_RE = re.compile(
    r'content="([^"]+)"[^>]*name="csrf-token"'
    r'|'
    r'name="csrf-token"[^>]*content="([^"]+)"'
)
S_PARAM_RE = re.compile(r'[?&]s=([^&"\'<>\s]+)')
SERVICE_GROUP_RE = re.compile(
    r'service_group_id=(\d+)[^>]*>([^<]+)<'
)
APPLY_SERVICE_RE = re.compile(
    r'apply_service_id=(\d+)[^>]*>([^<]+)<'
)
ROOM_CHECKBOX_RE = re.compile(
    r'name="apply\[coma\[(\d+)\]\]"'
)
SESSION_GUID_RE = re.compile(
    r'name="apply_session_guid"[^>]*value="([^"]+)"'
)


def extract_token(html):
    """Extract CSRF authenticity_token from form hidden input."""
    m = TOKEN_RE.search(html)
    return m.group(1) if m else None


def extract_meta_token(html):
    """Extract CSRF token from meta tag (for XHR requests)."""
    m = META_TOKEN_RE.search(html)
    if m:
        return m.group(1) or m.group(2)
    return None


def extract_s_param(html_or_url):
    """Extract session s= parameter from HTML or URL."""
    m = S_PARAM_RE.search(html_or_url)
    return m.group(1) if m else None


def extract_hotels(html):
    """Extract hotel names and service_group_ids from service_group_select page.

    Returns list of (service_group_id, hotel_name) tuples.
    """
    hotels = []
    pattern = re.compile(
        r'data-service-group-id="(\d+)"[^>]*>([^<]+)<',
    )
    for m in pattern.finditer(html):
        gid = m.group(1)
        name = m.group(2).strip()
        hotels.append((gid, name))
    return hotels


def extract_services(html):
    """Extract apply_service_id and service names from apply_service_select page."""
    services = []
    pattern = re.compile(
        r'data-apply-service-id="(\d+)"[^>]*>([^<]+)<',
    )
    for m in pattern.finditer(html):
        sid = m.group(1)
        name = m.group(2).strip()
        services.append((sid, name))
    return services


def extract_rooms(html):
    """Extract room IDs from the booking form."""
    rooms = []
    for m in ROOM_CHECKBOX_RE.finditer(html):
        room_id = m.group(1)
        # Check if disabled
        # Get surrounding context to check disabled state
        pos = m.start()
        context = html[max(0, pos - 50) : pos + 100]
        if "disabled" not in context.lower():
            rooms.append(room_id)
    return rooms


def extract_session_guid(html):
    """Extract apply_session_guid from the form."""
    m = SESSION_GUID_RE.search(html)
    return m.group(1) if m else None


class HTTPBooker:
    """Direct HTTP booking client — no browser needed.

    Performs the entire booking flow using aiohttp with ~50ms per request
    instead of ~300ms per browser page load.
    """

    def __init__(self, calendar_url):
        self.calendar_url = calendar_url
        parsed = urlparse(calendar_url)
        qs = parse_qs(parsed.query)
        self.s_param = qs.get("s", [""])[0]
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ja-JP,ja;q=0.9",
            },
        )
        return self

    async def __aexit__(self, *args):
        if self.session:
            await self.session.close()

    async def _get(self, url):
        """GET request, return response text."""
        async with self.session.get(url, allow_redirects=True) as resp:
            return await resp.text()

    async def _post(self, url, data, content_type=None):
        """POST request, return (response_text, final_url)."""
        headers = {}
        if content_type:
            headers["Content-Type"] = content_type
        async with self.session.post(
            url, data=data, allow_redirects=True, headers=headers
        ) as resp:
            text = await resp.text()
            return text, str(resp.url)

    async def scan_availability(self, target_date):
        """Check if a date has the availability circle marker.

        Uses the calendar page HTML to check without clicking.

        Returns:
            bool: True if date shows as available
        """
        html = await self._get(self.calendar_url)
        # Look for the date cell with availability marker
        pattern = re.compile(
            rf'data-join-time="{re.escape(target_date)}"[^>]*>.*?</td>',
            re.DOTALL,
        )
        m = pattern.search(html)
        if m:
            cell_html = m.group(0)
            return "○" in cell_html or "◎" in cell_html
        return False

    async def get_hotels_for_date(self, target_date):
        """Click a date and get available hotels via HTTP.

        Returns:
            list of (service_group_id, hotel_name) tuples
        """
        # Step 1: GET calendar page to get token
        html = await self._get(self.calendar_url)
        token = extract_token(html)
        if not token:
            print(f"  {LOG_ERROR} No token on calendar page")
            return []

        # Step 2: POST to service_group_select (clicking the date)
        data = urlencode({
            "utf8": "✓",
            "authenticity_token": token,
            "join_time": target_date,
            "s": self.s_param,
        })
        html, url = await self._post(
            f"{BASE_URL}/calendar_apply/service_group_select",
            data,
            content_type="application/x-www-form-urlencoded",
        )

        if "service_group_select" not in url:
            print(f"  {LOG_ERROR} Not on service_group_select: {url[:80]}")
            return []

        # Extract hotels
        hotels = extract_hotels(html)
        return hotels

    async def book_hotel(self, target_date, service_group_id, hotel_name):
        """Execute full booking for a specific hotel.

        Args:
            target_date: Date string (YYYY-MM-DD)
            service_group_id: Hotel's service group ID
            hotel_name: Hotel name for logging

        Returns:
            bool: True if booking completed
        """
        print(f"\n{LOG_SEPARATOR * SEPARATOR_WIDTH}")
        print(f"HTTP BOOK: {target_date} - {hotel_name[:50]}")
        print(f"{LOG_SEPARATOR * SEPARATOR_WIDTH}")

        try:
            # Step 1: GET calendar, extract token
            html = await self._get(self.calendar_url)
            token = extract_token(html)
            if not token:
                print(f"  {LOG_ERROR} No token on calendar")
                return False

            # Step 2: POST service_group_select (click date)
            data = urlencode({
                "utf8": "✓",
                "authenticity_token": token,
                "join_time": target_date,
                "s": self.s_param,
            })
            html, url = await self._post(
                f"{BASE_URL}/calendar_apply/service_group_select",
                data,
                content_type="application/x-www-form-urlencoded",
            )
            token = extract_token(html)
            if not token:
                print(f"  {LOG_ERROR} No token on service_group_select")
                return False
            print(f"  {LOG_ARROW} Date selected")

            # Step 3: POST apply_service_select (click hotel)
            data = urlencode({
                "utf8": "✓",
                "authenticity_token": token,
                "empty": "",
                "join_time": target_date,
                "s": self.s_param,
                "service_group_id": service_group_id,
            })
            html, url = await self._post(
                f"{BASE_URL}/calendar_apply/apply_service_select",
                data,
                content_type="application/x-www-form-urlencoded",
            )
            token = extract_token(html)
            if not token:
                print(f"  {LOG_ERROR} No token on apply_service_select")
                return False

            # Extract service ID
            services = extract_services(html)
            if not services:
                print(f"  {LOG_ERROR} No services found")
                return False
            service_id = services[0][0]
            print(f"  {LOG_ARROW} Hotel selected (service_id={service_id})")

            # Step 4: POST check_apply_service_coma (click service)
            data = urlencode({
                "utf8": "✓",
                "authenticity_token": token,
                "join_time": target_date,
                "s": self.s_param,
                "apply_service_id": service_id,
            })
            html, url = await self._post(
                f"{BASE_URL}/calendar_apply/check_apply_service_coma",
                data,
                content_type="application/x-www-form-urlencoded",
            )

            # Step 5: We should now be on empty_new — extract new s param + token
            new_s = extract_s_param(url)
            if new_s:
                self.s_param = new_s
            token = extract_token(html)
            meta_token = extract_meta_token(html)
            if not token:
                print(f"  {LOG_ERROR} No token on empty_new")
                return False

            # Extract the s param for the search and form
            form_s_match = re.search(r'empty_create\?s=([^&"]+)', html)
            form_s = form_s_match.group(1) if form_s_match else self.s_param
            print(f"  {LOG_ARROW} Service selected")

            # Step 6: XHR room search (requires meta CSRF token + XHR headers)
            form_data = urlencode({
                "utf8": "✓",
                "authenticity_token": token,
                "apply[join_time]": target_date,
                "apply[night_count]": "1",
                "apply[stay_persons]": str(NUM_GUESTS),
                "apply[hope_rooms]": "1",
                **{f"apply[hope_room{i}]": "" for i in range(1, 11)},
            })

            xhr_headers = {
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "X-Requested-With": "XMLHttpRequest",
                "X-CSRF-Token": meta_token or token,
                "Accept": "text/javascript, application/javascript, */*; q=0.01",
                "Origin": BASE_URL,
                "Referer": f"{BASE_URL}/apply/empty_new?s={form_s}",
            }

            async with self.session.post(
                f"{BASE_URL}/apply/empty_new?s={form_s}",
                data=form_data,
                headers=xhr_headers,
                allow_redirects=False,
            ) as resp:
                xhr_text = await resp.text()

            if not xhr_text:
                print(f"  {LOG_ERROR} Room search returned empty")
                return False
            print(f"  {LOG_ARROW} Searched rooms")

            # Step 7: Extract room IDs and session GUID from XHR JS response
            # Note: XHR returns JavaScript, so HTML is escaped (\" instead of ")
            rooms = re.findall(r'coma\[(\d+)\]', xhr_text)
            # Deduplicate while preserving order
            seen = set()
            unique_rooms = []
            for r in rooms:
                if r not in seen:
                    seen.add(r)
                    unique_rooms.append(r)
            rooms = unique_rooms

            guid_match = re.search(
                r'apply_session_guid[^>]*value=\\"([^"\\]+)\\"', xhr_text
            )
            if not guid_match:
                guid_match = re.search(
                    r'apply_session_guid[^>]*value="([^"]+)"', xhr_text
                )
            session_guid = guid_match.group(1) if guid_match else ""
            session_guid = guid_match.group(1) if guid_match else ""

            if not rooms:
                print(f"  {LOG_ERROR} No rooms available")
                return False

            room_id = rooms[0]

            form_data = {
                "utf8": "✓",
                "authenticity_token": token,
                "apply[join_time]": target_date,
                "apply[night_count]": "1",
                "apply[stay_persons]": str(NUM_GUESTS),
                "apply[hope_rooms]": "1",
                "apply_session_guid": session_guid,
                f"apply[coma[{room_id}]]": room_id,
            }
            for i in range(1, 11):
                form_data[f"apply[hope_room{i}]"] = ""

            html, url = await self._post(
                f"{BASE_URL}/apply/empty_create?s={form_s}",
                form_data,
            )
            print(f"  {LOG_ARROW} Room selected (id={room_id})")

            # Step 8: We should be on rule page — extract new s and token
            new_s = extract_s_param(url)
            if new_s:
                self.s_param = new_s
            token = extract_token(html)
            if not token:
                print(f"  {LOG_ERROR} No token on rule page")
                return False

            # Step 9: POST email_input (agree to rules)
            data = urlencode({
                "utf8": "✓",
                "authenticity_token": token,
                "s": self.s_param,
            })
            html, url = await self._post(
                f"{BASE_URL}/apply/email_input",
                data,
                content_type="application/x-www-form-urlencoded",
            )
            print(f"  {LOG_ARROW} Agreed to rules")

            # Check if we reached email_input page
            if "email_input" in url:
                token = extract_token(html)
                print(f"\n{LOG_EQUALS * SEPARATOR_WIDTH}")
                print(f"{LOG_SUCCESS} HTTP BOOKING REACHED EMAIL PAGE")
                print(f"  Next step: POST email with token to complete")
                print(f"{LOG_EQUALS * SEPARATOR_WIDTH}")

                save_booking(target_date, hotel_name)
                return True
            else:
                print(f"  {LOG_WARNING} Unexpected URL: {url[:80]}")
                return "email" in url or "complete" in url

        except Exception as e:
            print(f"  {LOG_ERROR} HTTP booking error: {str(e)[:80]}")
            return False


async def http_book_date(calendar_url, target_date, dry_run=False):
    """Book a date using direct HTTP requests.

    Args:
        calendar_url: Calendar URL with session token
        target_date: Target date (YYYY-MM-DD)
        dry_run: If True, only scan and list hotels

    Returns:
        bool: True if booking completed
    """
    async with HTTPBooker(calendar_url) as booker:
        # Get hotels for date
        hotels = await booker.get_hotels_for_date(target_date)

        if not hotels:
            print(f"  {LOG_ERROR} No hotels for {target_date}")
            return False

        # Filter skip hotels
        available = []
        for gid, name in hotels:
            if any(skip in name for skip in SKIP_HOTELS):
                print(f"  {LOG_SKIP} {name[:50]}")
            else:
                print(f"  {LOG_ARROW} {name[:50]} (id={gid})")
                available.append((gid, name))

        if not available:
            print(f"  {LOG_ERROR} All hotels skipped")
            return False

        # Filter already booked
        booked = get_booked_hotels_for_date(target_date)
        available = [(gid, name) for gid, name in available if name not in booked]

        if dry_run:
            print(f"  {LOG_ARROW} DRY RUN - {len(available)} hotels available")
            return bool(available)

        # Try each hotel
        for gid, name in available:
            if await booker.book_hotel(target_date, gid, name):
                return True

    return False
