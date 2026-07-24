"""
pi-side_telecom_server.py

Runs on the Raspberry Pi. This is the bridge between the long-range radio
link (to the dashboard laptop), the Arduino Nano motor controller (over
USB serial), and the VK-162 USB GPS dongle.

What it does:
  - Reads WASD control packets sent from the laptop over radio
  - Converts them into left/right motor speeds and forwards them to the
    Arduino as "T:<left>,<right>"
  - Runs a failsafe watchdog that stops the boat if no control packet
    arrives in time (radio dropout, laptop crash, etc.)
  - Reads NMEA sentences from the GPS dongle and includes the latest fix
    in telemetry
  - Sends a telemetry packet back to the laptop once a second

DEBUG MODE:
  If the Arduino can't be opened on ARDUINO_PORT, the script doesn't crash -
  it just prints what it would have sent instead of writing to serial. Set
  FORCE_DEBUG_MODE = True below to force this even with the Arduino plugged
  in (useful for testing control logic before the hardware is wired up).

Radio protocol from the laptop (newline terminated):
  "ARM"          -> arm the ESCs
  "STOP"         -> emergency stop
  "CTRL:<keys>"  -> currently held keys, e.g. "CTRL:wa" for forward+left

Radio messages sent back to the laptop (newline terminated):
  {"type":"telemetry", ...}  -> once per second
  "Pi: ..."                  -> plain-text ACKs for ARM/STOP/CTRL

GPS setup:
  Needs pynmea2 to parse NMEA sentences: pip install pynmea2
  If it's not installed, or the dongle isn't plugged in, the GPS thread
  just logs that once and disables itself - everything else keeps running.
"""

import serial
import sys
import threading
import time
import json

# Running under systemd buffers stdout in full blocks instead of by line,
# so prints don't show up in `journalctl` until the buffer fills. Force
# line buffering so log output shows up right away.
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# =====================================================================
# CONFIGURATION - update the port values below to match your own setup.
# Defaults shown are what our Trash Crab unit uses.
# =====================================================================

# Long-range radio link to the laptop dashboard.
RADIO_PORT = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_USB_to_UART_Bridge_Controller_0001-if00-port0"
RADIO_BAUD = 57600

# USB serial link to the Arduino Nano motor controller.
ARDUINO_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
ARDUINO_BAUD = 57600

# VK-162 USB GPS dongle.
GPS_PORT = "/dev/serial/by-id/usb-u-blox_AG_-_www.u-blox.com_u-blox_7_-_GPS_GNSS_Receiver-if00"
GPS_BAUD = 9600

# With three USB-serial devices plugged into the Pi at once, /dev/ttyUSB0 or /dev/ttyACM0 style names
# can swap around on every reboot depending on enumeration order. Run `ls -l /dev/serial/by-id/` on 
# your own Pi and copy the matching by-id name into the three PORT values above. These stay stable across reboots.

GPS_ENABLED = True                # master on/off switch for the GPS feed
GPS_RETRY_DELAY = 3.0              # seconds between GPS reconnect attempts
GPS_STALE_TIMEOUT = 5.0            # a fix older than this is reported as lost

MAX_SPEED_PCT = 85                 # global speed cap, mirrors the Arduino-side cap
FAILSAFE_TIMEOUT = 1.0             # seconds without a control packet before auto-stop
FORCE_DEBUG_MODE = False           # True = always print instead of writing to the Arduino

ARDUINO_CONNECT_RETRIES = 5        # startup connection attempts before falling back to debug mode
ARDUINO_CONNECT_RETRY_DELAY = 2    # seconds between those attempts

# PWM constants mirrored from arduino-motor-controller.ino. Used only to
# preview what pulse width the Arduino would generate, for the debug-mode printout.
PULSE_NEUTRAL = 1500
PULSE_FORWARD_MAX = 2000
PULSE_REVERSE_MAX = 1000
RAMP_MS = 1500                     # ramp time mirrored from arduino-motor-controller.ino

GPS_DIAG_LOG_INTERVAL = 5.0        # seconds between GPS diagnostic log lines

