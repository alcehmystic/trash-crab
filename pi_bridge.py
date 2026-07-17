"""
pi_bridge.py
------------
Runs on the Raspberry Pi. Bridges the long-range radio link (to the laptop),
the USB serial link (to the Arduino Nano motor controller), and a USB GPS
receiver (VK-162).

Responsibilities:
  - Receive WASD control packets from the laptop over radio
  - Translate them into left/right differential-drive target speeds
  - Forward target speeds to the Arduino as "T:<left>,<right>"
  - Enforce the global speed cap here too (defense in depth)
  - Run a failsafe watchdog: if no control packet arrives in time,
    force the Arduino to stop
  - Read NMEA sentences from the VK-162 GPS dongle and fold the latest
    fix into telemetry
  - Keep sending telemetry back to the laptop (unchanged from the
    original test harness, now with real GPS data)

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
  "ARM"          -> arm the ESCs
  "STOP"         -> emergency stop
  "CTRL:<keys>"  -> currently held keys, e.g. "CTRL:wa" for forward+left,
                    "CTRL:" (empty) when nothing is held

Radio messages sent back to the laptop (newline terminated):
  {"type":"telemetry", ...}  -> once per second, boat status for the
                                dashboard, including the latest GPS fix
  "Pi: ..."                  -> plain-text ACKs for ARM/STOP/CTRL

GPS SETUP (VK-162 USB GPS dongle):
  The VK-162 enumerates as a USB-serial device and streams standard NMEA
  0183 sentences at 9600 baud once it has a fix (can take up to ~30s
  outdoors on a cold start). Parsing uses pynmea2:
      pip install pynmea2
  If pynmea2 isn't installed, or the device can't be opened, the GPS
  thread logs that once and disables itself - everything else (drive,
  telemetry, failsafe) keeps running normally.
"""

import serial
import sys
import threading
import time
import json

# Under systemd, stdout is block-buffered by default, so print()s (radio RX,
# GPS status lines, etc.) don't reach `journalctl` until a full buffer
# flushes - which makes a running script look silent. Force line buffering so
# every line shows up in the service log immediately.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# Master switch for the GPS feed. Kept as a top-level flag (same pattern used
# elsewhere in this file for optional hardware) so it can be killed with one
# edit + restart if the GPS dongle is ever suspected of causing trouble.
GPS_ENABLED = True

# pynmea2 only needs to exist for the GPS thread. Import defensively so a
# missing dependency or a device hiccup never takes down the control/
# telemetry bridge - it just means the GPS feed stays disabled.
GPS_LIB_AVAILABLE = False
if GPS_ENABLED:
    try:
        import pynmea2
        GPS_LIB_AVAILABLE = True
        print("[GPS] pynmea2 imported OK.")
    except Exception as e:
        print(f"[GPS] pynmea2 unavailable ({type(e).__name__}: {e}); "
              "GPS feed disabled, everything else runs normally.")

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

# ---- GPS (VK-162 USB GPS dongle) config ----
# u-blox-based dongles like the VK-162 usually enumerate with a u-blox by-id
# name; check `ls -l /dev/serial/by-id/` on the actual Pi and update this if
# it doesn't match (same caveat as RADIO_PORT/ARDUINO_PORT below: with three
# USB-serial devices now plugged in, by-id names are the only ones stable
# across reboots - /dev/ttyUSB0 / /dev/ttyACM0 can and will move around).
GPS_PORT = "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_7_-_GPS_GNSS_Receiver-if00"
GPS_BAUD = 9600
GPS_RETRY_DELAY = 3.0             # seconds between reconnect attempts if the dongle isn't found
# A GGA/RMC sentence that hasn't updated the fix in longer than this is
# treated as stale: telemetry reports fix=False (but keeps the last known
# lat/lon) rather than silently claiming an old fix is still current.
GPS_STALE_TIMEOUT = 5.0

# PWM constants, mirrored from motor_controller.ino, used ONLY to preview
# what pulse width the Arduino would generate for a given target percent.
PULSE_NEUTRAL = 1500
PULSE_FORWARD_MAX = 2000
PULSE_REVERSE_MAX = 1000

# Ramp timing, mirrored from RAMP_MS in motor_controller.ino. Only used to
# drive the software ramp preview in debug mode below.
RAMP_MS = 1500

# NOTE: with the radio adapter, the Arduino, and the GPS dongle all on USB,
# device names like /dev/ttyUSB0 / /dev/ttyACM0 can swap on reboot depending
# on enumeration order. If you see the wrong device responding, check
# `ls -l /dev/serial/by-id/` for stable names and use those instead.

