# app.py
import os
import json
import sqlite3
import uuid
from typing import List, Dict, Any, Optional
from contextlib import closing
from datetime import datetime
from dotenv import load_dotenv
import streamlit as st
from openai import OpenAI  # same client you used previously

# load env
load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DB_PATH = "reservation_agent.db"

from datetime import datetime, timezone
try:
    # Python 3.9+: accurate zone support
    from zoneinfo import ZoneInfo
    ZONE_INFO_AVAILABLE = True
except Exception:
    ZoneInfo = None
    ZONE_INFO_AVAILABLE = False

# Default timezone to use for comparisons (user timezone per your info)
DEFAULT_TZ_NAME = "Asia/Kolkata"

import re
from datetime import time, timedelta

# helper: simple time parser for strings like "7pm", "7:30 pm", "19:00"
_time_re = re.compile(r"^\s*(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?\s*$", re.IGNORECASE)

def _normalize_natural_datetime(dt_str: str):
    """
    Try to convert informal datetime strings into ISO "YYYY-MM-DD HH:MM" in DEFAULT_TZ_NAME.
    Handles:
      - 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM' -> passes through
      - 'today 7pm', 'tonight 7 pm', 'tomorrow 19:00', 'tomorrow 7pm'
      - '7pm' or '19:00' -> assume today unless that time already passed, then assume tomorrow
    Returns (normalized_iso_str, debug_dict) or (None, debug_dict) on failure.
    debug_dict contains 'now_iso' and reason/messages.
    """
    now = _now_in_default_tz()
    debug = {"now_iso": now.isoformat(), "input": dt_str}

    if not dt_str or not isinstance(dt_str, str):
        debug["error"] = "empty_input"
        return None, debug

    s = dt_str.strip().lower()

    # 1) Already ISO-ish?
    try:
        # allow both T and space
        cand = s if "t" in s else s.replace(" ", "t")
        dt = datetime.fromisoformat(cand)
        # attach tz if missing
        if ZONE_INFO_AVAILABLE:
            tz = ZoneInfo(DEFAULT_TZ_NAME)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=tz)
            else:
                dt = dt.astimezone(tz)
        debug["parsed_as"] = "iso"
        debug["requested_iso"] = dt.isoformat()
        return dt.strftime("%Y-%m-%d %H:%M"), debug
    except Exception:
        pass

    # 2) Handle "today", "tonight", "tomorrow" prefixes
    tokens = s.split()
    day_offset = 0
    if tokens[0] in ("today", "tonight"):
        day_offset = 0
        rest = " ".join(tokens[1:]).strip()
    elif tokens[0] == "tomorrow":
        day_offset = 1
        rest = " ".join(tokens[1:]).strip()
    else:
        rest = s  # may be just "7pm" or "7:00 pm" or "19:00"

    # 3) If rest is empty but we had "tonight"/"today", default to 19:00
    if rest == "":
        default_hour = 19
        dt_candidate = now + timedelta(days=day_offset)
        dt_combined = datetime(dt_candidate.year, dt_candidate.month, dt_candidate.day, default_hour, 0)
        if ZONE_INFO_AVAILABLE:
            dt_combined = dt_combined.replace(tzinfo=ZoneInfo(DEFAULT_TZ_NAME))
        debug["parsed_as"] = "default_evening"
        debug["requested_iso"] = dt_combined.isoformat()
        return dt_combined.strftime("%Y-%m-%d %H:%M"), debug

    # 4) Parse time-only forms:
    m = _time_re.match(rest)
    if m:
        h = int(m.group("h"))
        minute = int(m.group("m") or 0)
        ampm = m.group("ampm")
        if ampm:
            ampm = ampm.lower()
            if ampm == "pm" and h < 12:
                h = h + 12
            if ampm == "am" and h == 12:
                h = 0
        # if 24h format and hour==24 -> set to 0 next day (unlikely)
        # compose date with day_offset
        candidate_day = now + timedelta(days=day_offset)
        dt_combined = datetime(candidate_day.year, candidate_day.month, candidate_day.day, h, minute)
        if ZONE_INFO_AVAILABLE:
            dt_combined = dt_combined.replace(tzinfo=ZoneInfo(DEFAULT_TZ_NAME))
        # If this is time-only (no explicit day) AND dt already passed today, and day_offset==0 -> try tomorrow
        if day_offset == 0 and dt_combined <= now:
            dt_combined = dt_combined + timedelta(days=1)
            debug["adjusted_to_tomorrow"] = True
        debug["parsed_as"] = "time_only"
        debug["requested_iso"] = dt_combined.isoformat()
        return dt_combined.strftime("%Y-%m-%d %H:%M"), debug

    # 5) try to catch "at 7pm" or "on 2025-11-10 at 19:00"
    # basic approach: find a time token in the string
    parts = re.split(r"\bat\b|\bfor\b|\bon\b", s)
    for part in parts[::-1]:
        part = part.strip()
        m = _time_re.match(part)
        if m:
            # handle similar to above
            h = int(m.group("h"))
            minute = int(m.group("m") or 0)
            ampm = m.group("ampm")
            if ampm:
                if ampm.lower() == "pm" and h < 12:
                    h += 12
                if ampm.lower() == "am" and h == 12:
                    h = 0
            # check if any explicit "tomorrow" or date present earlier
            if "tomorrow" in s:
                day_offset = 1
            candidate_day = now + timedelta(days=day_offset)
            dt_combined = datetime(candidate_day.year, candidate_day.month, candidate_day.day, h, minute)
            if ZONE_INFO_AVAILABLE:
                dt_combined = dt_combined.replace(tzinfo=ZoneInfo(DEFAULT_TZ_NAME))
            if day_offset == 0 and dt_combined <= now:
                dt_combined = dt_combined + timedelta(days=1)
                debug["adjusted_to_tomorrow"] = True
            debug["parsed_as"] = "found_time_token"
            debug["requested_iso"] = dt_combined.isoformat()
            return dt_combined.strftime("%Y-%m-%d %H:%M"), debug

    # give up
    debug["error"] = "unrecognized_format"
    return None, debug

