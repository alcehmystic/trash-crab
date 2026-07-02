"""
pi_bridge.py
------------
Runs on the Raspberry Pi. Bridges the long-range radio link (to the laptop)
and the USB serial link (to the Arduino Nano motor controller).

Responsibilities:
  - Receive WASD control packets from the laptop over radio
  - Translate them into left/right differential-drive target speeds
  - Forward target speeds to the Arduino as "T:<left>,<right>"
  - Enforce the global speed cap here too (defense in depth)
  - Run a failsafe watchdog: if no control packet arrives in time,
    force the Arduino to stop
  - Keep sending telemetry back to the laptop (unchanged from the
    original test harness)

DEBUG MODE:
  If the Arduino isn't connected (or ARDUINO_PORT can't be opened), this
  script does NOT crash. It drops into debug mode: every control packet
  is decoded and printed in full (keys held, target percentages, the
  equivalent PWM pulse widths, and the exact command string that would
  be sent) so you can proof the control logic before the Arduino side
  is wired up. Set FORCE_DEBUG_MODE = True to force this even if the
  Arduino is connected.

  Debug mode also runs a software mirror of the Arduino's ramp loop
  (same math, same ~1.5s full-swing timing as applyRamp() in
  motor_controller.ino), printing the current value as it converges
  toward each new target. This is ONLY a preview - the real ramping
  always happens on the Arduino once it's connected, this just lets you
  confirm the gradual-speed-change behavior before that hardware exists.

Radio protocol expected from laptop (newline terminated):
  "ARM"                 -> arm the ESCs
  "STOP"                -> emergency stop
  "CTRL:<keys>"         -> currently held keys, e.g. "CTRL:wa" for
                           forward+left, "CTRL:" (empty) when nothing is
                           held. Uses the full MAX_SPEED_PCT cap.
  "CTRL:<keys>:<scale>" -> same as above, but the resulting left/right
                           target is additionally scaled by scale% (0-100)
                           before being clamped and sent to the Arduino.
                           Used for things like an autonomous search spin
                           that wants less than full speed, e.g.
                           "CTRL:d:50" turns right at half of the normal
                           full-deflection target.
"""

import serial
import threading
import time
import json

# Marks the moment the script itself started running, before any of the
# (potentially slow/blocking) hardware connection attempts below. Elapsed
# time reported in telemetry is measured from here, not from ARM/first
# control packet/etc.
SCRIPT_START_TIME = time.time()

# ---- Configuration ----
RADIO_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"     # Long-range radio link to the laptop
RADIO_BAUD = 57600

ARDUINO_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"   # USB link to the Arduino Nano motor controller
ARDUINO_BAUD = 57600

MAX_SPEED_PCT = 85               # global speed cap, mirrors the Arduino-side cap
FAILSAFE_TIMEOUT = 1.0           # seconds without a control packet before auto-stop

FORCE_DEBUG_MODE = False         # set True to always print instead of writing to Arduino

# PWM constants, mirrored from motor_controller.ino, used ONLY to preview
# what pulse width the Arduino would generate for a given target percent.
PULSE_NEUTRAL = 1500
PULSE_FORWARD_MAX = 2000
PULSE_REVERSE_MAX = 1000

# Ramp timing, mirrored from RAMP_MS in motor_controller.ino. Only used to
# drive the software ramp preview in debug mode below.
RAMP_MS = 1500

# NOTE: with both the radio adapter and the Arduino on USB, device names
# like /dev/ttyUSB0 / /dev/ttyACM0 can swap on reboot depending on
# enumeration order. If you see the wrong device responding, check
# `ls -l /dev/serial/by-id/` for stable names and use those instead.

def open_radio():
    """Open the radio serial port, retrying indefinitely until it succeeds.
    Used both for the initial connection at startup (so a systemd-managed
    boot doesn't crash if the radio dongle hasn't finished enumerating yet)
    and for runtime reconnects after a dropped connection."""
    while True:
        try:
            ser = serial.Serial(RADIO_PORT, RADIO_BAUD, timeout=1)
            print(f"Radio connected on {RADIO_PORT}")
            return ser
        except (serial.SerialException, FileNotFoundError) as e:
            print(f"[RADIO] Could not open {RADIO_PORT} ({e}). Retrying in 2s...")
            time.sleep(2)


radio = open_radio()

