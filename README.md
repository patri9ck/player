This is my hobby project to build my own music player using a Raspberry Pi Zero 2 WH, a Pirate Audio Headphone AMP and a PiSugar S.

Features:
- Connect via Bluetooth and control playback from your phone.
- Played songs are recorded to the local library. Covers are downloaded once an internet connection is available and shown on the display.
- Music videos are downloaded from YouTube via [yt-dlp](https://github.com/yt-dlp/yt-dlp) and play as an animated background instead of the cover. Only a short clip (10 seconds from the middle of the video) is downloaded and looped. Only genuine official music videos are used; lyric videos, audio-only uploads and similar are rejected. When no music video exists, this is remembered so the song is not searched again, and the cover is shown instead.
- Browse and play songs from the local library, even without a phone.
- Disconnect and temporarily block an unknown Bluetooth device from the player itself.
- Headless Wi-Fi setup via [Comitup](https://github.com/davesteele/comitup), toggled with a button.

It also includes a 3D-printable case I created with Claude Fable 5. It is a deeper version of [this case](https://www.thingiverse.com/thing:5245754) that also fits the PiSugar and closes unnecessary holes. The model can be found in [`model`](model).

## Button Layout
```
              ┌──────────────────────┐
  A  GPIO 5   │                      │   X  GPIO 16
              │      240 x 240       │
              │       display        │
  B  GPIO 6   │                      │   Y  GPIO 24
              └──────────────────────┘
```

If a phone is connected via Bluetooth:
- A - previous track
- B - next track
- X - play / pause
- Y - disconnect phone, block it for 60 s
- Y (hold) - toggle Comitup Wi-Fi setup

Offline mode if no phone is connected:
- A - previous track
- B - next track
- X - play / pause
- X (hold) - open song browser
- Y (hold) - toggle Comitup Wi-Fi setup

Song browser:
- A - move selection up
- B - move selection down
- X - play selected song, return to now playing
- Y - return to now playing without changing the song
- Y (hold) - toggle Comitup Wi-Fi setup

The display turns off after 30 s without a button press. Any button wakes it and returns to the now playing view; that waking press only wakes the display and does not trigger its action.

## Installation

Install packages:
```
sudo apt update
sudo apt upgrade
sudo apt install git bluez bluez-alsa-utils alsa-utils comitup ffmpeg mpg123 python3-dbus python3-gi python3-mutagen python3-pip python3-pil python3-numpy python3-spidev python3-gpiozero python3-lgpio fonts-dejavu-core
pip install st7789 yt-dlp --break-system-packages
```

Add your user to the required groups:
```
sudo usermod -aG bluetooth,audio,spi,gpio $USER
```

Clone the repository, then copy the scripts and the systemd unit into place. The music, covers and videos directories are created by the player itself on first start:
```
mkdir -p ~/.config/systemd/user

git clone https://github.com/patri9ck/player.git
cp player/src/* ~
cp player/systemd/player.service ~/.config/systemd/user/
```

Add these lines to `/boot/firmware/config.txt`:
```
dtparam=spi=on
dtparam=audio=off
dtoverlay=hifiberry-dac
gpio=25=op,dh
```

Create or edit `/etc/asound.conf`:
```
pcm.dmixer {
    type dmix
    ipc_key 1024
    ipc_perm 0666
    slave {
        pcm "hw:sndrpihifiberry"
        format S16_LE
        rate 44100
        channels 2
        period_size 1024
        buffer_size 8192
    }
}

pcm.!default {
    type plug
    slave.pcm "dmixer"
}

ctl.!default {
    type hw
    card sndrpihifiberry
}
```

Create or edit `/etc/bluetooth/main.conf`:
```
[General]
Name = Player
Class = 0x240414
DiscoverableTimeout = 0
PairableTimeout = 0
FastConnectable = true
JustWorksRepairing = always

[Policy]
AutoEnable = true
```

Bluetooth might be soft-blocked, so unblock it:
```
sudo rfkill unblock bluetooth
```

Restrict BlueALSA to the a2dp-sink profile. Create the drop-in directory and file `/etc/systemd/system/bluealsa.service.d/override.conf`:
```
sudo mkdir -p /etc/systemd/system/bluealsa.service.d
```
```
[Service]
ExecStart=
ExecStart=/usr/bin/bluealsa -p a2dp-sink
```

Disable `bluealsa-aplay`, since the player captures and routes the a2dp stream itself. Otherwise it would grab the stream and playback would fail with a busy capture device:
```
sudo systemctl disable --now bluealsa-aplay.service
```

Edit `/etc/comitup.conf`:
```
ap_name: Player
# ap_password: yourpassword
```

Comitup is started manually through a button press, so disable its auto-start:
```
sudo systemctl disable --now comitup
```

Allow your user to start and stop Comitup without a password. Run `sudo visudo -f /etc/sudoers.d/player` and add the following, replacing `player` with your user name:
```
player ALL=(root) NOPASSWD: /usr/bin/systemctl start comitup, /usr/bin/systemctl stop comitup
```

Start everything:
```
loginctl enable-linger $USER

systemctl --user daemon-reload
systemctl --user enable --now player.service

sudo systemctl daemon-reload
sudo systemctl restart bluetooth
sudo systemctl restart bluealsa

sudo reboot
```

## License
This project is licensed under MIT.
