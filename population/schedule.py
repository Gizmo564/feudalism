"""When the next tick is due. No Discord code here."""
from datetime import datetime
from zoneinfo import ZoneInfo


def is_due(now, last, hours, tick_time, tz, test_minutes=""):
    """now and last are unix times. hours is the tick_hours rule: 24 means once a day at tick_time (in tz);
    any other number means that many hours after the last tick. test_minutes (the TICK_MINUTES setting, for
    playtests) wins only while hours is still 24."""
    if hours != 24:
        return now - last >= hours * 3600
    if test_minutes:
        return now - last >= float(test_minutes) * 60
    hour, minute = (int(x) for x in tick_time.split(":"))
    today = datetime.fromtimestamp(now, ZoneInfo(tz)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now >= today.timestamp() > last