def open_radio():
    """Open the radio serial port, retrying indefinitely until it succeeds.
    Used both for the initial connection at startup (so a systemd-managed
    boot doesn't crash if the radio dongle hasn't finished enumerating yet)
    and for runtime reconnects after a dropped connection."""
    while True:
        try:
            # write_timeout bounds every radio.write(): without it, a write to a
            # backed-up port could block forever holding radio_lock, freezing
            # telemetry. With it, a stuck/oversized write raises instead of
            # hanging, and radio_write() handles it. Normal writes (telemetry
            # ~210 B) finish in well under this.
            ser = serial.Serial(RADIO_PORT, RADIO_BAUD, timeout=1, write_timeout=5)
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

# --- GPS state ---
# Updated by gps_loop() as NMEA sentences arrive, read by get_telemetry()
# once a second. Lat/lon (and altitude/satellites/course/speed) hold the
# last known good values even after the fix goes stale, so the dashboard
# can keep showing "last known position" - only `fix` flips to False.
gps_state = {
    "latitude": 0.0,
    "longitude": 0.0,
    "fix": False,
    "satellites": 0,
    "altitude": 0.0,
    "speedKnots": 0.0,
    "course": 0.0,
    # course is GPS course-over-ground (bearing derived from movement
    # between fixes), not a compass reading - the receiver only reports it
    # once it has detected real movement, and RMC's true_course field comes
    # back blank until then. courseValid tracks whether we've ever gotten a
    # real one, so 0.0's default doesn't get mistaken by the dashboard for
    # an actual "facing north" reading before the boat has moved.
    "courseValid": False,
}
gps_last_update = 0.0    # time.time() of the last sentence that carried a valid fix
gps_lock = threading.Lock()


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


GPS_DIAG_LOG_INTERVAL = 5.0  # seconds between GPS course/speed diagnostic lines
_gps_diag_last_log = 0.0

# Tracks every distinct NMEA sentence type seen since startup (GGA, RMC,
# VTG, GSA, GSV, ...), logged periodically. If "RMC" and "VTG" never show
# up here at all, the module simply isn't emitting course/track data in
# any sentence we'd recognize - a configuration/firmware fact, not
# something a code fix on this end can work around. If they DO show up
# but _log_gps_diagnostic keeps reporting "blank", the sentences are
# arriving but the course/track fields inside them are genuinely empty.
_gps_seen_sentence_types = set()
_gps_seen_last_log = 0.0


def _note_sentence_type_seen(sentence):
    """Prints the current full set on a time-based heartbeat (not only when
    a new type first appears), so the complete list is guaranteed to show
    up even if different sentence types trickle in slower than the
    throttle interval."""
    global _gps_seen_last_log
    if not sentence:
        return
    _gps_seen_sentence_types.add(sentence)
    now = time.time()
    if now - _gps_seen_last_log < GPS_DIAG_LOG_INTERVAL:
        return
    _gps_seen_last_log = now
    print(f"[GPS] Sentence types seen so far: {sorted(_gps_seen_sentence_types)}")


def _log_gps_diagnostic(source, speed, course):
    """Throttled heartbeat (at most once every GPS_DIAG_LOG_INTERVAL
    seconds) showing exactly what course/speed data RMC/VTG sentences are
    actually carrying, so this shows up in the normal `journalctl`/console
    log without needing a live SSH session to debug course-over-ground
    issues. course=None means the receiver reported that field blank -
    most commonly because the module hasn't detected enough real movement
    between fixes yet to compute a confident heading. That's expected
    physics for a GPS-only module (no compass), not necessarily a bug -
    but if this keeps printing "blank" while the boat is clearly moving
    at a normal pace with a healthy fix, that points at a real problem
    worth reporting back (e.g. this module's firmware not populating
    course/track fields at all)."""
    global _gps_diag_last_log
    now = time.time()
    if now - _gps_diag_last_log < GPS_DIAG_LOG_INTERVAL:
        return
    _gps_diag_last_log = now
    course_str = f"{course:.1f}deg" if course is not None else "blank (no course yet)"
    speed_str = f"{speed}kn" if speed is not None else "blank"
    print(f"[GPS] {source} speed={speed_str} course={course_str}")