# replace the old _parse_datetime_for_ui with this corrected version
def _parse_datetime_for_ui(dt_str: str):
    """
    Parse "YYYY-MM-DD HH:MM" or "YYYY-MM-DDTHH:MM".
    Return tuple: (dt_obj_or_None, error_message_or_None, is_past_bool, now_iso, requested_iso)
    dt_obj will be timezone-aware using DEFAULT_TZ_NAME when zoneinfo is available.
    """
    now = _now_in_default_tz()
    now_iso = now.isoformat()

    if not dt_str or not dt_str.strip():
        return None, "Empty datetime", True, now_iso, None

    s = dt_str.strip()
    # allow user to type 'today' or 'tonight' is not handled here - must be explicit date
    # normalize separator
    if "T" not in s:
        s = s.replace(" ", "T")

    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        return None, "Invalid datetime format. Use YYYY-MM-DD HH:MM", True, now_iso, None

    # attach timezone (make aware)
    if ZONE_INFO_AVAILABLE:
        tz = ZoneInfo(DEFAULT_TZ_NAME)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
    else:
        # fallback: if dt has tzinfo convert to naive local time (best-effort)
        if dt.tzinfo is not None:
            dt = dt.astimezone().replace(tzinfo=None)

    requested_iso = dt.isoformat() if (dt is not None) else None

    # Now compare aware vs aware OR naive vs naive consistently using _now_in_default_tz()
    is_past = dt < now
    return dt, None if not is_past else "Datetime is in the past", is_past, now_iso, requested_iso

def _parse_datetime_with_tz(dt_str: str):
    """
    Parse datetime string into a timezone-aware datetime object.
    Accepts formats:
      - "YYYY-MM-DD HH:MM"
      - "YYYY-MM-DDTHH:MM"
    Returns datetime in DEFAULT_TZ_NAME timezone.
    Raises ValueError if invalid.
    """
    if not dt_str:
        raise ValueError("empty_datetime")

    # normalize separators
    s = dt_str.strip()
    if "T" not in s:
        s = s.replace(" ", "T")

    try:
        dt = datetime.fromisoformat(s)
    except Exception:
        raise ValueError("invalid_datetime_format")

    # attach timezone
    if ZONE_INFO_AVAILABLE:
        tz = ZoneInfo(DEFAULT_TZ_NAME)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
        else:
            dt = dt.astimezone(tz)
    else:
        # fallback: remove tzinfo and compare naively
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt

def _now_in_default_tz():
    if ZONE_INFO_AVAILABLE:
        return datetime.now(ZoneInfo(DEFAULT_TZ_NAME))
    else:
        return datetime.now()

if "clear_manual_after" in st.session_state and st.session_state["clear_manual_after"]:
    # clear the manual booking fields BEFORE widgets are instantiated
    st.session_state["manual_dt_iso"] = ""
    st.session_state["manual_name"] = ""
    st.session_state["manual_contact"] = ""
    st.session_state["manual_seating"] = ""
    st.session_state["manual_rest_select"] = "-- choose --"
    st.session_state["manual_party_size"] = 2
    # reset the flag
    st.session_state["clear_manual_after"] = False

# Ensure session state keys exist
if "history" not in st.session_state:
    st.session_state.history = []
if "chat_display" not in st.session_state:
    st.session_state.chat_display = []

# Show any pending success/error from last action BEFORE widgets are rendered
if st.session_state.get("last_success"):
    st.success(st.session_state["last_success"])
    # optional: also show JSON details if present
    if st.session_state.get("last_success_payload"):
        st.json(st.session_state["last_success_payload"])
    # clear after showing
    st.session_state["last_success"] = None
    st.session_state["last_success_payload"] = None

if st.session_state.get("last_error"):
    st.error(st.session_state["last_error"])
    if st.session_state.get("last_error_payload"):
        st.json(st.session_state["last_error_payload"])
    st.session_state["last_error"] = None
    st.session_state["last_error_payload"] = None

# Safe clear flag handling (clear manual inputs before widget creation)
if st.session_state.get("clear_manual_after", False):
    st.session_state["manual_dt_iso"] = ""
    st.session_state["manual_name"] = ""
    st.session_state["manual_contact"] = ""
    st.session_state["manual_seating"] = ""
    st.session_state["manual_rest_select"] = "-- choose --"
    st.session_state["manual_party_size"] = 2
    st.session_state["clear_manual_after"] = False


