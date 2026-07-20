import os
import re
import shutil

FEATURE = re.compile(r"\s+(?:feat\.?|ft\.?|featuring|with)\s+.*$", re.IGNORECASE)
SEPARATORS = (",", "&", ";", " x ", " + ", " vs ", " vs. ")


def sanitize(text):
    for character in "/\\\n\r\t":
        text = text.replace(character, "-")

    return text.strip()


def format_time(seconds):
    seconds = int(seconds)

    return f"{seconds // 60}:{seconds % 60:02d}"


def parse_song(path):
    name = os.path.basename(path)[:-4]

    if " - " in name:
        artist, title = name.split(" - ", 1)
        return artist, title

    return "", name


def normalize(text):
    return "".join(character for character in text.lower() if character.isalnum())


def primary_artist(artist):
    artist = FEATURE.sub("", artist)
    lowered = artist.lower()
    cut = len(artist)

    for separator in SEPARATORS:
        index = lowered.find(separator)
        if index != -1:
            cut = min(cut, index)

    return artist[:cut].strip()


def clean_title(title):
    for opener, closer in (("(", ")"), ("[", "]")):
        while opener in title:
            start = title.index(opener)
            end = title.find(closer, start)
            if end == -1:
                title = title[:start]
                break
            title = title[:start] + title[end + 1:]

    if " - " in title:
        title = title.split(" - ")[0]

    return title.strip()


def cleanup_partials(*directories):
    for directory in directories:
        if not os.path.isdir(directory):
            continue

        for name in os.listdir(directory):
            if not (name.endswith(".part") or name.endswith(".tmp")):
                continue

            path = os.path.join(directory, name)

            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
