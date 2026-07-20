import os
import time
import random
import threading
import subprocess

import dbus
from gi.repository import GLib
from mutagen.mp3 import MP3
from PIL import Image, ImageDraw, ImageFont, ImageEnhance
import ST7789
from gpiozero import Button

from config import *
from util import sanitize, format_time, parse_song, cleanup_partials
from covers import request_cover, cover_path
from videos import request_video, video_path
from video import VideoClip


class Speaker:

    def __init__(self, bus):
        self.bus = bus

        self.mode = "local"

        self.recorder = None
        self.playback = None
        self.job = None
        self.running = False

        self.player = None

        self.artist = ""
        self.title = ""
        self.duration = 0
        self.position = 0
        self.playing = False
        self.synced_at = time.monotonic()

        self.cover = None
        self.cover_checked = 0
        self.video = None
        self.video_checked = 0
        self.message = None
        self.message_until = 0

        self.x_held = False
        self.y_held = False
        self.buttons = []

        self.view = "playing"
        self.browser_index = 0
        self.thumb_cache = {}
        self.placeholder = Image.new("RGB", (THUMB_SIZE, THUMB_SIZE), (50, 50, 60))

        self.last_activity = time.monotonic()
        self.display_on = True
        self.last_signature = None

        self.mpg = None
        self.playlist = []
        self.local_index = 0
        self.local_intent = False
        self.loading = False

        os.makedirs(MUSIC_DIRECTORY, exist_ok=True)
        os.makedirs(COVER_DIRECTORY, exist_ok=True)
        os.makedirs(VIDEO_DIRECTORY, exist_ok=True)

        cleanup_partials(MUSIC_DIRECTORY, COVER_DIRECTORY, VIDEO_DIRECTORY)

        self.screen = ST7789.ST7789(
            port=0, cs=ST7789.BG_SPI_CS_FRONT, dc=9, backlight=13,
            rotation=90, spi_speed_hz=80 * 1000 * 1000,
        )
        self.screen.set_backlight(1)

        self.title_font = ImageFont.truetype(FONT_BOLD, 22)
        self.small_font = ImageFont.truetype(FONT_REGULAR, 16)

    def managed_objects(self):
        manager = dbus.Interface(
            self.bus.get_object("org.bluez", "/"),
            "org.freedesktop.DBus.ObjectManager")

        return manager.GetManagedObjects()

    def properties(self, path):
        return dbus.Interface(
            self.bus.get_object("org.bluez", path),
            "org.freedesktop.DBus.Properties")

    def connected_device(self):
        for path, interfaces in self.managed_objects().items():
            device = interfaces.get("org.bluez.Device1")
            if device and device.get("Connected"):
                return path, device.get("Address")

        return None, None

    def configure_adapters(self):
        for path, interfaces in self.managed_objects().items():
            if "org.bluez.Adapter1" not in interfaces:
                continue

            adapter = self.properties(path)
            adapter.Set("org.bluez.Adapter1", "Alias", DEVICE_NAME)
            adapter.Set("org.bluez.Adapter1", "Powered", True)
            adapter.Set("org.bluez.Adapter1", "Pairable", True)
            adapter.Set("org.bluez.Adapter1", "Discoverable", True)

    def find_player(self):
        for path, interfaces in self.managed_objects().items():
            if "org.bluez.MediaPlayer1" in interfaces:
                self.player = path
                self.load_from_player()
                return

    def player_call(self, method):
        if not self.player:
            return

        interface = dbus.Interface(
            self.bus.get_object("org.bluez", self.player),
            "org.bluez.MediaPlayer1")

        try:
            getattr(interface, method)()
        except dbus.exceptions.DBusException:
            pass

    def load_from_player(self):
        try:
            values = self.properties(self.player).GetAll("org.bluez.MediaPlayer1")
        except dbus.exceptions.DBusException:
            return

        self.apply_track(values.get("Track", {}))
        self.playing = str(values.get("Status", "")) == "playing"
        self.apply_position(int(values.get("Position", 0)))

    def apply_track(self, track):
        self.artist = sanitize(str(track.get("Artist", "")))
        self.title = sanitize(str(track.get("Title", "")))
        self.duration = int(track.get("Duration", 0)) / 1000
        self.cover = None
        self.cover_checked = 0
        self.load_video()

        request_cover(self.artist, self.title)
        request_video(self.artist, self.title)

        self.rotate()

    def apply_position(self, milliseconds):
        self.position = milliseconds / 1000
        self.synced_at = time.monotonic()

    def current_position(self):
        if self.mode == "bluetooth" and self.playing:
            value = self.position + (time.monotonic() - self.synced_at)
        else:
            value = self.position

        if self.duration > 0:
            return min(value, self.duration)

        return value

    def enter_bluetooth(self, address):
        self.mode = "bluetooth"
        self.view = "playing"
        self.local_intent = False

        self.mpg_send("STOP")
        self.find_player()

        GLib.timeout_add(AUDIO_START_DELAY, self.start_audio, address)

        return False

    def enter_local(self, play=True):
        self.mode = "local"
        self.player = None

        self.playlist = [
            os.path.join(MUSIC_DIRECTORY, name)
            for name in os.listdir(MUSIC_DIRECTORY) if name.endswith(".mp3")
        ]
        random.shuffle(self.playlist)

        if self.playlist:
            self.local_load(self.local_index % len(self.playlist), play)
        else:
            self.artist = self.title = ""
            self.duration = self.position = 0
            self.playing = False
            self.cover = None
            self.load_video()

        return False

    def start_local_engine(self):
        self.mpg = subprocess.Popen(
            ["mpg123", "-a", PLAYBACK_DEVICE, "-R"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)

        threading.Thread(target=self.local_reader, daemon=True).start()

    def mpg_send(self, command):
        if not self.mpg:
            return

        try:
            self.mpg.stdin.write(command + "\n")
            self.mpg.stdin.flush()
        except (BrokenPipeError, ValueError):
            pass

    def local_load(self, index, play=True):
        self.local_index = index % len(self.playlist)
        path = self.playlist[self.local_index]

        self.artist, self.title = parse_song(path)

        try:
            self.duration = MP3(path).info.length
        except Exception:
            self.duration = 0

        self.position = 0
        self.cover = None
        self.cover_checked = 0
        self.load_video()
        self.playing = play
        self.local_intent = play
        self.loading = True

        request_cover(self.artist, self.title)
        request_video(self.artist, self.title)
        self.mpg_send(f"{'LOAD' if play else 'LOADPAUSED'} {path}")

        return False

    def local_next(self):
        if self.playlist:
            self.local_load(self.local_index + 1)

        return False

    def local_previous(self):
        if self.playlist:
            self.local_load(self.local_index - 1)

        return False

    def local_reader(self):
        for line in self.mpg.stdout:
            line = line.strip()

            if line.startswith("@F "):
                parts = line.split()
                try:
                    elapsed, remaining = float(parts[3]), float(parts[4])
                except (IndexError, ValueError):
                    continue

                self.loading = False

                if self.mode == "local":
                    self.position = elapsed
                    self.duration = elapsed + remaining

            elif line.startswith("@P "):
                state = line.split()[1] if len(line.split()) > 1 else ""

                if state == "0":
                    if self.mode == "local" and self.local_intent and not self.loading:
                        GLib.idle_add(self.local_next)
                elif state == "1":
                    self.playing = False
                elif state == "2":
                    self.playing = True
                    self.local_intent = True

    def spawn_encoder(self, path):
        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS), "-i", "pipe:0",
            "-codec:a", "libmp3lame", "-q:a", "2", "-f", "mp3", path,
        ]

        return subprocess.Popen(command, stdin=subprocess.PIPE)

    def start_audio(self, address, attempt=1):
        if self.mode != "bluetooth" or self.running or self.recorder:
            return False

        device = f"bluealsa:DEV={address},PROFILE=a2dp"

        self.recorder = subprocess.Popen(
            ["arecord", "-q", "-D", device, "-f", "S16_LE",
             "-r", str(SAMPLE_RATE), "-c", str(CHANNELS), "-t", "raw"],
            stdout=subprocess.PIPE)

        GLib.timeout_add(AUDIO_CONFIRM_DELAY, self.confirm_audio, address, attempt)

        return False

    def confirm_audio(self, address, attempt):
        if self.mode != "bluetooth":
            self.terminate_audio()
            return False

        if self.recorder and self.recorder.poll() is None:
            self.playback = subprocess.Popen(
                ["aplay", "-q", "-D", PLAYBACK_DEVICE, "-f", "S16_LE",
                 "-r", str(SAMPLE_RATE), "-c", str(CHANNELS), "-t", "raw"],
                stdin=subprocess.PIPE)

            self.running = True
            threading.Thread(target=self.pump, daemon=True).start()

            return False

        self.terminate_audio()

        if attempt < AUDIO_ATTEMPTS:
            GLib.timeout_add(AUDIO_RETRY_DELAY, self.start_audio, address, attempt + 1)

        return False

    def stop_audio(self):
        if not self.running and not self.recorder and not self.playback:
            return

        self.running = False

        job, self.job = self.job, None
        self.finalize(job)

        self.terminate_audio()

    def terminate_audio(self):
        for process in (self.recorder, self.playback):
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()

        self.recorder = self.playback = None

    def pump(self):
        stream = self.recorder.stdout

        while self.running:
            data = stream.read(CHUNK_SIZE)
            if not data:
                break

            playback = self.playback
            if playback:
                try:
                    playback.stdin.write(data)
                except (BrokenPipeError, ValueError):
                    pass

            job = self.job
            if job:
                try:
                    job["encoder"].stdin.write(data)
                except (BrokenPipeError, ValueError):
                    pass

        if self.running:
            GLib.idle_add(self.stop_audio)

    def rotate(self):
        if not self.running:
            return

        if self.job and self.job["artist"] == self.artist and self.job["title"] == self.title:
            return

        new_job = None
        target = os.path.join(MUSIC_DIRECTORY, f"{self.artist} - {self.title}.mp3")

        if (self.artist and self.title
                and self.duration > 0 and not os.path.exists(target)):
            new_job = {
                "encoder": self.spawn_encoder(target + ".part"),
                "target": target,
                "expected": self.duration,
                "artist": self.artist,
                "title": self.title,
            }

        previous, self.job = self.job, new_job
        self.finalize(previous)

    def finalize(self, job):
        if job is None:
            return

        try:
            job["encoder"].stdin.close()
        except (BrokenPipeError, ValueError):
            pass

        job["encoder"].wait()

        target = job["target"]
        temporary = target + ".part"

        if not os.path.exists(temporary):
            return

        try:
            recorded = MP3(temporary).info.length
        except Exception:
            os.remove(temporary)
            return

        if recorded < job["expected"] - TOLERANCE_SECONDS:
            os.remove(temporary)
            return

        os.replace(temporary, target)

    def notify(self, text):
        self.message = text
        self.message_until = time.monotonic() + 2

    def load_cover(self):
        if self.cover is not None or not self.title:
            return

        now = time.monotonic()
        if now - self.cover_checked < COVER_RETRY_SECONDS:
            return

        self.cover_checked = now
        path = cover_path(self.artist, self.title)

        if not os.path.exists(path):
            return

        try:
            image = Image.open(path).convert("RGB").resize((WIDTH, HEIGHT))
            self.cover = ImageEnhance.Brightness(image).enhance(COVER_BRIGHTNESS)
        except Exception:
            self.cover = None

    def load_video(self):
        if self.video:
            self.video.stop()
            self.video = None

        self.video_checked = 0
        self.start_video()

    def start_video(self):
        if self.video or not self.display_on or not self.title:
            return

        now = time.monotonic()
        if now - self.video_checked < VIDEO_RETRY_SECONDS:
            return

        self.video_checked = now
        path = video_path(self.artist, self.title)

        if os.path.exists(path):
            self.video = VideoClip(path)

    def wake(self):
        self.last_activity = time.monotonic()

        if not self.display_on:
            self.display_on = True
            self.screen.set_backlight(1)
            self.load_video()
            self.render()

    def sleep(self):
        if self.display_on:
            self.display_on = False
            self.view = "playing"
            self.screen.set_backlight(0)

            if self.video:
                self.video.stop()
                self.video = None

    def render(self):
        if self.mode == "local" and self.view == "browser":
            self.render_browser()
            return

        self.start_video()

        frame = self.video.frame() if self.video else None

        if frame is None:
            self.load_cover()

            frame = self.cover.copy() if self.cover else Image.new("RGB", (WIDTH, HEIGHT), (18, 18, 22))

        draw = ImageDraw.Draw(frame)

        if self.message and time.monotonic() < self.message_until:
            self.draw_centered(draw, self.message, self.title_font, HEIGHT // 2 - 12)
            self.screen.display(frame)
            return

        label = " - ".join(part for part in (self.artist, self.title) if part)
        if not label:
            label = "No Music" if self.mode == "local" else "Waiting for Phone"

        self.draw_wrapped(draw, label, self.title_font, 20, 60)

        if self.duration > 0:
            self.draw_progress(draw)

        self.screen.display(frame)

    def draw_centered(self, draw, text, font, y):
        width = draw.textlength(text, font=font)
        draw.text(((WIDTH - width) / 2, y), text, font=font, fill=(255, 255, 255))

    def draw_wrapped(self, draw, text, font, x, y):
        lines, current = [], ""

        for word in text.split(" "):
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= WIDTH - 2 * x:
                current = candidate
            else:
                lines.append(current)
                current = word

        lines.append(current)

        for index, line in enumerate(lines[:3]):
            draw.text((x, y + index * 28), line, font=font, fill=(255, 255, 255))

    def draw_progress(self, draw):
        position = self.current_position()
        ratio = max(0.0, min(1.0, position / self.duration))

        left, right, y = 20, WIDTH - 20, HEIGHT - 42

        draw.rounded_rectangle([left, y, right, y + 6], radius=3, fill=(80, 80, 90))
        draw.rounded_rectangle([left, y, left + (right - left) * ratio, y + 6],
                               radius=3, fill=(0, 200, 120))

        draw.text((left, y + 12), format_time(position),
                  font=self.small_font, fill=(220, 220, 220))

        total = format_time(self.duration)
        draw.text((right - draw.textlength(total, font=self.small_font), y + 12),
                  total, font=self.small_font, fill=(220, 220, 220))

    def thumbnail(self, artist, title):
        path = cover_path(artist, title)

        if path in self.thumb_cache:
            return self.thumb_cache[path]

        if not os.path.exists(path):
            return self.placeholder

        try:
            image = Image.open(path).convert("RGB").resize((THUMB_SIZE, THUMB_SIZE))
        except OSError:
            return self.placeholder

        self.thumb_cache[path] = image
        return image

    def truncate(self, draw, text, font, max_width):
        if draw.textlength(text, font=font) <= max_width:
            return text

        while text and draw.textlength(text + "…", font=font) > max_width:
            text = text[:-1]

        return text + "…"

    def render_browser(self):
        frame = Image.new("RGB", (WIDTH, HEIGHT), (18, 18, 22))
        draw = ImageDraw.Draw(frame)

        if not self.playlist:
            self.draw_centered(draw, "No Music", self.title_font, HEIGHT // 2 - 12)
            self.screen.display(frame)
            return

        visible = 5
        row_height = 46
        total = len(self.playlist)
        start = max(0, min(self.browser_index - visible // 2, max(0, total - visible)))

        for row in range(visible):
            index = start + row
            if index >= total:
                break

            y = row * row_height + 3
            artist, title = parse_song(self.playlist[index])

            if index == self.browser_index:
                draw.rounded_rectangle([2, y, WIDTH - 2, y + row_height - 6],
                                       radius=6, fill=(40, 40, 55))

            frame.paste(self.thumbnail(artist, title), (8, y + 1))

            label = f"{artist} - {title}" if artist else title
            label = self.truncate(draw, label, self.small_font, WIDTH - 62)
            draw.text((54, y + 12), label, font=self.small_font, fill=(255, 255, 255))

        self.screen.display(frame)

    def render_signature(self):
        message = self.message if time.monotonic() < self.message_until else None

        return (self.view, self.mode, self.artist, self.title, self.playing,
                int(self.current_position()), message, self.browser_index)

    def tick(self):
        interval = IDLE_REFRESH_MILLISECONDS

        if self.display_on:
            if time.monotonic() - self.last_activity > SLEEP_SECONDS:
                self.sleep()
            elif self.video is not None and self.video.latest is not None:
                interval = VIDEO_REFRESH_MILLISECONDS
                self.render()
            else:
                signature = self.render_signature()
                if signature != self.last_signature:
                    self.last_signature = signature
                    self.render()

        GLib.timeout_add(interval, self.tick)

        return False

    def dispatch(self, action):
        if self.mode == "bluetooth":
            {"play": lambda: self.player_call("Pause" if self.playing else "Play"),
             "next": lambda: self.player_call("Next"),
             "previous": lambda: self.player_call("Previous")}[action]()
        else:
            {"play": lambda: self.mpg_send("PAUSE"),
             "next": self.local_next,
             "previous": self.local_previous}[action]()

    def bluetooth_short(self):
        path, address = self.connected_device()
        if not path:
            return

        device = dbus.Interface(self.bus.get_object("org.bluez", path), "org.bluez.Device1")
        device.Disconnect()

        self.properties(path).Set("org.bluez.Device1", "Blocked", True)
        GLib.timeout_add_seconds(BLOCK_SECONDS, self.unblock, path)

        self.notify("Bluetooth Reset")

    def unblock(self, path):
        try:
            self.properties(path).Set("org.bluez.Device1", "Blocked", False)
        except dbus.exceptions.DBusException:
            pass

        return False

    def bluetooth_hold(self):
        active = subprocess.run(["systemctl", "is-active", "--quiet", "comitup"]).returncode == 0

        if active:
            subprocess.run(["sudo", "systemctl", "stop", "comitup"])
            self.notify("Wi-Fi Setup Off")
        else:
            subprocess.run(["sudo", "systemctl", "start", "comitup"])
            self.notify("Wi-Fi Setup On")

    def on_button(self, action):
        was_asleep = not self.display_on

        self.wake()

        if not was_asleep:
            action()

        return False

    def open_browser(self):
        if self.mode == "local" and self.playlist:
            self.browser_index = self.local_index
            self.view = "browser"

    def close_browser(self):
        self.view = "playing"

    def browser_up(self):
        if self.playlist:
            self.browser_index = (self.browser_index - 1) % len(self.playlist)

    def browser_down(self):
        if self.playlist:
            self.browser_index = (self.browser_index + 1) % len(self.playlist)

    def browser_select(self):
        if self.playlist:
            self.local_load(self.browser_index)
            self.view = "playing"

    def browsing(self):
        return self.mode == "local" and self.view == "browser"

    def button_a(self):
        self.browser_up() if self.browsing() else self.dispatch("previous")

    def button_b(self):
        self.browser_down() if self.browsing() else self.dispatch("next")

    def button_x(self):
        self.browser_select() if self.browsing() else self.dispatch("play")

    def button_y(self):
        self.close_browser() if self.browsing() else self.bluetooth_short()

    def setup_buttons(self):
        a = Button(BUTTON_PREVIOUS)
        b = Button(BUTTON_NEXT)
        x = Button(BUTTON_PLAY_PAUSE, hold_time=HOLD_SECONDS)
        y = Button(BUTTON_BLUETOOTH, hold_time=HOLD_SECONDS)

        a.when_pressed = lambda: GLib.idle_add(self.on_button, self.button_a)
        b.when_pressed = lambda: GLib.idle_add(self.on_button, self.button_b)

        def x_held():
            self.x_held = True
            GLib.idle_add(self.on_button, self.open_browser)

        def x_released():
            if self.x_held:
                self.x_held = False
            else:
                GLib.idle_add(self.on_button, self.button_x)

        x.when_held = x_held
        x.when_released = x_released

        def y_held():
            self.y_held = True
            GLib.idle_add(self.on_button, self.bluetooth_hold)

        def y_released():
            if self.y_held:
                self.y_held = False
            else:
                GLib.idle_add(self.on_button, self.button_y)

        y.when_held = y_held
        y.when_released = y_released

        self.buttons = [a, b, x, y]

    def on_player_props(self, interface, changed, invalidated):
        if self.mode != "bluetooth":
            return

        if "Track" in changed:
            self.apply_track(changed["Track"])

        if "Status" in changed:
            self.playing = str(changed["Status"]) == "playing"

        if "Position" in changed:
            self.apply_position(int(changed["Position"]))

    def on_device_props(self, interface, changed, invalidated):
        if "Connected" not in changed:
            return

        if changed["Connected"]:
            _, address = self.connected_device()
            if address:
                self.enter_bluetooth(address)
        else:
            self.stop_audio()
            self.enter_local(play=False)

    def on_added(self, path, interfaces):
        if "org.bluez.Device1" in interfaces:
            GLib.timeout_add(1000, self.set_trusted, path)

        if "org.bluez.MediaPlayer1" in interfaces and self.mode == "bluetooth":
            self.player = path
            self.load_from_player()

    def on_removed(self, path, interfaces):
        if path == self.player:
            self.player = None

    def set_trusted(self, path):
        try:
            self.properties(path).Set("org.bluez.Device1", "Trusted", True)
        except dbus.exceptions.DBusException:
            pass

        return False