# -----------------------
# DB init & data-layer (same as before, with corrected variable names)
# -----------------------
def init_db(db_path: str = DB_PATH):
    with closing(sqlite3.connect(db_path)) as con:
        cur = con.cursor()
        cur.execute("PRAGMA foreign_keys = ON;")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS restaurants (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            cuisines_json TEXT NOT NULL,
            address TEXT,
            city TEXT,
            capacity_max INTEGER NOT NULL,
            seating_types_json TEXT NOT NULL,
            opening_hour TEXT,
            closing_hour TEXT,
            avg_rating REAL
        )
        """)
        cur.execute("""
        CREATE TABLE IF NOT EXISTS reservations (
            id INTEGER PRIMARY KEY,
            reservation_code TEXT UNIQUE NOT NULL,
            restaurant_id INTEGER NOT NULL,
            datetime_iso TEXT NOT NULL,
            party_size INTEGER NOT NULL,
            user_name TEXT,
            contact TEXT,
            seating_type TEXT,
            status TEXT NOT NULL DEFAULT 'confirmed',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (restaurant_id) REFERENCES restaurants(id)
        )
        """)
        con.commit()
        cur.execute("SELECT COUNT(*) FROM restaurants")
        if cur.fetchone()[0] == 0:
            restaurants_seed = [
                # Delhi (10)
                ("Sakura Sky Lounge", json.dumps(["Japanese", "Sushi"]), "HSR Layout, Delhi", "Delhi", 50, json.dumps(["rooftop", "outdoor"]), "18:00", "23:30", 4.6),
                ("Trattoria Roma", json.dumps(["Italian", "Pasta"]), "Connaught Place, Delhi", "Delhi", 60, json.dumps(["indoor", "outdoor"]), "11:00", "23:00", 4.4),
                ("Skyline Rooftop", json.dumps(["Modern Indian", "Fusion", "Sushi"]), "HSR Layout, Delhi", "Delhi", 120, json.dumps(["rooftop", "private"]), "17:00", "01:00", 4.7),
                ("Jazz & Dine", json.dumps(["American", "Barbecue"]), "Hauz Khas, Delhi", "Delhi", 80, json.dumps(["indoor", "live-music"]), "12:00", "23:00", 4.1),
                ("La Petite", json.dumps(["French", "Bakery"]), "GK-II, Delhi", "Delhi", 25, json.dumps(["indoor", "patio"]), "08:00", "21:00", 4.8),
                ("Curry Leaf", json.dumps(["North Indian", "Mughlai"]), "Rajouri Garden, Delhi", "Delhi", 100, json.dumps(["family", "indoor"]), "11:00", "23:00", 4.3),
                ("Bao Bar", json.dumps(["Asian", "Chinese", "Dimsum"]), "Khan Market, Delhi", "Delhi", 55, json.dumps(["indoor", "casual"]), "12:00", "23:00", 4.5),
                ("The Terrace Grill", json.dumps(["Continental", "Steakhouse"]), "CP, Delhi", "Delhi", 90, json.dumps(["rooftop", "bar"]), "17:00", "00:00", 4.6),
                ("Tandoor Tales", json.dumps(["Punjabi", "Tandoori"]), "Karol Bagh, Delhi", "Delhi", 85, json.dumps(["indoor", "family"]), "11:00", "23:00", 4.2),
                ("Masala Republic", json.dumps(["Indian", "Fusion"]), "Saket, Delhi", "Delhi", 70, json.dumps(["fine-dine", "modern"]), "12:00", "23:30", 4.5),

                # Mumbai (10)
                ("The Bombay Brasserie", json.dumps(["Indian", "Seafood"]), "Bandra, Mumbai", "Mumbai", 95, json.dumps(["indoor", "bar"]), "12:00", "00:00", 4.6),
                ("Pasta Street", json.dumps(["Italian"]), "Lower Parel, Mumbai", "Mumbai", 50, json.dumps(["indoor", "family"]), "11:00", "23:00", 4.3),
                ("Oceanside Diner", json.dumps(["Seafood", "Continental"]), "Juhu Beach, Mumbai", "Mumbai", 120, json.dumps(["seaside", "outdoor"]), "18:00", "01:00", 4.7),
                ("Saffron Soul", json.dumps(["Indian", "Biryani"]), "Andheri, Mumbai", "Mumbai", 75, json.dumps(["buffet", "indoor"]), "12:00", "23:30", 4.4),
                ("Cafe de Arts", json.dumps(["Cafe", "Bakery"]), "Colaba, Mumbai", "Mumbai", 40, json.dumps(["art-cafe", "casual"]), "08:00", "21:00", 4.5),
                ("Zen Izakaya", json.dumps(["Japanese", "Sushi"]), "BKC, Mumbai", "Mumbai", 60, json.dumps(["rooftop", "sushi-bar"]), "18:00", "00:00", 4.7),
                ("Tap & Barrel", json.dumps(["Pub", "Finger Food"]), "Powai, Mumbai", "Mumbai", 110, json.dumps(["pub", "sports"]), "17:00", "01:00", 4.3),
                ("Le Ciel", json.dumps(["French", "Continental"]), "Nariman Point, Mumbai", "Mumbai", 90, json.dumps(["fine-dine", "romantic"]), "19:00", "23:30", 4.8),
                ("Kebab Kingdom", json.dumps(["North Indian", "Grill"]), "Kurla, Mumbai", "Mumbai", 80, json.dumps(["casual", "family"]), "11:00", "23:00", 4.2),
                ("Green Bowl", json.dumps(["Vegan", "Healthy"]), "Bandra, Mumbai", "Mumbai", 45, json.dumps(["indoor", "garden"]), "09:00", "22:00", 4.5),

                # Bengaluru (10)
                ("Cloud 9 Terrace", json.dumps(["Continental", "Fusion"]), "Indiranagar, Bengaluru", "Bengaluru", 100, json.dumps(["rooftop", "bar"]), "18:00", "00:00", 4.6),
                ("Rasa Rasoi", json.dumps(["South Indian", "Traditional"]), "Jayanagar, Bengaluru", "Bengaluru", 60, json.dumps(["indoor", "family"]), "07:30", "22:30", 4.4),
                ("Grill House 88", json.dumps(["BBQ", "Steakhouse"]), "Koramangala, Bengaluru", "Bengaluru", 85, json.dumps(["outdoor", "barbecue"]), "12:00", "23:00", 4.5),
                ("Tapri Tales", json.dumps(["Cafe", "Tea"]), "Whitefield, Bengaluru", "Bengaluru", 40, json.dumps(["indoor", "casual"]), "08:00", "21:00", 4.3),
                ("The Wok Lab", json.dumps(["Asian", "Thai"]), "HSR Layout, Bengaluru", "Bengaluru", 70, json.dumps(["indoor", "family"]), "11:00", "23:00", 4.4),
                ("Elora Lounge", json.dumps(["Mediterranean", "Tapas"]), "Indiranagar, Bengaluru", "Bengaluru", 120, json.dumps(["rooftop", "live-music"]), "17:00", "01:00", 4.7),
                ("Cafe Nilgiri", json.dumps(["Coffee", "Desserts"]), "MG Road, Bengaluru", "Bengaluru", 30, json.dumps(["cafe", "quiet"]), "09:00", "22:00", 4.6),
                ("Korma Kafe", json.dumps(["Indian", "Mughlai"]), "BTM Layout, Bengaluru", "Bengaluru", 75, json.dumps(["indoor", "buffet"]), "12:00", "23:00", 4.3),
                ("Urban Spice", json.dumps(["Continental", "Fusion"]), "JP Nagar, Bengaluru", "Bengaluru", 90, json.dumps(["fine-dine", "family"]), "12:00", "23:30", 4.5),
                ("The Sizzler Pit", json.dumps(["Sizzlers", "Grill"]), "Koramangala, Bengaluru", "Bengaluru", 70, json.dumps(["casual", "indoor"]), "11:00", "23:00", 4.2),

                # Pune (5)
                ("Little Italy", json.dumps(["Italian", "Pizza"]), "Koregaon Park, Pune", "Pune", 50, json.dumps(["indoor", "family"]), "11:00", "23:00", 4.5),
                ("BBQ Ville", json.dumps(["BBQ", "Grill"]), "Viman Nagar, Pune", "Pune", 100, json.dumps(["outdoor", "barbecue"]), "12:00", "23:30", 4.4),
                ("The French Door", json.dumps(["French", "European"]), "Baner, Pune", "Pune", 60, json.dumps(["patio", "romantic"]), "18:00", "23:00", 4.6),
                ("Poha Junction", json.dumps(["Maharashtrian", "Breakfast"]), "Kothrud, Pune", "Pune", 35, json.dumps(["casual", "cafe"]), "07:00", "12:00", 4.3),
                ("The Spice Den", json.dumps(["Indian", "Chinese"]), "Hinjewadi, Pune", "Pune", 90, json.dumps(["indoor", "family"]), "11:00", "23:00", 4.2),

                # Hyderabad (5)
                ("Biryani Mahal", json.dumps(["Hyderabadi", "Biryani"]), "Banjara Hills, Hyderabad", "Hyderabad", 120, json.dumps(["indoor", "family"]), "11:00", "23:30", 4.7),
                ("Noodle Republic", json.dumps(["Asian", "Chinese"]), "Hitech City, Hyderabad", "Hyderabad", 75, json.dumps(["indoor", "casual"]), "12:00", "23:00", 4.3),
                ("Kebab-e-Khaas", json.dumps(["North Indian", "Grill"]), "Secunderabad, Hyderabad", "Hyderabad", 90, json.dumps(["indoor", "barbecue"]), "11:00", "23:30", 4.5),
                ("Sky High Bistro", json.dumps(["Continental", "Bar"]), "Gachibowli, Hyderabad", "Hyderabad", 150, json.dumps(["rooftop", "live-music"]), "18:00", "01:00", 4.8),
                ("The Sweet Spot", json.dumps(["Desserts", "Bakery"]), "Jubilee Hills, Hyderabad", "Hyderabad", 40, json.dumps(["cafe", "casual"]), "09:00", "21:00", 4.6),

                # Chennai (5)
                ("Marina Bay Diner", json.dumps(["Seafood", "South Indian"]), "Besant Nagar, Chennai", "Chennai", 100, json.dumps(["seaside", "outdoor"]), "12:00", "23:30", 4.6),
                ("Idli Express", json.dumps(["South Indian", "Fast Food"]), "T Nagar, Chennai", "Chennai", 30, json.dumps(["casual", "takeaway"]), "06:30", "22:00", 4.2),
                ("Bella Napoli", json.dumps(["Italian", "Pizza"]), "Nungambakkam, Chennai", "Chennai", 65, json.dumps(["indoor", "family"]), "12:00", "23:00", 4.5),
                ("Spice Route", json.dumps(["Indian", "Thai"]), "Velachery, Chennai", "Chennai", 85, json.dumps(["fine-dine", "romantic"]), "12:00", "23:00", 4.4),
                ("The Choco Room", json.dumps(["Cafe", "Desserts"]), "Anna Nagar, Chennai", "Chennai", 40, json.dumps(["cafe", "casual"]), "10:00", "22:00", 4.3),

                # +5 Extra entries to reach 50 (varied cities)
                ("Rooftop Mirage", json.dumps(["Sushi", "Japanese"]), "HSR Layout, Bengaluru", "Bengaluru", 45, json.dumps(["rooftop", "romantic"]), "18:00", "23:30", 4.6),
                ("Monsoon Grill", json.dumps(["Seafood", "Grill"]), "Bandra, Mumbai", "Mumbai", 85, json.dumps(["outdoor", "seaside"]), "17:00", "00:30", 4.4),
                ("Heritage Bites", json.dumps(["Indian", "Street Food"]), "Old Delhi, Delhi", "Delhi", 60, json.dumps(["casual", "outdoor"]), "10:00", "23:00", 4.2),
                ("Vine & Dine", json.dumps(["Mediterranean", "Wine Bar"]), "Koramangala, Bengaluru", "Bengaluru", 55, json.dumps(["indoor", "wine-bar"]), "18:00", "23:30", 4.7),
                ("Sunset Cafe", json.dumps(["Cafe", "Light Bites"]), "Juhu, Mumbai", "Mumbai", 35, json.dumps(["seaside", "patio"]), "07:00", "21:00", 4.4),
            ]

            cur.executemany("""
                INSERT INTO restaurants (name, cuisines_json, address, city, capacity_max, seating_types_json, opening_hour, closing_hour, avg_rating)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, restaurants_seed)
            con.commit()

