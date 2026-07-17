"""
telecom_server.py
------------------
Local HTTP bridge between the React dashboard and the Pi, over the same
radio link laptop_controller.py used. This REPLACES laptop_controller.py
as the process that owns the serial connection — run this instead of it,
not alongside it, since only one process can hold the COM port at a time.
The dashboard now drives the boat over HTTP instead of pynput capturing
keys directly on this machine.

Endpoints:
  GET  /telemetry        -> latest telemetry JSON received from the Pi.
                             Defaults to the same zero-state shape the
                             dashboard already initializes with, so it's
                             safe to render before the first packet arrives.
  GET  /status           -> {lastCommandSent, lastCommandAt, lastAck,
                             lastAckAt, lastTelemetryAt, lastGpsFixAt,
                             connected} - lets the dashboard show what was
                             sent next to what the Pi actually echoed back,
                             to check the link's integrity. lastTelemetryAt
                             is included too so "time since last contact"
                             reflects any traffic from the Pi, not just
                             command acks - telemetry keeps arriving once a
                             second even when nothing is being driven.
                             lastGpsFixAt is stamped with THIS machine's
                             clock the moment a telemetry packet reports
                             gps.fix=true, so the dashboard's "location last
                             received Xs ago" never depends on the Pi's
                             clock being in sync with the browser's.
  POST /command/arm      -> send ARM
  POST /command/stop     -> send STOP
  POST /command/ctrl     -> body: {"keys": "wa", "scale": 100} - currently
                             held drive keys plus an optional speed scale
                             (0-100, defaults to 100), forwarded as
                             "CTRL:wa:100\\n". Call this on an interval from
                             the frontend (matching the 300ms heartbeat
                             laptop_controller.py used), not once per
                             keypress. scale lets things like an autonomous
                             search spin request less than full speed.
  POST /command/<other>  -> anything else is logged but NOT forwarded to
                             the Pi. The dashboard only ever sends arm/stop/
                             ctrl - this exists as a guard for anything else
                             hitting this route, not because other commands
                             are expected. Returns 501 so the frontend can
                             tell the difference between "sent" and "not
                             real yet".

Requires: pip install flask flask-cors pyserial
"""

from flask import Flask, jsonify, request
from flask_cors import CORS
import serial
import threading
import time
import json
import logging

PORT = "COM3"
BAUD = 57600
RECONNECT_DELAY = 2  # seconds between attempts to (re)open the radio port

# Off by default: printing "Pi said: ..." on every ack fires on the same
# ~300ms cadence as the CTRL heartbeat, and the dashboard's own "Command
# Received" panel (fed by /status) already shows this same data live.
VERBOSE_ACK_LOGGING = False

# Werkzeug's dev-server access log prints one line per HTTP request. With
# telemetry polling at 1/sec plus status+ctrl polling at ~3.3/sec each (plus
# a CORS preflight per POST), that's 8-10 lines/sec of console I/O during
# active driving. Quiet it down to warnings/errors only; our own prints
# above still cover the events worth seeing.
logging.getLogger("werkzeug").setLevel(logging.WARNING)

app = Flask(__name__)
CORS(app)  # dashboard dev server runs on a different port (5173/3000);
           # without this the browser blocks every request as cross-origin

# Opened lazily by connect_radio_loop rather than at import time - this file
# now starts alongside `npm run dev`, where the radio/Pi frequently isn't
# plugged in yet (or at all, for frontend-only work). Blocking here would
# crash the whole dev process instead of just leaving telemetry at zero.
radio = None
radio_lock = threading.Lock()  # serializes all reads/writes, same reasoning
                                # as pi_bridge.py / laptop_controller.py:
                                # the link is effectively half-duplex


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

# Matches the dashboard's initial useState shape exactly, so it's safe to
# render immediately even before the first real telemetry packet arrives.
latest_telemetry = {
    "elapsedTime": "00:00:00",
    "etaCompletion": "00:00:00",
    # None, not 0 - matches pi_bridge.py's placeholder shape so the
    # dashboard shows "No Sensor Added" instead of a fake reading before any
    # of these sensors exist.
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
last_gps_fix_at = None    # epoch seconds (THIS machine's clock) of the last
                           # telemetry packet whose gps.fix was true
telemetry_lock = threading.Lock()

# What we last sent vs. the last plain-text line the Pi sent back (ARM/STOP/
# CTRL acks). Exposed via /status so the dashboard can show both side by
# side and catch a dropped or corrupted packet on the (half-duplex) link.
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
    """Continuously read from the radio and cache the latest telemetry
    packet, so GET /telemetry can respond instantly instead of blocking on
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
    # threaded=True: without it Werkzeug's dev server handles one HTTP
    # request at a time, so the dashboard's concurrent telemetry/status/ctrl
    # polling queues up behind itself instead of actually running in
    # parallel - a real source of extra latency the CLI scripts never hit
    # since they talk to the serial port directly, with no HTTP layer at all.
    app.run(host="0.0.0.0", port=5001, threaded=True)