def gps_loop():
    """Continuously read NMEA sentences from the VK-162 and update
    gps_state. Runs entirely independently of the radio/Arduino links - a
    disconnected or fixless GPS dongle never blocks or interferes with
    drive/telemetry, it just leaves gps_state at its last known values.

    GGA sentences supply fix quality, satellite count, and altitude. RMC
    and VTG both independently supply ground speed and course-over-ground -
    parsing both means a module that only reliably populates one of them
    still gets picked up. RMC can also update latitude/longitude, so it
    can refresh gps_last_update and, in turn, how fresh the dashboard
    considers the fix (VTG carries no position, only speed/track)."""
    global gps_last_update

    gps_serial = None
    while True:
        if gps_serial is None:
            try:
                gps_serial = serial.Serial(GPS_PORT, GPS_BAUD, timeout=1)
                print(f"[GPS] Connected on {GPS_PORT}")
            except (serial.SerialException, FileNotFoundError) as e:
                print(f"[GPS] Could not open {GPS_PORT} ({e}). Retrying in "
                      f"{GPS_RETRY_DELAY}s...")
                time.sleep(GPS_RETRY_DELAY)
                continue

        try:
            raw = gps_serial.readline().decode("ascii", errors="ignore").strip()
        except (serial.SerialException, OSError) as e:
            print(f"[GPS] Read error ({e}). Reconnecting...")
            try:
                gps_serial.close()
            except Exception:
                pass
            gps_serial = None
            time.sleep(GPS_RETRY_DELAY)
            continue

        if not raw.startswith("$"):
            continue  # partial line from mid-stream connect, or noise

        try:
            msg = pynmea2.parse(raw)
        except pynmea2.ParseError:
            continue  # a dropped/garbled byte mid-sentence - just skip it

        sentence = getattr(msg, "sentence_type", "")
        _note_sentence_type_seen(sentence)

        if sentence == "GGA":
            # gps_qual: 0 = no fix, 1 = GPS fix, 2 = DGPS fix, etc.
            try:
                fix_quality = int(msg.gps_qual)
            except (TypeError, ValueError):
                fix_quality = 0
            with gps_lock:
                try:
                    gps_state["satellites"] = int(msg.num_sats)
                except (TypeError, ValueError):
                    pass
                try:
                    gps_state["altitude"] = float(msg.altitude)
                except (TypeError, ValueError):
                    pass
                if fix_quality > 0:
                    gps_state["latitude"] = msg.latitude
                    gps_state["longitude"] = msg.longitude
                    gps_state["fix"] = True
                    gps_last_update = time.time()
                else:
                    gps_state["fix"] = False

        elif sentence == "RMC":
            valid = (msg.status == "A")  # 'A' = active/valid, 'V' = void
            with gps_lock:
                try:
                    gps_state["speedKnots"] = float(msg.spd_over_grnd)
                except (TypeError, ValueError):
                    pass
                try:
                    gps_state["course"] = float(msg.true_course)
                    gps_state["courseValid"] = True
                except (TypeError, ValueError):
                    pass
                if valid:
                    gps_state["latitude"] = msg.latitude
                    gps_state["longitude"] = msg.longitude
                    gps_state["fix"] = True
                    gps_last_update = time.time()
            _log_gps_diagnostic("RMC", msg.spd_over_grnd, msg.true_course)

        elif sentence == "VTG":
            # Independent of RMC - some receivers/firmwares populate this
            # sentence's course/speed more reliably than RMC's, so this is
            # a second chance at real course-over-ground data rather than
            # a strict requirement that RMC be the one that works.
            with gps_lock:
                try:
                    gps_state["speedKnots"] = float(msg.spd_over_grnd_kts)
                except (TypeError, ValueError):
                    pass
                try:
                    gps_state["course"] = float(msg.true_track)
                    gps_state["courseValid"] = True
                except (TypeError, ValueError):
                    pass
            _log_gps_diagnostic("VTG", msg.spd_over_grnd_kts, msg.true_track)


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
    main()'s CTRL/ARM/STOP acks - on separate threads - can never land on
    the wire at the same time."""
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

    with gps_lock:
        gps_snapshot = dict(gps_state)
        stale = (time.time() - gps_last_update) > GPS_STALE_TIMEOUT
    if stale:
        # Keep the last known lat/lon/etc. for the dashboard's "last known
        # position", but don't claim the fix is still current.
        gps_snapshot["fix"] = False

    return {
        "type": "telemetry",
        "elapsedTime": format_elapsed_time(elapsed_seconds),
        "etaCompletion": "00:41:15",
        # battery/speed/trashCollected/waterTemperature are None: no sensor
        # for any of these is wired up yet, and the dashboard shows "No
        # Sensor Added" for a None value rather than a fake reading. Swap a
        # real number in here once the corresponding sensor exists on the
        # Pi and the dashboard picks it up automatically.
        "battery": None,
        "speed": None,
        "trashCollected": None,
        "waterTemperature": None,
        "gps": gps_snapshot,
        "progressMeter": 0,
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

    # GPS feed is optional: only starts if pynmea2 imported and the feature
    # is enabled. Everything above still runs without it.
    if GPS_ENABLED and GPS_LIB_AVAILABLE:
        threading.Thread(target=gps_loop, daemon=True).start()

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
            keys = set(line[len("CTRL:"):].lower())
            left, right = wasd_to_diff(keys)
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
