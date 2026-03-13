"""
Google Fit API Integration for Health Companion
Fetches live heart rate, oxygen, steps, calories, weight from Google Fit
"""

import os, json, time, datetime
from pathlib import Path

TOKEN_FILE = "google_fit_token.json"

try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    GOOGLE_LIBS_AVAILABLE = False

SCOPES = [
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.oxygen_saturation.read",
    "https://www.googleapis.com/auth/fitness.body.read",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.blood_pressure.read",
    "https://www.googleapis.com/auth/fitness.body_temperature.read",
]

# Google Fit data source IDs
DATA_TYPES = {
    "heart_rate":   "com.google.heart_rate.bpm",
    "oxygen":       "com.google.oxygen_saturation",
    "steps":        "com.google.step_count.delta",
    "calories":     "com.google.calories.expended",
    "weight":       "com.google.weight",
    "bp":           "com.google.blood_pressure",
    "temperature":  "com.google.body.temperature",
}

def is_configured():
    return os.path.exists("google_credentials.json")

def is_authenticated():
    return os.path.exists(TOKEN_FILE)

def get_credentials():
    if not GOOGLE_LIBS_AVAILABLE:
        return None
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        except Exception:
            return None
    return creds if (creds and creds.valid) else None

def authenticate():
    """Run OAuth flow — opens browser for Google login."""
    if not GOOGLE_LIBS_AVAILABLE:
        return False, "Google libraries not installed. Run: pip install google-auth google-auth-oauthlib google-api-python-client"
    if not os.path.exists("google_credentials.json"):
        return False, "google_credentials.json not found. Download it from Google Cloud Console."
    try:
        flow = InstalledAppFlow.from_client_secrets_file("google_credentials.json", SCOPES)
        creds = flow.run_local_server(port=8080, open_browser=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        return True, "Connected to Google Fit!"
    except Exception as e:
        return False, str(e)

def disconnect():
    if os.path.exists(TOKEN_FILE):
        os.remove(TOKEN_FILE)

def _ns(dt):
    """datetime to nanoseconds"""
    return int(dt.timestamp() * 1e9)

def _fetch_dataset(service, data_type_name, start_dt, end_dt):
    """Fetch a dataset from Google Fit."""
    try:
        start_ns = _ns(start_dt)
        end_ns   = _ns(end_dt)
        dataset_id = f"{start_ns}-{end_ns}"
        result = service.users().dataSources().datasets().get(
            userId="me",
            dataSourceId=f"derived::{data_type_name}:com.google.android.gms:merge_{data_type_name.split('.')[-1]}",
            datasetId=dataset_id
        ).execute()
        return result.get("point", [])
    except Exception:
        return []

def _fetch_aggregated(service, data_type_name, start_dt, end_dt):
    """Fetch aggregated data using the aggregate endpoint."""
    try:
        body = {
            "aggregateBy": [{"dataTypeName": data_type_name}],
            "bucketByTime": {"durationMillis": int((end_dt - start_dt).total_seconds() * 1000)},
            "startTimeMillis": int(start_dt.timestamp() * 1000),
            "endTimeMillis":   int(end_dt.timestamp() * 1000),
        }
        resp = service.users().dataset().aggregate(userId="me", body=body).execute()
        points = []
        for bucket in resp.get("bucket", []):
            for ds in bucket.get("dataset", []):
                points.extend(ds.get("point", []))
        return points
    except Exception:
        return []

def get_latest_vitals():
    """Get the most recent vitals from Google Fit. Returns a dict."""
    creds = get_credentials()
    if not creds:
        return {"error": "Not authenticated with Google Fit"}

    try:
        service = build("fitness", "v1", credentials=creds, cache_discovery=False)
        now = datetime.datetime.utcnow()
        past_24h = now - datetime.timedelta(hours=24)

        result = {}

        # Heart Rate
        points = _fetch_aggregated(service, DATA_TYPES["heart_rate"], past_24h, now)
        if points:
            vals = [v["fpVal"] for p in points for v in p.get("value", []) if "fpVal" in v]
            if vals:
                result["heart_rate"] = round(vals[-1])
                result["heart_rate_avg"] = round(sum(vals) / len(vals))

        # Oxygen Saturation
        points = _fetch_aggregated(service, DATA_TYPES["oxygen"], past_24h, now)
        if points:
            vals = [v["fpVal"] for p in points for v in p.get("value", []) if "fpVal" in v]
            if vals:
                result["oxygen"] = round(vals[-1], 1)

        # Steps today
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        points = _fetch_aggregated(service, DATA_TYPES["steps"], today_start, now)
        if points:
            vals = [v["intVal"] for p in points for v in p.get("value", []) if "intVal" in v]
            result["steps"] = sum(vals)

        # Calories today
        points = _fetch_aggregated(service, DATA_TYPES["calories"], today_start, now)
        if points:
            vals = [v["fpVal"] for p in points for v in p.get("value", []) if "fpVal" in v]
            result["calories_burned"] = round(sum(vals))

        # Blood Pressure
        points = _fetch_aggregated(service, DATA_TYPES["bp"], past_24h, now)
        if points:
            for p in reversed(points):
                vals = p.get("value", [])
                if len(vals) >= 2:
                    result["bp_sys"] = round(vals[0].get("fpVal", 0))
                    result["bp_dia"] = round(vals[1].get("fpVal", 0))
                    break

        # Body Temperature
        points = _fetch_aggregated(service, DATA_TYPES["temperature"], past_24h, now)
        if points:
            vals = [v["fpVal"] for p in points for v in p.get("value", []) if "fpVal" in v]
            if vals:
                # Convert Celsius to Fahrenheit
                c = vals[-1]
                result["temperature"] = round(c * 9/5 + 32, 1)

        result["synced_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        result["source"] = "Google Fit"
        return result

    except Exception as e:
        return {"error": f"Google Fit error: {str(e)}"}

def get_vitals_history(hours=24):
    """Get vitals data points over the past N hours for charting."""
    creds = get_credentials()
    if not creds:
        return []

    try:
        service = build("fitness", "v1", credentials=creds, cache_discovery=False)
        now = datetime.datetime.utcnow()
        start = now - datetime.timedelta(hours=hours)

        # Bucket by 30 minutes
        bucket_ms = 30 * 60 * 1000
        body = {
            "aggregateBy": [
                {"dataTypeName": DATA_TYPES["heart_rate"]},
                {"dataTypeName": DATA_TYPES["oxygen"]},
            ],
            "bucketByTime": {"durationMillis": bucket_ms},
            "startTimeMillis": int(start.timestamp() * 1000),
            "endTimeMillis":   int(now.timestamp() * 1000),
        }
        resp = service.users().dataset().aggregate(userId="me", body=body).execute()

        history = []
        for bucket in resp.get("bucket", []):
            ts = int(bucket["startTimeMillis"]) / 1000
            dt = datetime.datetime.utcfromtimestamp(ts)
            entry = {"timestamp": dt.strftime("%H:%M"), "source": "Google Fit"}
            for ds in bucket.get("dataset", []):
                pts = ds.get("point", [])
                if not pts:
                    continue
                vals = pts[0].get("value", [])
                if not vals:
                    continue
                dtype = ds.get("dataSourceId", "")
                if "heart_rate" in dtype:
                    entry["heart_rate"] = round(vals[0].get("fpVal", 0))
                elif "oxygen" in dtype:
                    entry["oxygen"] = round(vals[0].get("fpVal", 0), 1)
            if "heart_rate" in entry or "oxygen" in entry:
                history.append(entry)

        return history

    except Exception:
        return []
