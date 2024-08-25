from datetime import datetime, timezone, timedelta

def parse_date(date_string):
    """Parse a date string to a datetime object."""
    try:
        return datetime.fromisoformat(date_string.replace('Z', '+00:00'))
    except ValueError:
        # If not in ISO format, try common formats
        for fmt in ('%Y-%m-%d', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S'):
            try:
                return datetime.strptime(date_string, fmt)
            except ValueError:
                continue
    raise ValueError(f"Unable to parse date string: {date_string}")

def format_date(date):
    """Format a datetime object to an ISO 8601 string."""
    if isinstance(date, datetime):
        return date.astimezone(timezone.utc).isoformat()
    elif isinstance(date, str):
        return format_date(parse_date(date))
    else:
        raise ValueError(f"Unsupported date type: {type(date)}")

def get_start_of_day(date):
    """Get the start of the day for a given date."""
    return date.replace(hour=0, minute=0, second=0, microsecond=0)

def get_end_of_day(date):
    """Get the end of the day for a given date."""
    return date.replace(hour=23, minute=59, second=59, microsecond=999999)

def date_range(start_date, end_date):
    """Generate a range of dates from start_date to end_date, inclusive."""
    start = get_start_of_day(parse_date(start_date))
    end = get_start_of_day(parse_date(end_date))
    while start <= end:
        yield start
        start += timedelta(days=1)