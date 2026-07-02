"""
laptop_controller.py
---------------------
Runs on the laptop. Captures live WASD keypresses (no need to press Enter)
and streams them to the Pi over the radio link as control packets, at a
fixed rate, so the Pi's failsafe watchdog always sees recent traffic.

Controls:
  W / A / S / D  - drive (differential/tank drive, combos like W+A work)
  SPACE          - emergency stop
  R              - re-arm (use after a STOP if you want to resume)
  ESC            - quit

Requires: pyserial, pynput
  pip install pyserial pynput
"""

import serial
import threading
import time
from pynput import keyboard

PORT = "COM4"
BAUD = 57600
SEND_INTERVAL = 0.15  # seconds between control packets (also acts as heartbeat)

radio = serial.Serial(PORT, BAUD, timeout=2)
print("Laptop radio link open on", PORT)

pressed_keys = set()
lock = threading.Lock()
running = True


def on_press(key):
    global running
    try:
        k = key.char.lower()
    except AttributeError:
        k = None

    if k in ('w', 'a', 's', 'd'):
        with lock:
            pressed_keys.add(k)
    elif k == 'r':
        radio.write(b"ARM\n")
        print("ARM sent")
    elif key == keyboard.Key.space:
        with lock:
            pressed_keys.clear()
        radio.write(b"STOP\n")
        print("STOP sent")
    elif key == keyboard.Key.esc:
        running = False
        return False  # stop the listener


def on_release(key):
    try:
        k = key.char.lower()
    except AttributeError:
        k = None
    if k in ('w', 'a', 's', 'd'):
        with lock:
            pressed_keys.discard(k)


def listen_for_replies():
    while running:
        response = radio.readline().decode("utf-8", errors="ignore").strip()
        if response:
            print("Pi said:", response)


def main():
    listener = keyboard.Listener(on_press=on_press, on_release=on_release)
    listener.start()

    threading.Thread(target=listen_for_replies, daemon=True).start()

    print("Controls: W/A/S/D drive, SPACE stop, R re-arm, ESC quit")
    radio.write(b"ARM\n")

    try:
        while running:
            with lock:
                keys_snapshot = "".join(sorted(pressed_keys))
            # Always send, even when empty, so the packet doubles as a heartbeat
            radio.write(f"CTRL:{keys_snapshot}\n".encode("utf-8"))
            time.sleep(SEND_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        radio.write(b"STOP\n")
        listener.stop()
        print("Stopped and disconnected.")


if __name__ == "__main__":
    main()