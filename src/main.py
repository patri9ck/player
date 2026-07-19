#!/usr/bin/env python3
import threading

import dbus
import dbus.mainloop.glib
from gi.repository import GLib

from config import AGENT_PATH, REFRESH_MILLISECONDS
from agent import Agent
from covers import cover_worker
from videos import video_worker
from speaker import Speaker


def main():
    dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
    system_bus = dbus.SystemBus()

    agent = Agent(system_bus, AGENT_PATH)
    manager = dbus.Interface(
        system_bus.get_object("org.bluez", "/org/bluez"), "org.bluez.AgentManager1")
    manager.RegisterAgent(AGENT_PATH, "NoInputNoOutput")
    manager.RequestDefaultAgent(AGENT_PATH)

    speaker = Speaker(system_bus)
    speaker.configure_adapters()
    speaker.setup_buttons()
    speaker.start_local_engine()

    system_bus.add_signal_receiver(
        speaker.on_player_props, dbus_interface="org.freedesktop.DBus.Properties",
        signal_name="PropertiesChanged", arg0="org.bluez.MediaPlayer1")

    system_bus.add_signal_receiver(
        speaker.on_device_props, dbus_interface="org.freedesktop.DBus.Properties",
        signal_name="PropertiesChanged", arg0="org.bluez.Device1")

    system_bus.add_signal_receiver(
        speaker.on_added, dbus_interface="org.freedesktop.DBus.ObjectManager",
        signal_name="InterfacesAdded")

    system_bus.add_signal_receiver(
        speaker.on_removed, dbus_interface="org.freedesktop.DBus.ObjectManager",
        signal_name="InterfacesRemoved")

    address = speaker.connected_device()[1]
    if address:
        speaker.enter_bluetooth(address)
    else:
        speaker.enter_local(play=False)

    threading.Thread(target=cover_worker, daemon=True).start()
    threading.Thread(target=video_worker, daemon=True).start()
    GLib.timeout_add(REFRESH_MILLISECONDS, speaker.tick)

    GLib.MainLoop().run()


if __name__ == "__main__":
    main()
