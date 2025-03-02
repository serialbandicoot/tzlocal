import os
import re
import sys
import warnings
from datetime import timezone

from tzlocal import utils

if sys.version_info >= (3, 9):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # pragma: no cover
else:
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # pragma: no cover

_cache_tz = None


def _tz_from_env(tzenv):
    if tzenv[0] == ":":
        tzenv = tzenv[1:]

    # TZ specifies a file
    if os.path.isabs(tzenv) and os.path.exists(tzenv):
        with open(tzenv, "rb") as tzfile:
            return ZoneInfo.from_file(tzfile, key="local")

    # TZ specifies a zoneinfo zone.
    try:
        tz = ZoneInfo(tzenv)
        # That worked, so we return this:
        return tz
    except ZoneInfoNotFoundError:
        raise ZoneInfoNotFoundError(
            "tzlocal() does not support non-zoneinfo timezones like %s. \n"
            "Please use a timezone in the form of Continent/City"
        ) from None


def _try_tz_from_env():
    tzenv = os.environ.get("TZ")
    if tzenv:
        try:
            return _tz_from_env(tzenv)
        except ZoneInfoNotFoundError:
            pass


def _get_localzone(_root="/"):
    """Tries to find the local timezone configuration.

    This method prefers finding the timezone name and passing that to pytz,
    over passing in the localtime file, as in the later case the zoneinfo
    name is unknown.

    The parameter _root makes the function look for files like /etc/localtime
    beneath the _root directory. This is primarily used by the tests.
    In normal usage you call the function without parameters."""

    tzenv = _try_tz_from_env()
    if tzenv:
        return tzenv

    # Are we under Termux on Android?
    if os.path.exists(os.path.join(_root, "system/bin/getprop")):
        import subprocess

        androidtz = (
            subprocess.check_output(["getprop", "persist.sys.timezone"])
            .strip()
            .decode()
        )
        return ZoneInfo(androidtz)

    # Now look for distribution specific configuration files
    # that contain the timezone name.

    # Stick all of them in a dict, to compare later.
    found_configs = {}

    for configfile in ("etc/timezone", "var/db/zoneinfo"):
        tzpath = os.path.join(_root, configfile)
        try:
            with open(tzpath, "rt") as tzfile:
                data = tzfile.read()

                etctz = data.strip()
                if not etctz:
                    # Empty file, skip
                    continue
                for etctz in data.splitlines():
                    # Get rid of host definitions and comments:
                    if " " in etctz:
                        etctz, dummy = etctz.split(" ", 1)
                    if "#" in etctz:
                        etctz, dummy = etctz.split("#", 1)
                    if not etctz:
                        continue

                    found_configs[tzpath] = etctz.replace(" ", "_")

        except (IOError, UnicodeDecodeError):
            # File doesn't exist or is a directory, or it's a binary file.
            continue

    # CentOS has a ZONE setting in /etc/sysconfig/clock,
    # OpenSUSE has a TIMEZONE setting in /etc/sysconfig/clock and
    # Gentoo has a TIMEZONE setting in /etc/conf.d/clock
    # We look through these files for a timezone:

    zone_re = re.compile(r"\s*ZONE\s*=\s*\"")
    timezone_re = re.compile(r"\s*TIMEZONE\s*=\s*\"")
    end_re = re.compile('"')

    for filename in ("etc/sysconfig/clock", "etc/conf.d/clock"):
        tzpath = os.path.join(_root, filename)
        try:
            with open(tzpath, "rt") as tzfile:
                data = tzfile.readlines()

            for line in data:
                # Look for the ZONE= setting.
                match = zone_re.match(line)
                if match is None:
                    # No ZONE= setting. Look for the TIMEZONE= setting.
                    match = timezone_re.match(line)
                if match is not None:
                    # Some setting existed
                    line = line[match.end() :]
                    etctz = line[: end_re.search(line).start()]

                    # We found a timezone
                    found_configs[tzpath] = etctz.replace(" ", "_")

        except (IOError, UnicodeDecodeError):
            # UnicodeDecode handles when clock is symlink to /etc/localtime
            continue

    # systemd distributions use symlinks that include the zone name,
    # see manpage of localtime(5) and timedatectl(1)
    tzpath = os.path.join(_root, "etc/localtime")
    if os.path.exists(tzpath) and os.path.islink(tzpath):
        etctz = tzpath = os.path.realpath(tzpath)
        start = etctz.find("/") + 1
        while start != 0:
            etctz = etctz[start:]
            try:
                ZoneInfo(etctz)
                found_configs[tzpath] = etctz.replace(" ", "_")
            except ZoneInfoNotFoundError:
                pass
            start = etctz.find("/") + 1

    if len(found_configs) > 0:
        # We found some explicit config of some sort!
        if len(found_configs) > 1:
            # Uh-oh, multiple configs. See if they match:
            unique_tzs = set()
            for tzname in found_configs.values():
                # Get rid of any Etc's
                tzname = tzname.replace('Etc/', '')
                # In practice these are the same:
                tzname = tzname.replace('UTC', 'GMT')
                # Let's handle these synonyms as well. Many systems have tons
                # of synonyms, including country names and "Zulu" and other
                # nonsense. Those will be seen as different ones. Let's stick
                # to the official zoneinfo Continent/City names.
                if tzname in ['GMT0', 'GMT+0', 'GMT-0']:
                    tzname = 'GMT'
                unique_tzs.add(tzname)

            if len(unique_tzs) != 1:
                message = "Multiple conflicting time zone configurations found:\n"
                for key, value in found_configs.items():
                    message += f"{key}: {value}\n"
                message += "Fix the configuration, or set the time zone in a TZ environment variable.\n"
                raise ZoneInfoNotFoundError(message)

        # We found exactly one config! Use it.
        tz = ZoneInfo(list(found_configs.values())[0])
        if _root == "/":
            # We are using a file in etc to name the timezone.
            # Verify that the timezone specified there is actually used:
            utils.assert_tz_offset(tz)
        return tz

    # No explicit setting existed. Use localtime
    for filename in ("etc/localtime", "usr/local/etc/localtime"):
        tzpath = os.path.join(_root, filename)

        if not os.path.exists(tzpath):
            continue
        with open(tzpath, "rb") as tzfile:
            return ZoneInfo.from_file(tzfile, key="local")

    warnings.warn("Can not find any timezone configuration, defaulting to UTC.")
    return timezone.utc


def get_localzone():
    """Get the computers configured local timezone, if any."""
    global _cache_tz
    if _cache_tz is None:
        _cache_tz = _get_localzone()

    return _cache_tz


def reload_localzone():
    """Reload the cached localzone. You need to call this if the timezone has changed."""
    global _cache_tz
    _cache_tz = _get_localzone()
    return _cache_tz
