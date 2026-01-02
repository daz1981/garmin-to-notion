# sleep_data.py — adaptive version
# - Auto-detects your Sleep DB Title/Date properties
# - Optionally auto-creates standard sleep properties (set CREATE_MISSING_SLEEP_PROPERTIES=true)
# - Skips properties that do not exist in your DB (prevents Notion 400 errors)

import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from garminconnect import Garmin
from notion_client import Client

# ---------- Env & basic guards ----------
NOTION_TOKEN = os.environ.get("NOTION_TOKEN")
SLEEP_DB_ID  = os.environ.get("NOTION_SLEEP_DB_ID")
TZ = ZoneInfo(os.environ.get("TZ", "Europe/London"))
# Keep the Notion API version explicit for consistency
NOTION_VERSION = os.environ.get("Notion-Version", "2025-09-03")
# Set to 'true' to let the script add standard properties to your Sleep DB if they are missing
AUTO_CREATE = os.environ.get("CREATE_MISSING_SLEEP_PROPERTIES", "").lower() == "true"

if not NOTION_TOKEN:
    sys.exit("ERROR: NOTION_TOKEN is not set.")
if not SLEEP_DB_ID:
    sys.exit("ERROR: NOTION_SLEEP_DB_ID is not set.")

notion = Client(auth=NOTION_TOKEN, notion_version=NOTION_VERSION)

# ---------- Notion helpers ----------
def get_db_props(db_id: str) -> dict:
    db = notion.databases.retrieve(db_id)
    return db.get("properties", {})

# notion_helpers.py
from typing import Optional, Dict, Tuple
from notion_client import Client, APIResponseError

# Simple in-process cache
_DS_CACHE: Dict[str, Tuple[str, str]] = {}

def get_data_source_id(
    client: Client,
    database_id: str,
    prefer_name: Optional[str] = None
) -> str:
    """
    Resolve the data_source_id for a given Notion database (container).
    Optionally pick a data source by name. Caches the result.
    """
    cached = _DS_CACHE.get(database_id)
    if cached and (prefer_name is None or cached[1] == prefer_name):
        return cached[0]

    try:
        # In Notion 2025-09-03, databases.retrieve returns a container
        # object that includes a 'data_sources' array (id + name).
        db = client.databases.retrieve(database_id=database_id)
    except APIResponseError as e:
        raise RuntimeError(
            "Could not retrieve the database container from Notion. "
            "Check that the ID is a *database* ID (from the DB page URL), "
            "not a page/view/link ID, and that the database is shared with your integration."
        ) from e

    data_sources = db.get("data_sources") or []
    if not data_sources:
        raise RuntimeError(
            "No data sources found (or no permission). Make sure this is the ORIGINAL database "
            "(not a linked view) and that your integration is added via ⋯ → Connections."
        )

    chosen = None
    if prefer_name:
        for ds in data_sources:
            if ds.get("name") == prefer_name:
                chosen = ds
                break
        if not chosen:
            names = ", ".join(ds.get("name", "〈unnamed〉") for ds in data_sources)
            raise RuntimeError(f"Data source named '{prefer_name}' not found. Available: {names}")
    else:
        chosen = data_sources[0]

    ds_id = chosen["id"]
    _DS_CACHE[database_id] = (ds_id, chosen.get("name", ""))
    return ds_id

def find_title_prop(props: dict) -> str:
    """Find the database's Title property key (often 'Name')."""
    for key, val in props.items():
        if val.get("type") == "title":
            return key
    return "Name"  # fallback

def find_date_prop(props: dict) -> str | None:
    """Prefer a Date-typed property literally called 'Date'; otherwise use any Date-typed column."""
    if "Date" in props and props["Date"].get("type") == "date":
        return "Date"
    for key, val in props.items():
        if val.get("type") == "date":
            return key
    return None

def query_by_date(db_id: str, date_prop: str, date_iso: str) -> str | None:
    res = notion.databases.query(
        database_id=db_id,
        filter={"property": date_prop, "date": {"equals": date_iso}},
        page_size=1,
    )
    items = res.get("results", [])
    return items[0]["id"] if items else None

def ensure_sleep_schema(db_id: str, props: dict):
    """Optionally add standard sleep properties so future updates succeed without manual edits."""
    if not AUTO_CREATE:
        return
    notion.databases.update(db_id, properties=props)

# ---------- Garmin helpers ----------
def to_minutes(seconds):
    return round((seconds or 0) / 60)

def to_iso_z(dt_str):
    """Garmin returns timestamps with 'GMT' fields; normalize to UTC (Zulu) for Notion."""
    if not dt_str:
        return None
    s = dt_str.rstrip("Z")
    if "." in s:
        s = s.split(".")[0]
    return s + "Z"

