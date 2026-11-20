import re
from datetime import datetime, timezone, timedelta
from typing import Tuple, Optional
import pytz

def calculate_offset_from_user_time(user_time_str: str) -> Optional[float]:
    """
    Parses string like '14:30', '14.30', '9' and compares to current UTC time
    to calculate UTC offset in hours.
    """
    cleaned = re.sub(r"[^\d:\.]", "", user_time_str.strip())
    parts = re.split(r"[:\.]", cleaned)
    
    try:
        hours = int(parts[0])
        minutes = int(parts[1]) if len(parts) > 1 else 0
        if not (0 <= hours < 24 and 0 <= minutes < 60):
            return None
    except (ValueError, IndexError):
        return None

    now_utc = datetime.now(timezone.utc)
    utc_total_minutes = now_utc.hour * 60 + now_utc.minute
    user_total_minutes = hours * 60 + minutes

    diff_minutes = user_total_minutes - utc_total_minutes
    # Handle midnight boundary
    if diff_minutes > 12 * 60:
        diff_minutes -= 24 * 60
    elif diff_minutes < -12 * 60:
        diff_minutes += 24 * 60

    # Round to nearest 0.5 hour
    offset_hours = round((diff_minutes / 60.0) * 2) / 2
    return offset_hours

def find_timezone_by_offset(offset_hours: float) -> str:
    """Finds standard tz name matching the offset (e.g. Europe/Moscow for +3)"""
    offset_td = timedelta(hours=offset_hours)
    now = datetime.now(timezone.utc)
    
    for tz_name in pytz.common_timezones:
        try:
            tz = pytz.timezone(tz_name)
            if tz.utcoffset(now) == offset_td:
                return tz_name
        except Exception:
            continue
    # Fallback to Etc/GMT format
    gmt_offset = -int(offset_hours)
    return f"Etc/GMT{'+' if gmt_offset >= 0 else ''}{gmt_offset}"

def get_user_now(tz_name: str) -> datetime:
    try:
        tz = pytz.timezone(tz_name)
        return datetime.now(tz)
    except Exception:
        return datetime.now(timezone.utc)