def fetch_restaurants_from_db(
    cuisines: Optional[List[str]] = None,
    locations: Optional[List[str]] = None,
    min_capacity: Optional[int] = None,
    max_capacity: Optional[int] = None,
    seating_types: Optional[List[str]] = None
) -> List[Dict[str, Any]]:
    cuisines_set = set([c.lower() for c in cuisines]) if cuisines else None
    locations_set = set([l.lower() for l in locations]) if locations else None
    seating_set = set([s.lower() for s in seating_types]) if seating_types else None

    rows: List[Dict[str, Any]] = []
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT id, name, cuisines_json, address, city, capacity_max, seating_types_json, opening_hour, closing_hour, avg_rating FROM restaurants")
        for row in cur.fetchall():
            rid, name, cuisines_json, address, city, capacity_max, seating_json, opening_hour, closing_hour, rating = row
            rest_cuisines = json.loads(cuisines_json)
            rest_seating = json.loads(seating_json)
            location_text = f"{address or ''} {city or ''}".strip().lower()

            if cuisines_set:
                rest_cuisine_set = set([rc.lower() for rc in rest_cuisines])
                if not (rest_cuisine_set & cuisines_set):
                    continue
            if locations_set:
                if not any(loc in location_text for loc in locations_set):
                    continue
            if min_capacity is not None and capacity_max < min_capacity:
                continue
            if max_capacity is not None and capacity_max > max_capacity:
                pass
            if seating_set:
                rest_seating_set = set([s.lower() for s in rest_seating])
                if not (rest_seating_set & seating_set):
                    continue

            rows.append({
                "id": rid,
                "name": name,
                "cuisines": rest_cuisines,
                "address": address,
                "city": city,
                "capacity_max": capacity_max,
                "seating_types": rest_seating,
                "opening_hour": opening_hour,
                "closing_hour": closing_hour,
                "avg_rating": rating
            })
    rows.sort(key=lambda r: r.get("avg_rating", 0), reverse=True)
    return rows