# ---------- Main ----------
def main():
    # Use the "night of" yesterday in UK time (typical for sleep data)
    target_dt = (datetime.now(TZ) - timedelta(days=1)).date()
    target_str = target_dt.strftime("%Y-%m-%d")
    title = f"Sleep {target_str}"

    # Inspect DB schema
    db_props = get_db_props(SLEEP_DB_ID)
    title_prop = find_title_prop(db_props)
    date_prop = find_date_prop(db_props)
    if not date_prop:
        sys.exit(
            "ERROR: No Date property found in your Sleep database.\n"
            "Add a Date column (e.g., 'Date') of type Date in Notion,\n"
            "or set CREATE_MISSING_SLEEP_PROPERTIES=true to auto-create standard sleep columns."
        )

    # Optionally add a standard set of columns so future updates just work
    standard_props = {
        "Date": {"date": {}},
        "Score": {"number": {"format": "number"}},
        "Efficiency (%)": {"number": {"format": "number"}},
        "Total (min)": {"number": {"format": "number"}},
        "Deep (min)": {"number": {"format": "number"}},
        "REM (min)": {"number": {"format": "number"}},
        "Light (min)": {"number": {"format": "number"}},
        "Awake (min)": {"number": {"format": "number"}},
        "Bedtime": {"date": {}},
        "Wake time": {"date": {}},
        "HRV (ms)": {"number": {"format": "number"}},
    }
    ensure_sleep_schema(SLEEP_DB_ID, standard_props)

    # Garmin login (OAuth via Garth; tokens reused from ~/.garminconnect when available)
    client = Garmin(os.environ.get("GARMIN_EMAIL", ""), os.environ.get("GARMIN_PASSWORD", ""))
    client.login()

    # Fetch sleep & HRV for the target date
    sleep = client.get_sleep_data(target_str) or {}
    try:
        hrv = client.get_hrv_data(target_str) or {}
    except Exception:
        hrv = {}

    daily = (sleep.get("dailySleepDTO") or {}) if isinstance(sleep, dict) else {}

    total_min = to_minutes(daily.get("sleepTimeSeconds") or daily.get("durationInSeconds"))
    deep_min = to_minutes(daily.get("deepSleepSeconds"))
    rem_min = to_minutes(daily.get("remSleepSeconds"))
    light_min = to_minutes(daily.get("lightSleepSeconds"))
    awake_min = to_minutes(daily.get("awakeSleepSeconds"))
    score = daily.get("sleepScore")
    efficiency = daily.get("sleepEfficiency")
    bedtime_iso = to_iso_z(daily.get("sleepStartTimestampGMT") or daily.get("startTimeGMT"))
    waketime_iso = to_iso_z(daily.get("sleepEndTimestampGMT") or daily.get("endTimeGMT"))

    # HRV nightly average (best effort; devices vary)
    hrv_nightly = None
    if isinstance(hrv, dict):
        if isinstance(hrv.get("hrvSummary"), dict):
            for k in ("lastNightAvg", "avg", "average"):
                v = hrv["hrvSummary"].get(k)
                if isinstance(v, (int, float)):
                    hrv_nightly = round(v, 2)
                    break
        else:
            for k in ("lastNightAvg", "avg", "hrvValue", "average"):
                v = hrv.get(k)
                if isinstance(v, (int, float)):
                    hrv_nightly = round(v, 2)
                    break

    # Only write properties that exist in the DB (prevents 400s)
    def has_prop(name: str) -> bool:
        return name in db_props

    props = {date_prop: {"date": {"start": target_str}}}
    if has_prop("Total (min)"):    props["Total (min)"]    = {"number": total_min}
    if has_prop("Deep (min)"):     props["Deep (min)"]     = {"number": deep_min}
    if has_prop("REM (min)"):      props["REM (min)"]      = {"number": rem_min}
    if has_prop("Light (min)"):    props["Light (min)"]    = {"number": light_min}
    if has_prop("Awake (min)"):    props["Awake (min)"]    = {"number": awake_min}
    if has_prop("Score") and score is not None:
        props["Score"] = {"number": float(score)}
    if has_prop("Efficiency (%)") and efficiency is not None:
        props["Efficiency (%)"] = {"number": float(efficiency)}
    if has_prop("Bedtime") and bedtime_iso:
        props["Bedtime"] = {"date": {"start": bedtime_iso}}
    if has_prop("Wake time") and waketime_iso:
        props["Wake time"] = {"date": {"start": waketime_iso}}
    if has_prop("HRV (ms)") and hrv_nightly is not None:
        props["HRV (ms)"] = {"number": float(hrv_nightly)}

    # Upsert (update if exists, otherwise create)
    page_id = query_by_date(SLEEP_DB_ID, date_prop, target_str)
    if page_id:
        notion.pages.update(page_id=page_id, properties=props)
        print(f"[sleep] Updated page {page_id} for {target_str}")
    else:
        new_page = notion.pages.create(
            parent={"database_id": SLEEP_DB_ID},
            properties={title_prop: {"title": [{"text": {"content": title}}]}} | props,
            icon={"emoji": "😴"},
        )
        print(f"[sleep] Created page {new_page['id']} for {target_str}")

if __name__ == "__main__":
    main()
