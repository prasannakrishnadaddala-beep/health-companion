"""
Google Fit - Production Web OAuth Flow
Each user connects their own Google account.
Tokens stored in DB per user. No local files needed.
"""
import os, json, datetime

GOOGLE_LIBS_AVAILABLE = False
try:
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request as GRequest
    from google_auth_oauthlib.flow import Flow
    from googleapiclient.discovery import build
    GOOGLE_LIBS_AVAILABLE = True
except ImportError:
    pass

SCOPES = [
    "https://www.googleapis.com/auth/fitness.heart_rate.read",
    "https://www.googleapis.com/auth/fitness.oxygen_saturation.read",
    "https://www.googleapis.com/auth/fitness.body.read",
    "https://www.googleapis.com/auth/fitness.activity.read",
    "https://www.googleapis.com/auth/fitness.blood_pressure.read",
    "https://www.googleapis.com/auth/fitness.body_temperature.read",
]

def get_client_config():
    """Load Google OAuth config from env variable (no files needed)."""
    raw = os.environ.get("GOOGLE_CLIENT_ID","")
    secret = os.environ.get("GOOGLE_CLIENT_SECRET","")
    if not raw or not secret:
        return None
    return {
        "web": {
            "client_id": raw,
            "client_secret": secret,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [os.environ.get("GOOGLE_REDIRECT_URI","")],
        }
    }

def is_configured():
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))

def get_auth_url(redirect_uri, state=None):
    """Generate Google OAuth URL for a user to click."""
    if not GOOGLE_LIBS_AVAILABLE or not is_configured():
        return None
    config = get_client_config()
    config["web"]["redirect_uris"] = [redirect_uri]
    flow = Flow.from_client_config(config, scopes=SCOPES, redirect_uri=redirect_uri)
    kwargs = {"access_type": "offline", "prompt": "consent"}
    if state:
        kwargs["state"] = state
    auth_url, _ = flow.authorization_url(**kwargs)
    return auth_url

def exchange_code(code, redirect_uri):
    """Exchange auth code for tokens. Stateless — no flow object needed."""
    if not GOOGLE_LIBS_AVAILABLE or not is_configured():
        return None
    config = get_client_config()
    if not config:
        return None
    import urllib.request, urllib.parse, urllib.error

    # Strip any trailing slash to ensure exact match
    redirect_uri = redirect_uri.rstrip("/")

    data = urllib.parse.urlencode({
        "code": code,
        "client_id": config["web"]["client_id"],
        "client_secret": config["web"]["client_secret"],
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"}
    )
    try:
        with urllib.request.urlopen(req) as resp:
            token_data = json.loads(resp.read().decode())
        return {
            "token": token_data.get("access_token"),
            "refresh_token": token_data.get("refresh_token"),
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": config["web"]["client_id"],
            "client_secret": config["web"]["client_secret"],
            "scopes": SCOPES,
            "expiry": (
                datetime.datetime.utcnow() +
                datetime.timedelta(seconds=token_data.get("expires_in", 3600))
            ).isoformat(),
        }
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise Exception(f"Google token error: {body}")
    except Exception as e:
        raise Exception(f"Token exchange failed: {str(e)}")

def get_credentials_from_token(token_dict):
    """Rebuild Credentials object from stored token dict."""
    if not GOOGLE_LIBS_AVAILABLE or not token_dict:
        return None
    expiry = None
    if token_dict.get("expiry"):
        try:
            expiry = datetime.datetime.fromisoformat(token_dict["expiry"])
        except Exception:
            pass
    creds = Credentials(
        token=token_dict.get("token"),
        refresh_token=token_dict.get("refresh_token"),
        token_uri=token_dict.get("token_uri","https://oauth2.googleapis.com/token"),
        client_id=token_dict.get("client_id"),
        client_secret=token_dict.get("client_secret"),
        scopes=token_dict.get("scopes", SCOPES),
        expiry=expiry,
    )
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(GRequest())
            # Return updated token dict
            token_dict["token"] = creds.token
            if creds.expiry:
                token_dict["expiry"] = creds.expiry.isoformat()
            return creds, token_dict
        except Exception:
            return None, token_dict
    return creds, token_dict

def fetch_vitals(token_dict):
    """Fetch latest vitals from Google Fit using stored token."""
    creds, updated_token = get_credentials_from_token(token_dict)
    if not creds:
        return {"error": "Invalid or expired Google credentials. Please reconnect."}, token_dict

    try:
        service = build("fitness", "v1", credentials=creds, cache_discovery=False)
        now = datetime.datetime.utcnow()
        past_24h = now - datetime.timedelta(hours=24)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

        def aggregate(data_type, start, end, bucket_ms=None):
            if bucket_ms is None:
                bucket_ms = int((end - start).total_seconds() * 1000)
            body = {
                "aggregateBy": [{"dataTypeName": data_type}],
                "bucketByTime": {"durationMillis": bucket_ms},
                "startTimeMillis": int(start.timestamp() * 1000),
                "endTimeMillis":   int(end.timestamp() * 1000),
            }
            try:
                resp = service.users().dataset().aggregate(userId="me", body=body).execute()
                pts = []
                for bucket in resp.get("bucket", []):
                    for ds in bucket.get("dataset", []):
                        pts.extend(ds.get("point", []))
                return pts
            except Exception:
                return []

        result = {}

        # Heart Rate
        pts = aggregate("com.google.heart_rate.bpm", past_24h, now, 30*60*1000)
        vals = [v["fpVal"] for p in pts for v in p.get("value",[]) if "fpVal" in v]
        if vals:
            result["heart_rate"] = round(vals[-1])
            result["heart_rate_avg"] = round(sum(vals)/len(vals))

        # O2
        pts = aggregate("com.google.oxygen_saturation", past_24h, now)
        vals = [v["fpVal"] for p in pts for v in p.get("value",[]) if "fpVal" in v]
        if vals:
            result["oxygen"] = round(vals[-1], 1)

        # Steps today
        pts = aggregate("com.google.step_count.delta", today_start, now)
        vals = [v["intVal"] for p in pts for v in p.get("value",[]) if "intVal" in v]
        if vals:
            result["steps"] = sum(vals)

        # Calories today
        pts = aggregate("com.google.calories.expended", today_start, now)
        vals = [v["fpVal"] for p in pts for v in p.get("value",[]) if "fpVal" in v]
        if vals:
            result["calories_burned"] = round(sum(vals))

        # Blood Pressure
        pts = aggregate("com.google.blood_pressure", past_24h, now)
        for p in reversed(pts):
            vs = p.get("value",[])
            if len(vs) >= 2:
                result["bp_sys"] = round(vs[0].get("fpVal",0))
                result["bp_dia"] = round(vs[1].get("fpVal",0))
                break

        # Temperature (C → F)
        pts = aggregate("com.google.body.temperature", past_24h, now)
        vals = [v["fpVal"] for p in pts for v in p.get("value",[]) if "fpVal" in v]
        if vals:
            result["temperature"] = round(vals[-1] * 9/5 + 32, 1)

        result["synced_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
        result["source"] = "Google Fit"
        return result, updated_token

    except Exception as e:
        return {"error": f"Google Fit error: {str(e)}"}, updated_token
