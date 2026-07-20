import os
import queue
import shutil
import time

from yt_dlp import YoutubeDL

from config import (VIDEO_DIRECTORY, MUSIC_DIRECTORY, BACKFILL_INTERVAL,
                    REQUEST_TIMEOUT, VIDEO_CANDIDATES, VIDEO_MAX_HEIGHT,
                    VIDEO_CLIP_SECONDS)
from util import sanitize, normalize, primary_artist, clean_title
from covers import is_online

video_queue = queue.Queue()
_requested = set()

REJECT_KEYWORDS = ("lyric", "audio", "visualizer", "visualiser",
                   "karaoke", "instrumental", "reaction", "cover by",
                   "sped up", "slowed", "nightcore")


def request_video(artist, title):
    key = (artist, title)

    if key in _requested:
        return

    _requested.add(key)
    video_queue.put(key)


def video_path(artist, title):
    return os.path.join(VIDEO_DIRECTORY, f"{sanitize(artist)} - {sanitize(title)}.mp4")


def none_marker(artist, title):
    return os.path.join(VIDEO_DIRECTORY, f".{sanitize(artist)} - {sanitize(title)}.none")


def is_music_video(entry, artist, title):
    entry_title = entry.get("title", "")
    channel = entry.get("channel") or entry.get("uploader") or ""

    if any(word in entry_title.lower() for word in REJECT_KEYWORDS):
        return False

    if "topic" in channel.lower():
        return False

    normalized_title = normalize(entry_title)

    if normalize(clean_title(title)) not in normalized_title:
        return False

    wanted = normalize(primary_artist(artist))

    return wanted in normalized_title or wanted in normalize(channel)


def find_video(artist, title):
    query = (f"ytsearch{VIDEO_CANDIDATES}:"
             f"{primary_artist(artist)} {title} official music video")

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "extract_flat": "in_playlist",
        "socket_timeout": REQUEST_TIMEOUT,
    }

    with YoutubeDL(options) as downloader:
        data = downloader.extract_info(query, download=False)

    for entry in data.get("entries", []):
        if entry.get("id") and is_music_video(entry, artist, title):
            return entry["id"]

    return None


def clip_range(info, downloader):
    duration = info.get("duration") or 0
    start = max(0, duration / 2 - VIDEO_CLIP_SECONDS / 2)

    return [{"start_time": start, "end_time": start + VIDEO_CLIP_SECONDS}]


def download_video(identifier, destination):
    working = destination + ".tmp"
    shutil.rmtree(working, ignore_errors=True)
    os.makedirs(working, exist_ok=True)

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": REQUEST_TIMEOUT,
        "format": f"best[ext=mp4][height<={VIDEO_MAX_HEIGHT}]/best[height<={VIDEO_MAX_HEIGHT}]",
        "outtmpl": os.path.join(working, "%(id)s.%(ext)s"),
        "download_ranges": clip_range,
        "force_keyframes_at_cuts": True,
    }

    try:
        with YoutubeDL(options) as downloader:
            downloader.download([f"https://www.youtube.com/watch?v={identifier}"])

        produced = os.listdir(working)
        if produced:
            os.replace(os.path.join(working, produced[0]), destination)
    finally:
        shutil.rmtree(working, ignore_errors=True)


def fetch_video(artist, title):
    if not artist or not title:
        return

    destination = video_path(artist, title)
    marker = none_marker(artist, title)

    if os.path.exists(destination) or os.path.exists(marker):
        return

    if not is_online():
        return

    try:
        identifier = find_video(artist, title)
    except Exception:
        return

    if not identifier:
        open(marker, "w").close()
        return

    try:
        download_video(identifier, destination)
    except Exception:
        pass


def backfill():
    if not is_online():
        return

    for filename in sorted(os.listdir(MUSIC_DIRECTORY)):
        if filename.endswith(".mp3") and " - " in filename:
            artist, title = filename[:-4].split(" - ", 1)
            fetch_video(artist, title)
            time.sleep(1.5)


def video_worker():
    while True:
        try:
            artist, title = video_queue.get(timeout=BACKFILL_INTERVAL)
            fetch_video(artist, title)
        except queue.Empty:
            try:
                backfill()
            except Exception:
                pass
        except Exception:
            pass