# pynmea2 is only needed for GPS parsing, so import it defensively.
# A missing dependency shouldn't take down the rest of the bridge.
GPS_LIB_AVAILABLE = False
if GPS_ENABLED:
    try:
        import pynmea2
        GPS_LIB_AVAILABLE = True
        print("[GPS] pynmea2 imported OK.")
    except Exception as e:
        print(f"[GPS] pynmea2 unavailable ({type(e).__name__}: {e}); "
              "GPS feed disabled, everything else runs normally.")

# Timestamp the script started, so elapsed time in telemetry is measured
# from here and not from the first ARM/control packet.
SCRIPT_START_TIME = time.time()


def open_radio():
    """Open the radio serial port, retrying forever until it connects.
    Used both at startup and to reconnect after a dropped link."""
    while True:
        try:
            # write_timeout keeps a stuck/backed-up write from hanging
            # forever and freezing telemetry along with it.
            ser = serial.Serial(RADIO_PORT, RADIO_BAUD, timeout=1, write_timeout=5)
            print(f"Radio connected on {RADIO_PORT}")
            return ser
        except (serial.SerialException, FileNotFoundError) as e:
            print(f"[RADIO] Could not open {RADIO_PORT} ({e}). Retrying in 2s...")
            time.sleep(2)


radio = open_radio()

# Guards every read/write on `radio`. The radio link is half-duplex, so
# without this lock the telemetry thread and the main control loop could
# collide mid-write.
radio_lock = threading.Lock()

arduino = None
ARDUINO_CONNECTED = False
if not FORCE_DEBUG_MODE:
    for attempt in range(1, ARDUINO_CONNECT_RETRIES + 1):
        try:
            arduino = serial.Serial(ARDUINO_PORT, ARDUINO_BAUD, timeout=1)
            time.sleep(2)  # give the Arduino time to reset after the port opens
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
failsafe_active = False  # only used so the failsafe trip message prints once, not every loop

# --- Debug-mode ramp preview state (mirrors currentLeft/Right, targetLeft/Right in the .ino) ---
sim_current_left = 0.0
sim_current_right = 0.0
sim_target_left = 0.0
sim_target_right = 0.0
sim_lock = threading.Lock()