def check_availability_db(restaurant_id: int, datetime_iso: str, party_size: int) -> Dict[str, Any]:
    """
    Enhanced availability check that:
     - parses & normalizes datetime using _parse_datetime_with_tz()
     - rejects past datetimes
     - checks capacity at the exact datetime_iso slot
    """
    # 1) parse & timezone-normalize the requested datetime
    try:
        req_dt = _parse_datetime_with_tz(datetime_iso)
    except ValueError:
        return {"error": "invalid_datetime_format", "available": False}

    # 2) reject past datetimes
    now = _now_in_default_tz()
    if req_dt < now:
        # return both times for debugging/UX
        return {"error": "datetime_in_past", "available": False, "now": now.isoformat(), "requested": req_dt.isoformat()}

    # 3) existing capacity checks
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT capacity_max FROM restaurants WHERE id=?", (restaurant_id,))
        r = cur.fetchone()
        if not r:
            return {"error": "restaurant_not_found", "available": False}
        capacity_max = r[0]
        cur.execute("SELECT SUM(party_size) FROM reservations WHERE restaurant_id=? AND datetime_iso=? AND status='confirmed'",
                    (restaurant_id, datetime_iso))
        used = cur.fetchone()[0] or 0
        seats_left = max(0, capacity_max - used)
        available = (seats_left >= party_size)
        return {"available": available, "seats_left": seats_left, "capacity_max": capacity_max}

