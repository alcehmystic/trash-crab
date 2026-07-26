"""
dashboard-side_telecom_server.py

Runs on the operator's laptop, alongside the React dashboard (`npm run dev`
starts both together). This is the HTTP bridge between the dashboard and
the Pi over the radio link. It owns the COM port, so only one process can
run at a time. The dashboard drives the boat over HTTP instead of reading
keystrokes directly on this machine.

Endpoints:
  GET  /telemetry        -> latest telemetry JSON received from the Pi.
                             Returns a zero-state placeholder until the
                             first real packet arrives.
  GET  /status           -> {lastCommandSent, lastCommandAt, lastAck,
                             lastAckAt, lastTelemetryAt, lastGpsFixAt,
                             connected} - what we sent vs. what the Pi
                             actually echoed back, so the dashboard can
                             show the link is healthy. lastGpsFixAt is
                             stamped with this machine's clock so "time
                             since last fix" doesn't depend on the Pi's
                             clock being in sync.
  POST /command/arm      -> sends ARM
  POST /command/stop     -> sends STOP
  POST /command/ctrl     -> body: {"keys": "wa", "scale": 100} - currently
                             held drive keys plus an optional speed scale
                             (0-100, defaults to 100), sent as
                             "CTRL:wa:100\\n". Called on an interval from
                             the frontend, not once per keypress.
  POST /command/<other>  -> anything else is logged but not forwarded to
                             the Pi (returns 501).

Requires: pip install flask flask-cors pyserial
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import serial
import threading
import time
import json
import logging

# =====================================================================
# CONFIGURATION : set PORT to whatever COM port your radio adapter shows
# up as (Device Manager on Windows, or /dev/tty* on Mac/Linux).
# =====================================================================
PORT = "COM3"
BAUD = 57600
RECONNECT_DELAY = 2  # seconds between attempts to (re)open the radio port

# Off by default. The CTRL heartbeat fires every ~300ms, and the
# dashboard's own "Command Received" panel already shows this same data.
VERBOSE_ACK_LOGGING = False

# Flask's dev server logs one line per request. With telemetry/status/ctrl
# all polling several times a second, that's a lot of console spam - quiet
# it down to warnings/errors only.
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)
CORS(app)  # the Vite dev server runs on a different port, so the browser
           # would otherwise block these requests as cross-origin

# Opened lazily by connect_radio_loop, not at import time, since this file
# starts alongside `npm run dev` even when the radio isn't plugged in yet.
radio = None
radio_lock = threading.Lock()  # serializes reads/writes. The link is
                                # effectively half-duplex


def connect_radio_loop():
    global radio
    while True:
        with radio_lock:
            connected = radio is not None
        if connected:
            time.sleep(RECONNECT_DELAY)
            continue
        try:
            opened = serial.Serial(PORT, BAUD, timeout=1)
            with radio_lock:
                radio = opened
            print(f"[RADIO] Connected on {PORT}")
        except (serial.SerialException, OSError) as e:
            print(f"[RADIO] {PORT} not available ({e}); retrying in {RECONNECT_DELAY}s")
            time.sleep(RECONNECT_DELAY)


# Matches the dashboard's initial state so it's safe to render before the
# first real telemetry packet arrives.
latest_telemetry = {
    "elapsedTime": "00:00:00",
    "etaCompletion": "00:00:00",
    # None, not 0 - the dashboard shows "No Sensor Added" instead of a fake
    # reading for sensors that aren't wired up yet.
    "battery": None,
    "speed": None,
    "trashCollected": None,
    "waterTemperature": None,
    "gps": {
        "latitude": 0,
        "longitude": 0,
        "fix": False,
        "satellites": 0,
        "altitude": 0,
        "speedKnots": 0,
        "course": 0,
        "courseValid": False,
    },
    "progressMeter": 0,
    "state": "NO DATA"
}
last_telemetry_at = None  # epoch seconds of the last telemetry packet received
last_gps_fix_at = None    # epoch seconds (this machine's clock) of the last
                          # telemetry packet whose gps.fix was true
telemetry_lock = threading.Lock()

# What we last sent vs. the last plain-text line the Pi sent back (ARM/
# STOP/CTRL acks), so the dashboard can catch a dropped or corrupted
# packet on the link.
command_status = {
    "lastCommandSent": None,
    "lastCommandAt": None,
    "lastAck": None,
    "lastAckAt": None,
}
command_lock = threading.Lock()


def radio_write(data: bytes) -> bool:
    with radio_lock:
        if radio is None:
            return False
        try:
            radio.write(data)
            return True
        except (serial.SerialException, OSError) as e:
            print(f"[RADIO] Write error: {e}")
            return False


def radio_reader_loop():
    """Continuously reads from the radio and caches the latest telemetry
    packet, so GET /telemetry can answer instantly instead of blocking on
    the serial link for every dashboard poll."""
    global latest_telemetry, last_telemetry_at, last_gps_fix_at
    while True:
        with radio_lock:
            active = radio
        if active is None:
            time.sleep(0.5)
            continue

        try:
            line = active.readline().decode("utf-8", errors="ignore").strip()
        except (serial.SerialException, OSError) as e:
            print(f"[RADIO] Read error: {e}")
            line = ""
            time.sleep(0.5)

        if not line:
            continue

        try:
            data = json.loads(line)
            if data.get("type") == "telemetry":
                with telemetry_lock:
                    latest_telemetry = data
                    last_telemetry_at = time.time()
                    if data.get("gps", {}).get("fix"):
                        last_gps_fix_at = last_telemetry_at
        except json.JSONDecodeError:
            # Non-JSON lines are ARM/STOP/CTRL acks or other plain-text replies
            if VERBOSE_ACK_LOGGING:
                print("Pi said:", line)
            with command_lock:
                command_status["lastAck"] = line
                command_status["lastAckAt"] = time.time()


@app.route("/telemetry", methods=["GET"])
def get_telemetry():
    with telemetry_lock:
        return jsonify(latest_telemetry)


@app.route("/status", methods=["GET"])
def get_status():
    with radio_lock:
        connected = radio is not None
    with command_lock:
        status = dict(command_status)
    with telemetry_lock:
        status["lastTelemetryAt"] = last_telemetry_at
        status["lastGpsFixAt"] = last_gps_fix_at
    status["connected"] = connected
    return jsonify(status)


@app.route("/command/<cmd>", methods=["POST"])
def command(cmd):
    if cmd == "arm":
        payload = "ARM"
        ok = radio_write(b"ARM\n")
    elif cmd == "stop":
        payload = "STOP"
        ok = radio_write(b"STOP\n")
    elif cmd == "ctrl":
        body = request.get_json(silent=True) or {}
        keys = body.get("keys", "")
        scale = body.get("scale", 100)
        payload = f"CTRL:{keys}:{scale}"
        ok = radio_write(f"{payload}\n".encode("utf-8"))
    else:
        print(f"[UNIMPLEMENTED] Dashboard requested '{cmd}' - no Pi-side handler yet")
        return jsonify({"status": "not_implemented", "command": cmd}), 501

    with command_lock:
        command_status["lastCommandSent"] = payload
        command_status["lastCommandAt"] = time.time()

    if not ok:
        return jsonify({"status": "radio_error", "command": cmd}), 503
    return jsonify({"status": "sent", "command": cmd})


if __name__ == "__main__":
    threading.Thread(target=connect_radio_loop, daemon=True).start()
    threading.Thread(target=radio_reader_loop, daemon=True).start()
    print("Telecom server running on http://localhost:5001")
    # threaded=True lets Flask handle the dashboard's concurrent telemetry/
    # status/ctrl polling in parallel instead of queuing requests one at a time.
    app.run(host="0.0.0.0", port=5001, threaded=True)
