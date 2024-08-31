from datetime import datetime, timezone, timedelta
from typing import Union

def parse_date(date_string: Union[str, datetime]) -> datetime:
    """Parse a date string to a timezone-aware datetime object."""
    if isinstance(date_string, datetime):
        return date_string.astimezone(timezone.utc)

    if isinstance(date_string, str):
        # Try parsing as ISO format first
        try:
            dt = datetime.fromisoformat(date_string.replace('Z', '+00:00'))
            return dt.astimezone(timezone.utc)
        except ValueError:
            pass

        # Try common formats
        for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
            try:
                dt = datetime.strptime(date_string, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        # Try parsing timestamp
        try:
            return datetime.fromtimestamp(float(date_string), tz=timezone.utc)
        except ValueError:
            pass

    raise ValueError(f"Unable to parse date string: {date_string}")

def format_date(date: Union[str, datetime]) -> str:
    """Format a datetime object to an ISO 8601 string."""
    if isinstance(date, str):
        date = parse_date(date)
    return date.astimezone(timezone.utc).isoformat()

def get_start_of_day(date: Union[str, datetime]) -> datetime:
    """Get the start of the day for a given date."""
    if isinstance(date, str):
        date = parse_date(date)
    return date.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

def get_end_of_day(date: Union[str, datetime]) -> datetime:
    """Get the end of the day for a given date."""
    if isinstance(date, str):
        date = parse_date(date)
    return date.astimezone(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=999999)

def date_range(start_date: Union[str, datetime], end_date: Union[str, datetime]) -> iter:
    """Generate a range of dates from start_date to end_date, inclusive."""
    start = get_start_of_day(parse_date(start_date))
    end = get_start_of_day(parse_date(end_date))
    while start <= end:
        yield start
        start += timedelta(days=1)

def days_ago(num_days: int) -> datetime:
    """Returns the datetime object for the date num_days ago from now."""
    return datetime.now(timezone.utc) - timedelta(days=num_days)
