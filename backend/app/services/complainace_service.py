from datetime import datetime, time
import pytz
from app.config import settings

class ComplianceService:

    def is_dnc(self, lead) -> bool:
        return lead.is_dnc

    def is_within_call_hours(self) -> bool:
        """Check if current time is within allowed call window (IST)."""
        tz = pytz.timezone(settings.TIMEZONE)
        now = datetime.now(tz).time()
        window_start = time(settings.CALL_WINDOW_START_HOUR, 0)
        window_end = time(settings.CALL_WINDOW_END_HOUR, 0)
        return window_start <= now <= window_end

    def next_call_window_start(self) -> datetime:
        """Returns the next valid call window start as UTC datetime."""
        tz = pytz.timezone(settings.TIMEZONE)
        now = datetime.now(tz)
        today_start = now.replace(
            hour=settings.CALL_WINDOW_START_HOUR,
            minute=0, second=0, microsecond=0
        )
        if now.time() < time(settings.CALL_WINDOW_START_HOUR, 0):
            # Today's window hasn't started yet
            return today_start.astimezone(pytz.utc).replace(tzinfo=None)
        else:
            # Already past today's window, schedule for tomorrow
            from datetime import timedelta
            tomorrow_start = today_start + timedelta(days=1)
            return tomorrow_start.astimezone(pytz.utc).replace(tzinfo=None)