def make_reservation_db(
    restaurant_id: int,
    datetime_iso: str,
    party_size: int,
    user_name: str,
    contact: str,
    seating_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Atomic reservation: re-check that requested time isn't in the past; then attempt booking.
    """
    try:
        req_dt = _parse_datetime_with_tz(datetime_iso)
    except ValueError:
        return {"error": "invalid_datetime_format"}
    
    # 2) reject past datetimes
    now = _now_in_default_tz()
    if req_dt < now:
        return {"error": "datetime_in_past", "message": f"Requested time {req_dt.isoformat()} is before current time {now.isoformat()}"}


    con = sqlite3.connect(DB_PATH)
    try:
        con.isolation_level = "EXCLUSIVE"
        cur = con.cursor()
        cur.execute("BEGIN EXCLUSIVE")
        cur.execute("SELECT capacity_max FROM restaurants WHERE id=?", (restaurant_id,))
        r = cur.fetchone()
        if not r:
            con.rollback()
            return {"error": "restaurant_not_found"}
        capacity_max = r[0]
        cur.execute("SELECT SUM(party_size) FROM reservations WHERE restaurant_id=? AND datetime_iso=? AND status='confirmed'",
                    (restaurant_id, datetime_iso))
        used = cur.fetchone()[0] or 0
        seats_left = max(0, capacity_max - used)
        if seats_left < party_size:
            con.rollback()
            return {"error": "no_availability", "seats_left": seats_left}
        reservation_code = f"R{uuid.uuid4().hex[:8].upper()}"
        cur.execute("""
            INSERT INTO reservations
            (reservation_code, restaurant_id, datetime_iso, party_size, user_name, contact, seating_type, status, created_at)
            VALUES (?,?,?,?,?,?,?, 'confirmed', datetime('now'))
        """, (reservation_code, restaurant_id, datetime_iso, party_size, user_name, contact, seating_type))
        con.commit()
        return {"reservation_code": reservation_code, "status": "confirmed"}
    except Exception as e:
        con.rollback()
        return {"error": "db_error", "message": str(e)}
    finally:
        con.close()

def cancel_reservation_db(reservation_code: str) -> Dict[str, Any]:
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("SELECT status FROM reservations WHERE reservation_code=?", (reservation_code,))
        row = cur.fetchone()
        if not row:
            return {"error": "not_found"}
        if row[0] == "cancelled":
            return {"error": "already_cancelled"}
        cur.execute("UPDATE reservations SET status='cancelled' WHERE reservation_code=?", (reservation_code,))
        con.commit()
        return {"status": "cancelled", "reservation_code": reservation_code}

def list_reservations_by_contact(contact: str) -> List[Dict[str, Any]]:
    with closing(sqlite3.connect(DB_PATH)) as con:
        cur = con.cursor()
        cur.execute("""
            SELECT r.reservation_code, r.datetime_iso, r.party_size, r.user_name, r.status, rest.name
            FROM reservations r JOIN restaurants rest ON r.restaurant_id = rest.id
            WHERE r.contact = ?
            ORDER BY r.datetime_iso DESC
        """, (contact,))
        items = []
        for row in cur.fetchall():
            code, dt_iso, party, name, status, rest_name = row
            items.append({
                "reservation_code": code,
                "restaurant": rest_name,
                "datetime_iso": dt_iso,
                "party_size": party,
                "user_name": name,
                "status": status
            })
        return items

# -----------------------
# Agent tool metadata & tool execution
# -----------------------
def tools_registry():
    return [
        {
            "type": "function",
            "function": {
                "name": "getRestaurants",
                "description": "Search restaurants by cuisines, locations, min_capacity, max_capacity, seating_types.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "cuisines": {"type": "array", "items": {"type": "string"}},
                        "locations": {"type": "array", "items": {"type": "string"}},
                        "min_capacity": {"type": "integer"},
                        "max_capacity": {"type": "integer"},
                        "seating_types": {"type": "array", "items": {"type": "string"}}
                    },
                    "required": []
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "checkAvailability",
                "description": "Check availability for restaurant at datetime_iso for given party_size.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "restaurant_id": {"type": "integer"},
                        "datetime_iso": {"type": "string"},
                        "party_size": {"type": "integer"}
                    },
                    "required": ["restaurant_id", "datetime_iso", "party_size"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "makeReservation",
                "description": "Make an atomic reservation. Requires restaurant_id, datetime_iso, party_size, user_name, contact.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "restaurant_id": {"type": "integer"},
                        "datetime_iso": {"type": "string"},
                        "party_size": {"type": "integer"},
                        "user_name": {"type": "string"},
                        "contact": {"type": "string"},
                        "seating_type": {"type": "string"}
                    },
                    "required": ["restaurant_id", "datetime_iso", "party_size", "user_name", "contact"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "cancelReservation",
                "description": "Cancel a previously made reservation by reservation_code.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "reservation_code": {"type": "string"}
                    },
                    "required": ["reservation_code"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "listReservations",
                "description": "List reservations by contact (phone/email).",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact": {"type": "string"}
                    },
                    "required": ["contact"]
                }
            }
        },
        {
            "type": "function",
            "function": {
                "name": "sendResponse",
                "description": "Final response (string) sent to the user to finish the turn.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "response": {"type": "string"}
                    },
                    "required": ["response"]
                }
            }
        }
    ]

def execute_tool(tool_name: str, args: Dict[str, Any]) -> Any:
    if tool_name == "getRestaurants":
        return fetch_restaurants_from_db(
            cuisines=args.get("cuisines"),
            locations=args.get("locations"),
            min_capacity=args.get("min_capacity"),
            max_capacity=args.get("max_capacity"),
            seating_types=args.get("seating_types")
        )
    if tool_name == "checkAvailability":
        return check_availability_db(
            restaurant_id=int(args["restaurant_id"]),
            datetime_iso=str(args["datetime_iso"]),
            party_size=int(args["party_size"])
        )
    if tool_name == "makeReservation":
        # Accept LLM-provided datetime strings like "today 7pm", "7pm", "2025-11-10 19:00"
        raw_dt = str(args.get("datetime_iso") or args.get("datetime") or "")
        normalized_iso, debug = _normalize_natural_datetime(raw_dt)
        # attach debug info in server logs and also return to caller when failing
        if normalized_iso is None:
            # return a structured error so the LLM can ask a clarifying question
            return {"error": "unparseable_datetime", "debug": debug}
        # call backend using normalized ISO (YYYY-MM-DD HH:MM)
        return make_reservation_db(
            restaurant_id=int(args["restaurant_id"]),
            datetime_iso=normalized_iso,
            party_size=int(args["party_size"]),
            user_name=str(args["user_name"]),
            contact=str(args["contact"]),
            seating_type=args.get("seating_type")
        )
    if tool_name == "cancelReservation":
        return cancel_reservation_db(args["reservation_code"])
    if tool_name == "listReservations":
        return list_reservations_by_contact(args["contact"])
    if tool_name == "sendResponse":
        return args.get("response")
    return {"error": "unknown_tool"}

# -----------------------
# Multi-hop process (non-interactive)
# -----------------------
def process_user_input(user_input: str, conversation_history: List[Dict[str, Any]], model: str = "gpt-4o"):
    """
    Sends user_input + conversation_history to model with tool registry,
    executes any tool_calls the model returns (ensures assistant->tool pairing),
    and continues multi-hop until model calls sendResponse or returns no tool_calls.
    Returns the final assistant text and updated conversation_history.
    """
    client = OpenAI(api_key=OPENAI_API_KEY)
    tools = tools_registry()

    system_message = (
        "You are a reservation assistant. FIRST decide the user's intent: one of "
        "['search', 'check_availability', 'reserve', 'cancel', 'list']. Then call tools "
        "(getRestaurants, checkAvailability, makeReservation, cancelReservation, listReservations) as needed. "
        "Never call makeReservation without first calling checkAvailability for that restaurant+slot. "
        "When you have a final user-facing reply, call sendResponse(response). Use ISO datetime 'YYYY-MM-DD HH:MM'. "
        "If ambiguous, ask a brief clarifying question."
    )

    # build messages
    messages = [{"role": "system", "content": system_message}]
    messages.extend(conversation_history)
    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        tools=tools,
        tool_choice="auto"
    )

    conversation_history.append({"role": "user", "content": user_input})
    response_message = response.choices[0].message

    # if no tool_calls, treat content as final assistant response
    if not getattr(response_message, "tool_calls", None):
        final_text = (response_message.content or "").strip()
        if final_text:
            conversation_history.append({"role": "assistant", "content": final_text})
        return final_text, conversation_history

    # process tool calls sequentially just like the CLI agent
    while getattr(response_message, "tool_calls", None):
        tool_calls = response_message.tool_calls
        for tool_call in tool_calls:
            fname = tool_call.function.name
            args = json.loads(tool_call.function.arguments or "{}")

            # append assistant message with tool_call
            messages.append({
                "role": "assistant",
                "content": response_message.content or "",
                "tool_calls": [tool_call]
            })
            conversation_history.append({
                "role": "assistant",
                "content": response_message.content or "",
                "tool_calls": [tool_call]
            })

            # execute tool
            result = execute_tool(fname, args)
            tool_content = json.dumps(result) if not isinstance(result, str) else result

            # append tool message
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_content
            })
            conversation_history.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": tool_content
            })

            # If tool was sendResponse, print and finish
            if fname == "sendResponse":
                # result is the response string (tool_content)
                conversation_history.append({"role": "assistant", "content": tool_content})
                return tool_content, conversation_history

        # call model for next hop
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto"
        )
        response_message = response.choices[0].message
        if not getattr(response_message, "tool_calls", None):
            final_text = (response_message.content or "").strip()
            if final_text:
                conversation_history.append({"role": "assistant", "content": final_text})
            return final_text, conversation_history

    # fallback
    return "", conversation_history

# -----------------------
# Streamlit UI
# -----------------------
st.set_page_config(page_title="Reservation Agent", layout="wide")
st.title("🍽️ Reservation Agent (Streamlit + LLM)")

init_db(DB_PATH)

# Session state
if "history" not in st.session_state:
    st.session_state.history = []  # conversation history messages
if "chat_display" not in st.session_state:
    st.session_state.chat_display = []

col1, col2 = st.columns([2, 1])

with col1:
    st.subheader("Chat (LLM-driven)")
    user_query = st.text_area("Your request", placeholder="e.g., Recommend me a rooftop restaurant near HSR Layout, Delhi that serves Sushi for 4 on 2025-11-15 19:00")
    if st.button("Send"):
        if not OPENAI_API_KEY:
            st.error("OPENAI_API_KEY not set in environment.")
        else:
            assistant_text, st.session_state.history = process_user_input(user_query, st.session_state.history)
            st.session_state.chat_display.append(("User", user_query))
            st.session_state.chat_display.append(("Assistant", assistant_text or "—"))
    if st.session_state.chat_display:
        for speaker, msg in st.session_state.chat_display[::-1]:
            if speaker == "Assistant":
                st.markdown(f"**🤖 {speaker}:** {msg}")
            else:
                st.markdown(f"**👤 {speaker}:** {msg}")

with col2:
    st.subheader("Manual Booking")

    # Fetch restaurants for dropdown
    restaurants = fetch_restaurants_from_db()
    rest_map = {r["name"]: r for r in restaurants}
    rest_names = ["-- choose --"] + [r["name"] for r in restaurants]
    sel_rest = st.selectbox("Restaurant", rest_names, key="manual_rest_select")

    party_size = st.number_input("Party size", min_value=1, max_value=50, value=2, key="manual_party_size")

    # Datetime input (text) - user supplies YYYY-MM-DD HH:MM
    dt_iso = st.text_input("Datetime (YYYY-MM-DD HH:MM)", value="", key="manual_dt_iso")

    # client-side parse + validation (live feedback)
    parsed_dt, parse_err, is_past, now_iso, requested_iso = _parse_datetime_for_ui(dt_iso)

    # show the server/app current time in DEFAULT_TZ_NAME and the parsed requested time
    st.markdown("**Debug times (for diagnosis):**")
    st.markdown(f"- Current (app) time: `{now_iso}` ({DEFAULT_TZ_NAME})")
    st.markdown(f"- Parsed requested time: `{requested_iso or '—'}`")

    if parse_err:
        st.warning(parse_err)
    else:
        # show friendly confirmation of parsed datetime in default tz
        display_dt = parsed_dt.isoformat() if ZONE_INFO_AVAILABLE else parsed_dt.strftime("%Y-%m-%d %H:%M")
        if is_past:
            st.error(f"Requested datetime {display_dt} is in the past. Please pick a future time.")
        else:
            st.info(f"Requested datetime parsed as: {display_dt} ({DEFAULT_TZ_NAME})")

    name = st.text_input("Your name", key="manual_name")
    contact = st.text_input("Contact (phone/email)", key="manual_contact")
    seating = st.text_input("Seating type (optional, e.g., rooftop)", key="manual_seating")

    # CHECK AVAILABILITY
    if st.button("Check Availability", key="manual_check"):
        if sel_rest == "-- choose --":
            st.warning("Pick a restaurant first.")
        elif parse_err:
            st.warning("Fix the datetime first: " + (parse_err or "invalid"))
        elif is_past:
            st.warning("Cannot check availability for a past datetime.")
        else:
            rest = rest_map[sel_rest]
            res = check_availability_db(rest["id"], dt_iso, party_size)
            st.json(res)

    # MAKE RESERVATION
    if st.button("Make Reservation", key="manual_make"):
        if sel_rest == "-- choose --":
            st.warning("Pick a restaurant first.")
        elif not name or not contact:
            st.warning("Please enter your name and contact before booking.")
        elif parse_err:
            st.warning("Fix the datetime first: " + (parse_err or "invalid"))
        elif is_past:
            st.error("You cannot make a reservation for a past datetime. Please pick a future date/time.")
        else:
            rest = rest_map[sel_rest]
            # Proceed with backend reservation (server-side will still re-check)
           # Proceed with backend reservation (server-side will still re-check)
            res = make_reservation_db(rest["id"], dt_iso, party_size, name, contact, seating or None)

            # Debug: always print the backend response to the Streamlit logs (helpful)
            st.write("Debug: reservation response ->", res)

            if res.get("error") == "invalid_datetime_format":
                # capture the error in session and rerun so top-of-page displays it
                st.session_state["last_error"] = "Server rejected datetime format. Use YYYY-MM-DD HH:MM."
                st.session_state["last_error_payload"] = res
                st.rerun()
            elif res.get("error") == "datetime_in_past":
                st.session_state["last_error"] = "Server rejected the request: requested datetime is in the past."
                st.session_state["last_error_payload"] = res
                st.rerun()
            elif res.get("error") == "no_availability":
                st.session_state["last_error"] = f"No availability. Seats left: {res.get('seats_left')}"
                st.session_state["last_error_payload"] = res
                st.rerun()
            elif res.get("reservation_code"):
                # Save success message & payload to session_state BEFORE rerun so it survives reload
                success_msg = f"Reservation confirmed: {res['reservation_code']} at {rest['name']} on {dt_iso} for {party_size} people."
                st.session_state["last_success"] = success_msg
                st.session_state["last_success_payload"] = res
                # set clear flag so inputs are cleared on next run
                st.session_state["clear_manual_after"] = True
                # rerun (will show the success message at the top due to the code in step 1)
                st.rerun()
            else:
                # Unexpected fallback — show error details
                st.session_state["last_error"] = "Failed to make reservation (unknown error)."
                st.session_state["last_error_payload"] = res
                st.rerun()

    
    st.write("---")
    st.subheader("Reservations (by contact)")
    contact_q = st.text_input("Contact to list/cancel", value="")
    cancel_code = st.text_input("Reservation code to cancel", value="", key="cancel_code_input")
    if st.button("List Reservations"):
        if not contact_q:
            st.warning("Provide contact.")
        else:
            items = list_reservations_by_contact(contact_q)
            st.json(items)
    if st.button("Cancel Reservation"):
        if not cancel_code:
            st.warning("Enter reservation code above.")
        else:
            out = cancel_reservation_db(cancel_code)
            # Save success/error to show after rerun
            if out.get("status") == "cancelled":
                st.session_state["last_success"] = f"Reservation {cancel_code} cancelled."
                st.session_state["last_success_payload"] = out
                # set flag to clear the cancel input on next run
                st.session_state["clear_cancel_after"] = True
                st.rerun()
            else:
                # surface the error and keep the input so user can correct it
                st.session_state["last_error"] = f"Failed to cancel: {out.get('error', out)}"
                st.session_state["last_error_payload"] = out
                st.rerun()
    

st.write("---")
st.caption("This demo uses the LLM to parse intent and call tools; manual form calls DB functions directly for convenience.")
