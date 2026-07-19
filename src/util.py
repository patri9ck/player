import os


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