# --- GPS state, updated by gps_loop(), read once a second by get_telemetry() ---
gps_state = {
    "latitude": 0.0,
    "longitude": 0.0,
    "fix": False,
    "satellites": 0,
    "altitude": 0.0,
    "speedKnots": 0.0,
    "course": 0.0,
    # course is GPS course-over-ground, not a compass heading - the module
    # only reports it once it has detected real movement. courseValid tells
    # the dashboard whether we've ever gotten a real reading, so the 0.0
    # default doesn't look like "facing north" before the boat has moved.
    "courseValid": False,
}
gps_last_update = 0.0    # time.time() of the last sentence that carried a valid fix
gps_lock = threading.Lock()


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def format_elapsed_time(seconds):
    """Format seconds as HH:MM:SS for the dashboard's elapsedTime field."""
    total_seconds = int(seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def wasd_to_diff(keys):
    """Turn a set of held keys ({'w','a','s','d'}) into left/right motor
    percentages for tank/differential drive, capped at MAX_SPEED_PCT."""
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

    # Scale down proportionally instead of just clipping, so a turn doesn't
    # get lost when throttle is already maxed out.
    largest = max(abs(left), abs(right), 100)
    left = left * 100 / largest
    right = right * 100 / largest

    left = clamp(left, -MAX_SPEED_PCT, MAX_SPEED_PCT)
    right = clamp(right, -MAX_SPEED_PCT, MAX_SPEED_PCT)
    return round(left), round(right)


def percent_to_pulse(percent):
    """Preview the microsecond pulse the Arduino would generate for a given
    target percent. Mirrors writeMotor() in arduino-motor-controller.ino."""
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
    # Disabled by default as it gets noisy fast once the CTRL heartbeat is
    # running. Uncomment for debugging control logic.
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

    # No real Arduino connected, so drive the software ramp preview instead,
    # to still show the gradual speed change in the console.
    global sim_target_left, sim_target_right, sim_current_left, sim_current_right
    with sim_lock:
        if cmd in ("STOP", "ARM"):
            # Matches handleCommand() in the .ino: instant cut, not a ramp.
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
    """Debug-mode only. Runs at ~50Hz like the Arduino's loop(), applying
    the same ramp math as applyRamp() in arduino-motor-controller.ino, so you can
    preview the gradual speed change before the real hardware exists."""
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
    """Only runs when the Arduino is actually connected. Prints whatever
    the Nano sends back, which is the real proof the two boards are
    talking - opening the serial port alone doesn't guarantee that."""
    while True:
        try:
            line = arduino.readline().decode("utf-8", errors="ignore").strip()
        except (serial.SerialException, OSError):
            print("[ARDUINO] Lost connection to the serial port.")
            return
        if line:
            print(f"[ARDUINO] {line}")


_gps_diag_last_log = 0.0

# Tracks every distinct NMEA sentence type seen since startup (GGA, RMC,
# VTG, ...), logged periodically for debugging a GPS module that isn't
# reporting course/speed the way you'd expect.
_gps_seen_sentence_types = set()
_gps_seen_last_log = 0.0


def _note_sentence_type_seen(sentence):
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
    """Throttled log of what course/speed RMC/VTG sentences are actually
    carrying. course=None just means the module hasn't detected enough
    movement yet to compute a heading. Expected for our GPS-only module
    with no compass, not necessarily a bug."""
    global _gps_diag_last_log
    now = time.time()
    if now - _gps_diag_last_log < GPS_DIAG_LOG_INTERVAL:
        return
    _gps_diag_last_log = now
    course_str = f"{course:.1f}deg" if course is not None else "blank (no course yet)"
    speed_str = f"{speed}kn" if speed is not None else "blank"
    print(f"[GPS] {source} speed={speed_str} course={course_str}")


def gps_loop():
    """Continuously reads NMEA sentences from the GPS dongle and updates
    gps_state. Runs independently of the radio/Arduino links. A
    disconnected or fixless GPS never blocks drive or telemetry, it just
    leaves gps_state at its last known values.

    GGA gives fix quality, satellite count, and altitude. RMC and VTG both
    independently carry speed/course-over-ground, so parsing both means a
    module that only reliably fills in one of them still works."""
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
            continue  # a dropped/garbled byte mid-sentence, just skip it

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
            # VTG carries no position, only speed/track. A second chance
            # at course-over-ground data if RMC's fields come back blank.
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
    """Close and reopen the radio port after a runtime failure. Holds
    radio_lock for the whole swap so no other thread can write to a
    half-closed or stale radio object mid-reconnect."""
    global radio
    with radio_lock:
        try:
            radio.close()
        except Exception:
            pass
        radio = open_radio()


def radio_write(data: bytes):
    """Write to the radio, reconnecting automatically instead of crashing
    the process on a dropped link."""
    with radio_lock:
        try:
            radio.write(data)
        except (serial.SerialException, OSError) as e:
            print(f"[RADIO] Write error ({e}). Reconnecting...")
        else:
            return
    # Reconnect outside the `with` above since Lock isn't reentrant and
    # reconnect_radio() takes radio_lock itself.
    reconnect_radio()


def get_telemetry():
    elapsed_seconds = time.time() - SCRIPT_START_TIME

    with gps_lock:
        gps_snapshot = dict(gps_state)
        stale = (time.time() - gps_last_update) > GPS_STALE_TIMEOUT
    if stale:
        # Keep the last known lat/lon for "last known position" on the
        # dashboard, but stop claiming the fix is still current.
        gps_snapshot["fix"] = False

    return {
        "type": "telemetry",
        "elapsedTime": format_elapsed_time(elapsed_seconds),
        "etaCompletion": "00:41:15",
        # battery/speed/trashCollected/waterTemperature stay None until
        # those sensors actually exist on the boat - the dashboard shows
        # "No Sensor Added" for None instead of a fake reading.
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
    """Forces the Arduino to stop if the last control packet is older than
    FAILSAFE_TIMEOUT - protects against radio dropout or a laptop crash."""
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

    # GPS feed is optional. Only starts if pynmea2 imported and the
    # feature is enabled. Everything else above still runs without it.
    if GPS_ENABLED and GPS_LIB_AVAILABLE:
        threading.Thread(target=gps_loop, daemon=True).start()

    while True:
        # Snapshot the radio object under the lock, then read outside it.
        # readline() blocks for up to 1s, and holding the lock that whole
        # time would stall telemetry_loop()'s writes for no reason.
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
