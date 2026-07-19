import threading
import subprocess

from PIL import Image

from config import WIDTH, HEIGHT, VIDEO_FPS


class VideoClip:

    def __init__(self, path):
        self.process = subprocess.Popen(
            ["ffmpeg", "-hide_banner", "-loglevel", "error",
             "-stream_loop", "-1", "-re", "-i", path,
             "-vf", f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={WIDTH}:{HEIGHT},fps={VIDEO_FPS}",
             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            stdout=subprocess.PIPE)

        self.frame_bytes = WIDTH * HEIGHT * 3
        self.latest = None
        self.running = True

        threading.Thread(target=self.reader, daemon=True).start()

    def reader(self):
        while self.running:
            data = self.process.stdout.read(self.frame_bytes)
            if len(data) < self.frame_bytes:
                break
            self.latest = data

    def frame(self):
        if self.latest is None:
            return None

        return Image.frombytes("RGB", (WIDTH, HEIGHT), self.latest)

    def stop(self):
        self.running = False
        self.process.terminate()
