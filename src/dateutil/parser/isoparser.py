"""
This module offers a parser for ISO-8601 strings

It is intended to support all valid date, time and datetime formats per the
ISO-8601 specification.

..versionadded:: 2.7.0
"""
from datetime import datetime, timedelta, time, date
import calendar
from dateutil import tz

from functools import wraps

import re

__all__ = ["isoparse", "isoparser"]


class _Cursor:
    """A position in an ISO-8601 byte string.

    ``pos`` is the offset of the next unread byte, ``current`` is the byte at
    that offset (empty at the end) and ``rest`` is everything not yet read.
    The reading helpers advance ``pos`` as they consume input, so parsers can
    leave the cursor at the boundary where the next portion starts.
    """

    __slots__ = ("data", "pos")

    def __init__(self, data):
        self.data = data
        self.pos = 0

    @property
    def at_end(self):
        return self.pos >= len(self.data)

    @property
    def current(self):
        return self.data[self.pos : self.pos + 1]

    @property
    def rest(self):
        return self.data[self.pos :]

    def advance(self, n=1):
        self.pos += n

    def read_digits(self, n):
        """Read exactly ``n`` ASCII digits and advance past them."""
        digits = self.data[self.pos : self.pos + n]
        if len(digits) != n or not digits.isdigit():
            raise ValueError(
                "Expected {} digits, got {!r}".format(n, digits.decode("ascii"))
            )
        self.pos += n
        return int(digits)

    def skip(self, sep):
        """Consume ``sep`` if it is current, returning whether it was."""
        if self.current == sep:
            self.advance()
            return True
        return False

    def expect(self, sep, message):
        """Consume the required separator ``sep`` or raise ``ValueError``."""
        if self.current != sep:
            raise ValueError(message)
        self.advance()


def _takes_ascii(f):
    @wraps(f)
    def func(self, str_in, *args, **kwargs):
        # If it's a stream, read the whole thing
        str_in = getattr(str_in, 'read', lambda: str_in)()

        # If it's unicode, turn it into bytes, since ISO-8601 only covers ASCII
        if isinstance(str_in, str):
            # ASCII is the same in UTF-8
            try:
                str_in = str_in.encode('ascii')
            except UnicodeEncodeError as e:
                msg = 'ISO-8601 strings should contain only ASCII characters'
                raise ValueError(msg) from e

        return f(self, str_in, *args, **kwargs)

    return func


