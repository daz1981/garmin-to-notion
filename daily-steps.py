from datetime import date, timedelta
from garminconnect import Garmin
from notion_client import Client
from dotenv import load_dotenv
import os

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

def get_all_daily_steps(garmin):
    """
    Get last x days of daily step count data from Garmin Connect.
    """
    startdate = date.today() - timedelta(days=1)
    daterange = [startdate + timedelta(days=x) 
                 for x in range((date.today() - startdate).days)] # excl. today
    daily_steps = []
    for d in daterange:
        daily_steps += garmin.get_daily_steps(d.isoformat(), d.isoformat())
    return daily_steps

def daily_steps_exist(client, database_id, activity_date):
    """
    Check if daily step count already exists in the Notion database.
    """
    query = client.databases.query(
        database_id=database_id,
        filter={
            "and": [
                {"property": "Date", "date": {"equals": activity_date}},
                {"property": "Activity Type", "title": {"equals": "Walking"}}
            ]
        }
    )
    results = query['results']
    return results[0] if results else None

def steps_need_update(existing_steps, new_steps):
    """
    Compare existing steps data with imported data to determine if an update is needed.
    """
    existing_props = existing_steps['properties']
    activity_type = "Walking"
    
    return (
        existing_props['Total Steps']['number'] != new_steps.get('totalSteps') or
        existing_props['Step Goal']['number'] != new_steps.get('stepGoal') or
        existing_props['Total Distance (km)']['number'] != new_steps.get('totalDistance') or
        existing_props['Activity Type']['title'] != activity_type
    )

def update_daily_steps(client, existing_steps, new_steps):
    """
    Update an existing daily steps entry in the Notion database with new data.
    """
    total_distance = new_steps.get('totalDistance')
    if total_distance is None:
        total_distance = 0
    properties = {
        "Activity Type":  {"title": [{"text": {"content": "Walking"}}]},
        "Total Steps": {"number": new_steps.get('totalSteps')},
        "Step Goal": {"number": new_steps.get('stepGoal')},
        "Total Distance (km)": {"number": round(total_distance / 1000, 2)}
    }
    
    update = {
        "page_id": existing_steps['id'],
        "properties": properties,
    }
        
    client.pages.update(**update)

def create_daily_steps(client, database_id, steps):
    """
    Create a new daily steps entry in the Notion database.
    """
    total_distance = steps.get('totalDistance')
    if total_distance is None:
        total_distance = 0
    properties = {
        "Activity Type": {"title": [{"text": {"content": "Walking"}}]},
        "Date": {"date": {"start": steps.get('calendarDate')}},
        "Total Steps": {"number": steps.get('totalSteps')},
        "Step Goal": {"number": steps.get('stepGoal')},
        "Total Distance (km)": {"number": round(total_distance / 1000, 2)}
    }
    
    page = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    
    client.pages.create(**page)

def main():
    load_dotenv()

    # Initialize Garmin and Notion clients using environment variables
    garmin_email = os.getenv("GARMIN_EMAIL")
    garmin_password = os.getenv("GARMIN_PASSWORD")
    notion_token = os.getenv("NOTION_TOKEN")
    database_id = os.getenv("NOTION_STEPS_DB_ID")

    # Initialize Garmin client and login
    garmin = Garmin(garmin_email, garmin_password)
    garmin.login()
    client = Client(auth=notion_token)

    daily_steps = get_all_daily_steps(garmin)
    for steps in daily_steps:
        steps_date = steps.get('calendarDate')
        existing_steps = daily_steps_exist(client, database_id, steps_date)
        if existing_steps:
            if steps_need_update(existing_steps, steps):
                update_daily_steps(client, existing_steps, steps)
        else:
            create_daily_steps(client, database_id, steps)

if __name__ == '__main__':
    main()