# Guards every radio.write()/radio.readline() call and the `radio` variable
# itself. Without this, telemetry_loop()'s once-a-second write and main()'s
# CTRL-ack write (both running on separate threads) could collide on this
# half-duplex link with no ordering at all - whichever one lost the race
# would sit queued behind the other, adding the kind of inconsistent
# delay (fast most of the time, occasionally much slower) that was showing
# up as sluggish "Command Received" updates on the dashboard. Mirrors the
# radio_lock telecom-server.py already uses on the laptop side for the
# identical reason.
radio_lock = threading.Lock()

ARDUINO_CONNECT_RETRIES = 5      # bounded retries at startup, then fall back to debug mode
ARDUINO_CONNECT_RETRY_DELAY = 2  # seconds between attempts

arduino = None
ARDUINO_CONNECTED = False
if not FORCE_DEBUG_MODE:
    for attempt in range(1, ARDUINO_CONNECT_RETRIES + 1):
        try:
            arduino = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
            time.sleep(2)  # allow the Arduino to reset after the serial port opens
            ARDUINO_CONNECTED = True
            print(f"Arduino connected on {ARDUINO_PORT}")
            break
        except (serial.SerialException, FileNotFoundError) as e:
            print(f"[ARDUINO] Attempt {attempt}/{ARDUINO_CONNECT_RETRIES}: "
                  f"could not open {ARDUINO_PORT} ({e}).")
            if attempt < ARDUINO_CONNECT_RETRIES:
                time.sleep(ARDUINO_CONNECT_RETRY_DELAY)

if not ARDUINO_CONNECTED:
    print("[DEBUG MODE] Running without Arduino. Commands will be printed, not sent.")
    print("[DEBUG MODE] Set FORCE_DEBUG_MODE = False and connect the Nano to go live.\n")

last_ctrl_time = time.time()
lock = threading.Lock()
failsafe_active = False  # tracks whether we've already printed the failsafe trip

