import os
import json
import time
import queue
import socket
import urllib.parse
import urllib.request

from config import (COVER_DIRECTORY, MUSIC_DIRECTORY, USER_AGENT,
                    BACKFILL_INTERVAL, REQUEST_TIMEOUT, COVER_CANDIDATES)
from util import sanitize, normalize, primary_artist, clean_title

cover_queue = queue.Queue()
_requested = set()


def request_cover(artist, title):
    key = (artist, title)

    if key in _requested:
        return

    _requested.add(key)
    cover_queue.put(key)


def is_online():
    try:
        socket.create_connection(("musicbrainz.org", 443), timeout=5).close()
        return True
    except OSError:
        return False


def cover_path(artist, title):
    return os.path.join(COVER_DIRECTORY, f"{sanitize(artist)} - {sanitize(title)}.jpg")


def none_marker(artist, title):
    return os.path.join(COVER_DIRECTORY, f".{sanitize(artist)} - {sanitize(title)}.none")


def http_get(url):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

    return urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT).read()


def credited_artists(recording):
    names = []

    for credit in recording.get("artist-credit", []):
        artist = credit.get("artist") or {}
        if artist.get("name"):
            names.append(normalize(artist["name"]))

    return names


REJECTED_SECONDARY_TYPES = ("compilation", "dj-mix", "mixtape/street")


def rank_release(release):
    if release.get("status") in ("Bootleg", "Pseudo-Release"):
        return None

    group = release.get("release-group") or {}
    secondary = [item.lower() for item in group.get("secondary-types", [])]

    if any(bad in secondary for bad in REJECTED_SECONDARY_TYPES):
        return None

    score = 0

    if release.get("status") == "Official":
        score += 4

    if (group.get("primary-type") or "").lower() in ("single", "album", "ep"):
        score += 2

    return score


def find_releases(artist, title):
    artist = primary_artist(artist)
    title = clean_title(title)

    wanted_artist = normalize(artist)
    wanted_title = normalize(title)

    query = f'artist:"{artist}" AND recording:"{title}"'
    url = ("https://musicbrainz.org/ws/2/recording"
           f"?query={urllib.parse.quote(query)}&fmt=json&limit=10")

    data = json.loads(http_get(url).decode("utf-8"))
    scored = {}

    for recording in data.get("recordings", []):
        recording_title = normalize(clean_title(recording.get("title", "")))

        if wanted_title not in recording_title and recording_title not in wanted_title:
            continue

        if not any(wanted_artist in name for name in credited_artists(recording)):
            continue

        for release in recording.get("releases", []):
            identifier = release.get("id")
            if not identifier:
                continue

            score = rank_release(release)
            if score is None:
                continue

            if identifier not in scored or score > scored[identifier]:
                scored[identifier] = score

    return sorted(scored, key=scored.get, reverse=True)


def fetch_cover(artist, title):
    if not artist or not title:
        return

    destination = cover_path(artist, title)
    marker = none_marker(artist, title)

    if os.path.exists(destination) or os.path.exists(marker):
        return

    if not is_online():
        return

    try:
        releases = find_releases(artist, title)
    except Exception:
        return

    if not releases:
        open(marker, "w").close()
        return

    content = None

    for release in releases[:COVER_CANDIDATES]:
        try:
            content = http_get(f"https://coverartarchive.org/release/{release}/front-500")
            break
        except Exception:
            time.sleep(0.5)

    if content is None:
        return

    temporary = destination + ".part"

    with open(temporary, "wb") as handle:
        handle.write(content)

    os.replace(temporary, destination)


def backfill():
    if not is_online():
        return

    for filename in sorted(os.listdir(MUSIC_DIRECTORY)):
        if filename.endswith(".mp3") and " - " in filename:
            artist, title = filename[:-4].split(" - ", 1)
            fetch_cover(artist, title)
            time.sleep(1.5)


def cover_worker():
    while True:
        try:
            artist, title = cover_queue.get(timeout=BACKFILL_INTERVAL)
            fetch_cover(artist, title)
        except queue.Empty:
            try:
                backfill()
            except Exception:
                pass
        except Exception:
            pass