class isoparser:
    def __init__(self, sep=None):
        """
        :param sep:
            A single character that separates date and time portions. If
            ``None``, the parser will accept any single character.
            For strict ISO-8601 adherence, pass ``'T'``.
        """
        if sep is not None:
            if (len(sep) != 1 or ord(sep) >= 128 or sep in '0123456789'):
                raise ValueError('Separator must be a single, non-numeric ' +
                                 'ASCII character')

            sep = sep.encode('ascii')

        self._sep = sep

    @_takes_ascii
    def isoparse(self, dt_str):
        """
        Parse an ISO-8601 datetime string into a :class:`datetime.datetime`.

        An ISO-8601 datetime string consists of a date portion, followed
        optionally by a time portion - the date and time portions are separated
        by a single character separator, which is ``T`` in the official
        standard. Incomplete date formats (such as ``YYYY-MM``) may *not* be
        combined with a time portion.

        Supported date formats are:

        Common:

        - ``YYYY``
        - ``YYYY-MM``
        - ``YYYY-MM-DD`` or ``YYYYMMDD``

        Uncommon:

        - ``YYYY-Www`` or ``YYYYWww`` - ISO week (day defaults to 0)
        - ``YYYY-Www-D`` or ``YYYYWwwD`` - ISO week and day

        The ISO week and day numbering follows the same logic as
        :func:`datetime.date.isocalendar`.

        Supported time formats are:

        - ``hh``
        - ``hh:mm`` or ``hhmm``
        - ``hh:mm:ss`` or ``hhmmss``
        - ``hh:mm:ss.ssssss`` (Up to 6 sub-second digits)

        Midnight is a special case for `hh`, as the standard supports both
        00:00 and 24:00 as a representation. The decimal separator can be
        either a dot or a comma.


        .. caution::

            Support for fractional components other than seconds is part of the
            ISO-8601 standard, but is not currently implemented in this parser.

        Supported time zone offset formats are:

        - `Z` (UTC)
        - `±HH:MM`
        - `±HHMM`
        - `±HH`

        Offsets will be represented as :class:`dateutil.tz.tzoffset` objects,
        with the exception of UTC, which will be represented as
        :class:`dateutil.tz.tzutc`. Time zone offsets equivalent to UTC (such
        as `+00:00`) will also be represented as :class:`dateutil.tz.tzutc`.

        :param dt_str:
            A string or stream containing only an ISO-8601 datetime string

        :return:
            Returns a :class:`datetime.datetime` representing the string.
            Unspecified components default to their lowest value.

        .. warning::

            As of version 2.7.0, the strictness of the parser should not be
            considered a stable part of the contract. Any valid ISO-8601 string
            that parses correctly with the default settings will continue to
            parse correctly in future versions, but invalid strings that
            currently fail (e.g. ``2017-01-01T00:00+00:00:00``) are not
            guaranteed to continue failing in future versions if they encode
            a valid date.

        .. versionadded:: 2.7.0
        """
        components, cursor = self._parse_isodate(dt_str)

        if not cursor.at_end:
            if self._sep is None or cursor.current == self._sep:
                cursor.advance()
                components += self._parse_isotime(cursor.rest)
            else:
                raise ValueError('String contains unknown ISO components')

        if len(components) > 3 and components[3] == 24:
            components[3] = 0
            return datetime(*components) + timedelta(days=1)

        return datetime(*components)

    @_takes_ascii
    def parse_isodate(self, datestr):
        """
        Parse the date portion of an ISO string.

        :param datestr:
            The string portion of an ISO string, without a separator

        :return:
            Returns a :class:`datetime.date` object
        """
        components, cursor = self._parse_isodate(datestr)
        if not cursor.at_end:
            raise ValueError('String contains unknown ISO ' +
                             'components: {!r}'.format(datestr.decode('ascii')))
        return date(*components)

    @_takes_ascii
    def parse_isotime(self, timestr):
        """
        Parse the time portion of an ISO string.

        :param timestr:
            The time portion of an ISO string, without a separator

        :return:
            Returns a :class:`datetime.time` object
        """
        components = self._parse_isotime(timestr)
        if components[0] == 24:
            components[0] = 0
        return time(*components)

    @_takes_ascii
    def parse_tzstr(self, tzstr, zero_as_utc=True):
        """
        Parse a valid ISO time zone string.

        See :func:`isoparser.isoparse` for details on supported formats.

        :param tzstr:
            A string representing an ISO time zone offset

        :param zero_as_utc:
            Whether to return :class:`dateutil.tz.tzutc` for zero-offset zones

        :return:
            Returns :class:`dateutil.tz.tzoffset` for offsets and
            :class:`dateutil.tz.tzutc` for ``Z`` and (if ``zero_as_utc`` is
            specified) offsets equivalent to UTC.
        """
        return self._parse_tzstr(tzstr, zero_as_utc=zero_as_utc)

    # Constants
    _DATE_SEP = b'-'
    _TIME_SEP = b':'
    _FRACTION_REGEX = re.compile(b'[\\.,]([0-9]+)')

    def _parse_isodate(self, dt_str):
        """Parse the date at the start of ``dt_str``.

        Returns the date components and a cursor positioned at the first byte
        that is not part of the date, which is where the date/time separator
        (if there is one) starts.
        """
        cursor = _Cursor(dt_str)
        if len(dt_str) < 4:
            raise ValueError('ISO string too short')

        # All ISO date formats start with the year
        year = cursor.read_digits(4)
        if cursor.at_end:
            return [year, 1, 1], cursor

        has_sep = cursor.skip(self._DATE_SEP)
        if cursor.current == b'W':
            components = self._parse_week_date(cursor, year, has_sep)
        elif self._starts_calendar_date(cursor, has_sep):
            components = self._parse_calendar_date(cursor, year, has_sep)
        else:
            components = self._parse_ordinal_date(cursor, year)

        return components, cursor

    def _starts_calendar_date(self, cursor, has_sep):
        """Whether the bytes after the year start ``MM[-DD]`` rather than DDD.

        Basic-format dates have no separators, so four remaining digits are a
        month and day and three are an ordinal day. With separators, ``MM``
        followed by ``-`` continues to a day and ``MM`` alone is a
        month-only date; any other shape is an ordinal day.
        """
        rest = cursor.rest
        if has_sep:
            return len(rest) == 2 or rest[2:3] == self._DATE_SEP
        return len(rest) >= 4 and rest[:4].isdigit()

    def _parse_calendar_date(self, cursor, year, has_sep):
        """Parse ``MMDD`` or ``MM-DD`` after the year."""
        components = [year, 1, 1]

        if len(cursor.rest) < 2:
            raise ValueError('Invalid common month')
        components[1] = cursor.read_digits(2)

        if cursor.at_end:
            if has_sep:
                return components
            raise ValueError('Invalid ISO format')

        if has_sep:
            cursor.expect(self._DATE_SEP, 'Invalid separator in ISO string')

        if len(cursor.rest) < 2:
            raise ValueError('Invalid common day')
        components[2] = cursor.read_digits(2)
        return components

    def _parse_week_date(self, cursor, year, has_sep):
        """Parse ``Www[-D]`` or ``WwwD`` after the year."""
        cursor.advance()  # 'W'
        weekno = cursor.read_digits(2)

        dayno = 1
        if not cursor.at_end:
            if cursor.skip(self._DATE_SEP) != has_sep:
                raise ValueError('Inconsistent use of dash separator')
            dayno = cursor.read_digits(1)

        base_date = self._calculate_weekdate(year, weekno, dayno)
        return [base_date.year, base_date.month, base_date.day]

    def _parse_ordinal_date(self, cursor, year):
        """Parse ``DDD`` after the year."""
        if len(cursor.rest) < 3:
            raise ValueError('Invalid ordinal day')

        ordinal_day = cursor.read_digits(3)
        if ordinal_day < 1 or ordinal_day > (365 + calendar.isleap(year)):
            raise ValueError('Invalid ordinal day' +
                             ' {} for year {}'.format(ordinal_day, year))

        base_date = date(year, 1, 1) + timedelta(days=ordinal_day - 1)
        return [base_date.year, base_date.month, base_date.day]

    def _calculate_weekdate(self, year, week, day):
        """
        Calculate the day of corresponding to the ISO year-week-day calendar.

        This function is effectively the inverse of
        :func:`datetime.date.isocalendar`.

        :param year:
            The year in the ISO calendar

        :param week:
            The week in the ISO calendar - range is [1, 53]

        :param day:
            The day in the ISO calendar - range is [1 (MON), 7 (SUN)]

        :return:
            Returns a :class:`datetime.date`
        """
        if not 0 < week < 54:
            raise ValueError('Invalid week: {}'.format(week))

        if not 0 < day < 8:     # Range is 1-7
            raise ValueError('Invalid weekday: {}'.format(day))

        # Get week 1 for the specific year:
        jan_4 = date(year, 1, 4)   # Week 1 always has January 4th in it
        week_1 = jan_4 - timedelta(days=jan_4.isocalendar()[2] - 1)

        # Now add the specific number of weeks and days to get what we want
        week_offset = (week - 1) * 7 + (day - 1)
        return week_1 + timedelta(days=week_offset)

    def _parse_isotime(self, timestr):
        """Parse an ISO time, including a trailing time zone offset."""
        if len(timestr) < 2:
            raise ValueError('ISO time too short')

        cursor = _Cursor(timestr)
        components = [0, 0, 0, 0, None]

        if cursor.current in (b'-', b'+', b'Z', b'z'):
            # A time may consist of an offset alone
            components[-1] = self._parse_tzstr(cursor.rest)
        else:
            self._parse_time_fields(cursor, components)
            if cursor.current in (b'-', b'+', b'Z', b'z'):
                components[-1] = self._parse_tzstr(cursor.rest)
            elif not cursor.at_end:
                raise ValueError('Unused components in ISO string')

        if components[0] == 24:
            # Standard supports 00:00 and 24:00 as representations of midnight
            if any(component != 0 for component in components[1:4]):
                raise ValueError('Hour may only be 24 at 24:00:00.000')

        return components

    def _parse_time_fields(self, cursor, components):
        """Read the hour, minute, second and fraction from ``cursor``.

        Reads at most three colon-separated fields: a field is only read if
        enough input remains, and every field after the first must use the
        same separator as the first. Stops at the first byte that is not part
        of the time, leaving the cursor there.
        """
        has_sep = False
        for comp in range(3):
            if comp > 0:
                # An offset or the end of the input terminates the time
                if cursor.at_end or cursor.current in (b'-', b'+', b'Z', b'z'):
                    return

                if comp == 1:
                    has_sep = cursor.skip(self._TIME_SEP)
                elif has_sep:
                    cursor.expect(self._TIME_SEP,
                                  'Inconsistent use of colon separator')

            components[comp] = cursor.read_digits(2)

        if cursor.current in (b'.', b','):
            components[3] = self._parse_second_fraction(cursor)

    def _parse_second_fraction(self, cursor):
        """Read a decimal separator and fractional second digits."""
        frac = self._FRACTION_REGEX.match(cursor.rest)
        if not frac:
            return 0

        us_str = frac.group(1)[:6]  # Truncate to microseconds
        cursor.advance(len(frac.group()))
        return int(us_str) * 10**(6 - len(us_str))

    def _parse_tzstr(self, tzstr, zero_as_utc=True):
        """Parse ``Z`` or a ``±HH``, ``±HH:MM`` or ``±HHMM`` offset."""
        if tzstr == b'Z' or tzstr == b'z':
            return tz.UTC

        if len(tzstr) not in {3, 5, 6}:
            raise ValueError('Time zone offset must be 1, 3, 5 or 6 characters')

        cursor = _Cursor(tzstr)

        if cursor.current == b'-':
            mult = -1
        elif cursor.current == b'+':
            mult = 1
        else:
            raise ValueError('Time zone offset requires sign')
        cursor.advance()

        hours = cursor.read_digits(2)
        if cursor.at_end:
            minutes = 0
        else:
            has_sep = cursor.skip(self._TIME_SEP)
            expected_len = 6 if has_sep else 5
            if len(tzstr) != expected_len:
                raise ValueError("Invalid minutes in time zone offset")
            minutes = cursor.read_digits(2)

        if zero_as_utc and hours == 0 and minutes == 0:
            return tz.UTC
        else:
            if minutes > 59:
                raise ValueError('Invalid minutes in time zone offset')

            if hours > 23:
                raise ValueError('Invalid hours in time zone offset')

            return tz.tzoffset(None, mult * (hours * 60 + minutes) * 60)


DEFAULT_ISOPARSER = isoparser()
isoparse = DEFAULT_ISOPARSER.isoparse