# --- Ramp preview state (debug mode only) ---
# Mirrors currentLeft/currentRight/targetLeft/targetRight in the .ino.
sim_current_left = 0.0
sim_current_right = 0.0
sim_target_left = 0.0
sim_target_right = 0.0
sim_lock = threading.Lock()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def format_elapsed_time(seconds):
    """Format a duration in seconds as HH:MM:SS, matching the format the
    dashboard already expects for elapsedTime."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def wasd_to_diff(keys):
    """Convert a set of held keys ({'w','a','s','d'}) into left/right
    percentages for tank/differential drive, respecting MAX_SPEED_PCT."""
    throttle = 0
    turn = 0
    if 'w' in keys:
        throttle += 100
    if 's' in keys:
        throttle -= 100
    if 'a' in keys:
        turn -= 100
    if 'd' in keys:
        turn += 100

    left = throttle + turn
    right = throttle - turn

    # Normalize proportionally if the combined magnitude exceeds 100,
    # so a turn command doesn't just get clipped away at full throttle.
    largest = max(abs(left), abs(right), 100)
    left = left * 100 / largest
    right = right * 100 / largest

    left = clamp(left, -MAX_SPEED_PCT, MAX_SPEED_PCT)
    right = clamp(right, -MAX_SPEED_PCT, MAX_SPEED_PCT)
    return round(left), round(right)


def percent_to_pulse(percent):
    """Preview the microsecond pulse the Arduino would generate for a given
    target percent. Mirrors writeMotor() in motor_controller.ino."""
    percent = clamp(percent, -MAX_SPEED_PCT, MAX_SPEED_PCT)
    if percent >= 0:
        return round(PULSE_NEUTRAL + (percent / 100.0) * (PULSE_FORWARD_MAX - PULSE_NEUTRAL))
    else:
        return round(PULSE_NEUTRAL + (percent / 100.0) * (PULSE_NEUTRAL - PULSE_REVERSE_MAX))


def describe_keys(keys):
    labels = {'w': 'W(fwd)', 'a': 'A(left)', 's': 'S(back)', 'd': 'D(right)'}
    held = [labels[k] for k in sorted(keys) if k in labels]
    return " + ".join(held) if held else "(none held)"


def print_ctrl_debug(keys, left, right, cmd):
    left_pulse = percent_to_pulse(left)
    right_pulse = percent_to_pulse(right)
    # print("-" * 48)
    # print(f"Keys held     : {describe_keys(keys)}")
    # print(f"Left  target  : {left:+d}%  -> {left_pulse}us pulse")
    # print(f"Right target  : {right:+d}%  -> {right_pulse}us pulse")
    # print(f"Arduino cmd   : {cmd}")


def send_to_arduino(cmd):
    global ARDUINO_CONNECTED
    if ARDUINO_CONNECTED and arduino:
        try:
            arduino.write((cmd + "\n").encode("utf-8"))
            return
        except (serial.SerialException, OSError) as e:
            print(f"[ARDUINO] Write error ({e}). Falling back to debug mode.")
            ARDUINO_CONNECTED = False
            threading.Thread(target=ramp_simulator_loop, daemon=True).start()

    print(f"[SIMULATED -> Arduino]  {cmd}")

    # Also drive the software ramp preview so the gradual speed change is
    # visible even without the real Arduino connected.
    global sim_target_left, sim_target_right, sim_current_left, sim_current_right
    with sim_lock:
        if cmd in ("STOP", "ARM"):
            # Matches handleCommand() in the .ino: both are an instant cut,
            # not a ramp.
            sim_target_left = sim_target_right = 0.0
            sim_current_left = sim_current_right = 0.0
        elif cmd.startswith("T:"):
            try:
                l_str, r_str = cmd[2:].split(",")
                sim_target_left = float(l_str)
                sim_target_right = float(r_str)
            except ValueError:
                pass


def ramp_simulator_loop():
    """Debug-mode only. Runs at ~50Hz, same as the Arduino's loop() tick,
    and applies the exact same ramp math as applyRamp() in
    motor_controller.ino so you can watch the gradual speed change happen
    before the real hardware exists. Only prints while a value is still
    moving toward its target, so it stays quiet once things settle."""
    global sim_current_left, sim_current_right
    last_tick = time.time()
    last_print = 0.0
    while True:
        now = time.time()
        dt_ms = (now - last_tick) * 1000.0
        last_tick = now

        with sim_lock:
            max_step = (2.0 * MAX_SPEED_PCT) * dt_ms / RAMP_MS

            if sim_current_left < sim_target_left:
                sim_current_left = min(sim_current_left + max_step, sim_target_left)
            elif sim_current_left > sim_target_left:
                sim_current_left = max(sim_current_left - max_step, sim_target_left)

            if sim_current_right < sim_target_right:
                sim_current_right = min(sim_current_right + max_step, sim_target_right)
            elif sim_current_right > sim_target_right:
                sim_current_right = max(sim_current_right - max_step, sim_target_right)

            cl, cr = sim_current_left, sim_current_right
            tl, tr = sim_target_left, sim_target_right

        still_ramping = round(cl, 1) != round(tl, 1) or round(cr, 1) != round(tr, 1)
        if still_ramping and (now - last_print) > 0.2:
            print(f"  [RAMP PREVIEW] current L={cl:+5.1f}%  R={cr:+5.1f}%   "
                  f"-> target L={tl:+.0f}%  R={tr:+.0f}%")
            last_print = now

        time.sleep(0.02)  # ~50Hz, matches the Arduino's delay(20) loop


def arduino_reader_loop():
    """Only runs when the Arduino is actually connected. Continuously reads
    whatever the Nano sends back over serial and prints it. This is the real
    proof of a live connection: the "Arduino connected on ..." line at
    startup only means the OS handed over a valid serial port, not that
    anything is actually alive on the other end. Seeing the Nano's own boot
    messages (from armEscs() in motor_controller.ino) show up here means the
    two boards are genuinely talking, even with no motors attached."""
    while True:
        try:
            line = arduino.readline().decode("utf-8", errors="ignore").strip()
        except (serial.SerialException, OSError):
            print("[ARDUINO] Lost connection to the serial port.")
            return
        if line:
            print(f"[ARDUINO] {line}")


def reconnect_radio():
    """Close and reopen the radio serial port after a runtime failure,
    reusing the same retrying open_radio() logic used at startup. Holds
    radio_lock for the whole swap (open_radio()'s retry loop included) so
    no write can land on a half-closed or stale radio object mid-reconnect -
    any radio_write() call from another thread just waits until this
    finishes instead."""
    global radio
    with radio_lock:
        try:
            radio.close()
        except Exception:
            pass
        radio = open_radio()


def radio_write(data: bytes):
    """Write to the radio link, reconnecting automatically on failure
    instead of letting the exception propagate and crash the process.
    Serialized via radio_lock so telemetry_loop()'s periodic send and
    main()'s CTRL/ARM/STOP acks - running on separate threads - can never
    land on the wire at the same time."""
    with radio_lock:
        try:
            radio.write(data)
        except (serial.SerialException, OSError) as e:
            print(f"[RADIO] Write error ({e}). Reconnecting...")
        else:
            return
    # Reconnect outside the `with` above - reconnect_radio() takes radio_lock
    # itself, and Lock isn't reentrant.
    reconnect_radio()


def get_telemetry():
    elapsed_seconds = time.time() - SCRIPT_START_TIME
    return {
        "type": "telemetry",
        "elapsedTime": format_elapsed_time(elapsed_seconds),
        "etaCompletion": "00:41:15",
        "speed": 0.3,
        "trashCollected": 100,
        "gps": {
            "latitude": 33.7756,
            "longitude": -84.3963
        },
        "progressMeter": 56,
        "state": "RUNNING"
    }


def telemetry_loop():
    while True:
        telemetry_update = json.dumps(get_telemetry()) + "\n"
        radio_write(telemetry_update.encode("utf-8"))
        time.sleep(1)


def failsafe_loop():
    """If the last control packet is older than FAILSAFE_TIMEOUT, force
    the Arduino to stop. Protects against radio dropout or a laptop crash.
    Only prints once per trip so it doesn't spam the console."""
    global last_ctrl_time, failsafe_active
    while True:
        with lock:
            elapsed = time.time() - last_ctrl_time
        if elapsed > FAILSAFE_TIMEOUT:
            if not failsafe_active:
                print(f"[FAILSAFE] No control packet for {elapsed:.1f}s -> forcing stop")
                failsafe_active = True
            send_to_arduino("T:0,0")
        else:
            failsafe_active = False
        time.sleep(0.2)


def main():
    global last_ctrl_time

    print("Arming motor controller...")
    send_to_arduino("ARM")
    time.sleep(3 if ARDUINO_CONNECTED else 0.2)  # skip the long wait in debug mode
    print("Armed. Pi bridge is running.\n")

    threading.Thread(target=telemetry_loop, daemon=True).start()
    threading.Thread(target=failsafe_loop, daemon=True).start()
    if ARDUINO_CONNECTED:
        threading.Thread(target=arduino_reader_loop, daemon=True).start()
    else:
        threading.Thread(target=ramp_simulator_loop, daemon=True).start()

    while True:
        # Snapshot the current radio object under the lock, then read
        # outside it - readline() blocks for up to 1s (its timeout), and
        # holding radio_lock that whole time would stall telemetry_loop()'s
        # writes for no reason. This only needs to protect against reading
        # from a radio object mid-swap during a reconnect, not against
        # overlapping with another write.
        with radio_lock:
            active_radio = radio
        try:
            line = active_radio.readline().decode("utf-8", errors="ignore").strip()
        except (serial.SerialException, OSError) as e:
            print(f"[RADIO] Read error ({e}). Reconnecting...")
            reconnect_radio()
            continue

        if not line:
            continue

        print(f"\nRadio RX: {line!r}")

        if line == "STOP":
            send_to_arduino("STOP")
            with lock:
                last_ctrl_time = time.time()
            radio_write(b"Pi: STOP received\n")

        elif line == "ARM":
            send_to_arduino("ARM")
            radio_write(b"Pi: ARM received\n")

        elif line.startswith("CTRL:"):
            payload = line[len("CTRL:"):]
            # Optional "<keys>:<scale>" suffix (e.g. "d:50" for a half-speed
            # search spin) - rpartition so a bare "CTRL:wa" with no scale
            # (the normal WASD case) still parses correctly, since it has no
            # colon and falls through to the 100% default below.
            keys_part, sep, scale_part = payload.rpartition(":")
            if sep and scale_part.lstrip("-").isdigit():
                keys_str = keys_part
                scale_pct = clamp(int(scale_part), 0, 100)
            else:
                keys_str = payload
                scale_pct = 100

            keys = set(keys_str.lower())
            left, right = wasd_to_diff(keys)
            scale = scale_pct / 100.0
            left = round(left * scale)
            right = round(right * scale)
            cmd = f"T:{left},{right}"

            print_ctrl_debug(keys, left, right, cmd)

            send_to_arduino(cmd)
            with lock:
                last_ctrl_time = time.time()
            radio_write(f"Pi: L{left} R{right}\n".encode("utf-8"))

        else:
            reply = f"Pi received: {line}\n"
            radio_write(reply.encode("utf-8"))


if __name__ == "__main__":
    main()
