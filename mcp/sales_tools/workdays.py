"""Working days per country: weekends and public holidays, for the send window, follow-up spacing and
meeting slots. One place, so every tool counts days the same way.

National holidays only (no regional ones, such as German states). Days most Nordic offices close
although they are not official holidays (Christmas Eve, New Year's Eve, Midsummer Eve) count as
holidays. A country not in the table only skips weekends.
"""
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

COUNTRY_TZ = {"SE": "Europe/Stockholm", "NO": "Europe/Oslo", "FI": "Europe/Helsinki", "DK": "Europe/Copenhagen",
              "DE": "Europe/Berlin", "NL": "Europe/Amsterdam", "GB": "Europe/London", "UK": "Europe/London",
              "IS": "Atlantic/Reykjavik", "EE": "Europe/Tallinn"}
TZ_COUNTRY = {"Europe/Stockholm": "SE", "Europe/Oslo": "NO", "Europe/Helsinki": "FI", "Europe/Copenhagen": "DK",
              "Europe/Berlin": "DE"}


def easter(year: int) -> date:
    """Easter Sunday (Gregorian, the anonymous algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _weekday_between(year: int, month: int, first: int, last: int, weekday: int) -> date:
    """The given weekday (0 = Monday) between two days of a month, e.g. Midsummer Eve: the Friday 19-25 June."""
    for d in range(first, last + 1):
        if date(year, month, d).weekday() == weekday:
            return date(year, month, d)
    raise ValueError("no such day")


@lru_cache(maxsize=None)
def holidays(country: str, year: int) -> dict[date, str]:
    e = easter(year)
    rel = lambda n: e + timedelta(days=n)  # noqa: E731
    common = {date(year, 1, 1): "New Year's Day", rel(-2): "Good Friday", rel(1): "Easter Monday",
              rel(39): "Ascension Day", date(year, 12, 25): "Christmas Day", date(year, 12, 26): "Boxing Day"}
    c = (country or "").upper()
    if c == "SE":
        midsummer_eve = _weekday_between(year, 6, 19, 25, 4)
        return {**common, date(year, 1, 6): "Epiphany", date(year, 5, 1): "May Day", date(year, 6, 6): "National Day",
                midsummer_eve: "Midsummer Eve", midsummer_eve + timedelta(days=1): "Midsummer Day",
                _weekday_between(year, 10, 31, 31, 5) if date(year, 10, 31).weekday() == 5
                else _weekday_between(year, 11, 1, 6, 5): "All Saints' Day",
                date(year, 12, 24): "Christmas Eve", date(year, 12, 31): "New Year's Eve"}
    if c == "NO":
        return {**common, rel(-3): "Maundy Thursday", date(year, 5, 1): "May Day", date(year, 5, 17): "Constitution Day",
                rel(50): "Whit Monday", date(year, 12, 24): "Christmas Eve", date(year, 12, 31): "New Year's Eve"}
    if c == "FI":
        midsummer_eve = _weekday_between(year, 6, 19, 25, 4)
        return {**common, date(year, 1, 6): "Epiphany", date(year, 5, 1): "May Day",
                midsummer_eve: "Midsummer Eve", midsummer_eve + timedelta(days=1): "Midsummer Day",
                _weekday_between(year, 10, 31, 31, 5) if date(year, 10, 31).weekday() == 5
                else _weekday_between(year, 11, 1, 6, 5): "All Saints' Day",
                date(year, 12, 6): "Independence Day", date(year, 12, 24): "Christmas Eve"}
    if c == "DK":
        return {**common, rel(-3): "Maundy Thursday", rel(50): "Whit Monday", date(year, 6, 5): "Constitution Day",
                date(year, 12, 24): "Christmas Eve", date(year, 12, 31): "New Year's Eve"}
    if c == "DE":
        return {**common, date(year, 5, 1): "Labour Day", rel(50): "Whit Monday", date(year, 10, 3): "German Unity Day"}
    return {}


def holiday(d: date, country: str | None) -> str | None:
    return holidays(country or "", d.year).get(d) if country else None


def is_working_day(d: date, *countries: str | None) -> bool:
    """A weekday that is not a public holiday in any of the countries."""
    return d.weekday() < 5 and not any(holiday(d, c) for c in countries)


def add_working_days(at: datetime, n: int, tz: ZoneInfo, *countries: str | None) -> datetime:
    """n working days later, same local time of day."""
    day = at.astimezone(tz)
    while n > 0:
        day = (day + timedelta(days=1)).replace(tzinfo=None)
        day = day.replace(tzinfo=tz)  # keep the wall-clock time across a daylight-saving change
        if is_working_day(day.date(), *countries):
            n -= 1
    return day


def next_working_day(d: date, *countries: str | None) -> date:
    d += timedelta(days=1)
    while not is_working_day(d, *countries):
        d += timedelta(days=1)
    return d


def at_local(d: date, hour: int, tz: ZoneInfo, minute: int = 0) -> datetime:
    return datetime.combine(d, time(hour, minute), tz)


def country_of_tz(tz_name: str | None) -> str | None:
    return TZ_COUNTRY.get(tz_name or "")
