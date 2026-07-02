import Card from './Card';
import React, { useEffect, useRef, useState } from 'react';
import Joystick from '../assets/card_icons/Joystick.png';
import Videocall from '../assets/card_icons/Video_call.png';
import Live from '../assets/card_icons/Live.png';
import World from '../assets/card_icons/World_location.png';
import Battery from '../assets/card_icons/Full_battery.png';
import Speed from '../assets/card_icons/Speed.png';
import Water from '../assets/card_icons/Water.png';
import Trash from '../assets/card_icons/Trash.png';
import Address from '../assets/card_icons/Address.png';
import StatusButton from './StatusButton.jsx';
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

import ProgressMeter from './ProgressMeter.jsx'
import './HomeCards.css';

import MapDisplay from './MapDisplay.jsx'

import { trashCrabData } from "../test/testData";

// How often the currently-held WASD keys are re-sent to the Pi as a
// heartbeat. Matches the interval laptop_controller.py used - comfortably
// inside the Pi's 1.0s failsafe timeout, without flooding the (effectively
// half-duplex) radio link the way a much faster interval would. Actual
// keypresses are sent immediately (see handleKeyDown/handleKeyUp below),
// not just on this heartbeat, so a fresh command doesn't wait up to a full
// interval before it's even transmitted.
const CTRL_SEND_INTERVAL_MS = 300;

// 127.0.0.1, not localhost: on this dev setup, resolving "localhost" was
// adding a large, inconsistent delay (measured ~300ms average, spiking past
// 800ms) from an IPv6-then-IPv4 fallback in the browser's connection setup.
// 127.0.0.1 skips that resolution entirely and measured a consistent ~100ms.
const TELECOM_BASE_URL = "http://127.0.0.1:5001";

// "Start Search" spins the boat in place by holding a turn-right command at
// reduced speed - SEARCH_SPIN_KEY matches the WASD key computeDrive/the Pi
// already treat as turn-right, so it reuses the exact same drive math, just
// scaled down via the CTRL protocol's optional speed parameter.
const SEARCH_SPIN_KEY = "d";
const SEARCH_SPEED_PCT = 50;

const parseHmsToSeconds = (hms) => {
    const parts = String(hms).split(":").map(Number);
    if (parts.length !== 3 || parts.some(Number.isNaN)) return 0;
    const [hours, minutes, seconds] = parts;
    return hours * 3600 + minutes * 60 + seconds;
};

// Progress is elapsed time as a percentage of (elapsed + ETA remaining),
// rather than a value the Pi sends directly - etaCompletion is a countdown
// of time remaining, not a fixed total, so this recomputes on every
// telemetry poll as that countdown changes.
const computeProgressPercent = (elapsedTime, etaCompletion) => {
    const elapsedSeconds = parseHmsToSeconds(elapsedTime);
    const etaSeconds = parseHmsToSeconds(etaCompletion);
    const totalSeconds = elapsedSeconds + etaSeconds;
    if (totalSeconds <= 0) return 0;
    return Math.round((elapsedSeconds / totalSeconds) * 100);
};

// Client-side estimate of differential-drive thruster mixing, purely to
// visualize intent in the dashboard - the Pi is the source of truth for
// what the motors actually do with a given CTRL packet.
const computeDrive = (heldKeys) => {
    const forward = (heldKeys.has("w") ? 1 : 0) - (heldKeys.has("s") ? 1 : 0);
    const turn = (heldKeys.has("d") ? 1 : 0) - (heldKeys.has("a") ? 1 : 0);
    const clamp = (n) => Math.max(-1, Math.min(1, n));

    const parts = [];
    if (heldKeys.has("w")) parts.push("Forward");
    if (heldKeys.has("s")) parts.push("Reverse");
    if (heldKeys.has("a")) parts.push("Left");
    if (heldKeys.has("d")) parts.push("Right");

    return {
        keys: Array.from(heldKeys).sort().join(""),
        label: parts.length ? parts.join(" + ") : "Neutral",
        leftPower: Math.round(clamp(forward + turn) * 100),
        rightPower: Math.round(clamp(forward - turn) * 100),
    };
};

// Same shape as computeDrive(), but scaled down to reflect the reduced-speed
// search spin actually being sent, so "Command Sent" shows what's really
// happening instead of the full-speed numbers a bare turn-right implies.
const computeSearchDrive = () => {
    const base = computeDrive(new Set([SEARCH_SPIN_KEY]));
    const scale = SEARCH_SPEED_PCT / 100;
    return {
        ...base,
        label: `Searching (${base.label})`,
        leftPower: Math.round(base.leftPower * scale),
        rightPower: Math.round(base.rightPower * scale),
    };
};

// linkStatus/now/isOnline are lifted up into App.jsx and passed down as
// props, since the Navbar's top-level status badge needs the exact same
// /status data as the Controls card - polling it separately in both places
// would double the request rate for no benefit.
const HomeCards = ({ linkStatus, now, isOnline, lastContactAt }) => {
    const [telemetry, setTelemetry] = useState({
        elapsedTime: "00:00:00",
        etaCompletion: "00:00:00",
        battery: 0,
        speed: 0,
        trashCollected: 0,
        waterTemperature: 0,
        gps: {
            latitude: 0,
            longitude: 0
        },
        progressMeter: 0,
        state: "NO DATA"
    });

    // What the dashboard is currently telling the Pi to do - the Pi's last
    // actual reply (linkStatus.lastAck) comes in as a prop, shown side by
    // side so a dropped/garbled packet on the radio link is visible instead
    // of silent.
    const [drive, setDrive] = useState(computeDrive(new Set()));

    // Whether "Start Search" is active: an autonomous turn-in-place spin
    // instead of manual WASD. Exclusive with tele-op - WASD is ignored while
    // this is true, and starting/stopping toggles which one the heartbeat
    // below actually sends.
    const [searchMode, setSearchMode] = useState(false);

    // Set once by "Emergency Stop" and never cleared - a full page reload is
    // the only way back. haltedRef is checked inside the WASD/heartbeat
    // effect and sendCtrl(): a plain ref (not state) is intentional here
    // since this only ever flips one-way and callbacks need to see it
    // immediately, without waiting on a re-render.
    const [halted, setHalted] = useState(false);
    const haltedRef = useRef(false);

    const getTelemetry = async () => {
        try{
            const response = await fetch(`${TELECOM_BASE_URL}/telemetry`)
            const data = await response.json();

            setTelemetry(data);
        }

        catch(error){
            console.error("Failed to get telemetry with error: ", error);
        }
    }

    // Sends a CTRL packet - either the currently-held WASD keys (tele-op) or
    // the fixed search-spin key (autonomous search), plus an optional speed
    // scale (0-100, defaults to full speed). Called on an interval (the
    // heartbeat) rather than once per keypress, so combos (W+A) work and the
    // Pi's failsafe watchdog stays satisfied the whole time a key is held,
    // not just at the moment it's pressed. No-ops after Emergency Stop -
    // nothing gets sent to the Pi again until the page is reloaded.
    const sendCtrl = async (keys, scale = 100) => {
        if (haltedRef.current) return;
        try {
            const response = await fetch(`${TELECOM_BASE_URL}/command/ctrl`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ keys, scale })
            });

            const data = await response.json();
            console.log("Ctrl Request:", data);
        }

        catch(error){
            console.error("Failed to send ctrl: ", error);
        }
    };

    // The only way to reach the Pi's real (Arduino-side instant-cut) STOP,
    // as opposed to the graceful "CTRL:" neutral tele-op/search already use.
    // Sent directly rather than through sendCtrl, since this is the one
    // command that must go out even though everything else is about to be
    // permanently blocked by haltedRef. There is no way back from this short
    // of reloading the page - by design, so a stuck key or leftover timer
    // can't quietly resume driving afterward.
    const emergencyStop = async () => {
        if (haltedRef.current) return;
        haltedRef.current = true;
        setHalted(true);
        setSearchMode(false);
        setDrive(computeDrive(new Set()));
        try {
            const response = await fetch(`${TELECOM_BASE_URL}/command/stop`, {
                method: "POST"
            });
            const data = await response.json();
            console.log("Emergency Stop:", data);
        } catch (error) {
            console.error("Failed to send emergency stop: ", error);
        }
    };

    // Enters the autonomous search spin: WASD is ignored (see the effect
    // below) and the heartbeat instead holds a fixed turn-right at
    // SEARCH_SPEED_PCT until "Stop Search" is pressed.
    const startSearch = () => {
        setSearchMode(true);
        setDrive(computeSearchDrive());
        sendCtrl(SEARCH_SPIN_KEY, SEARCH_SPEED_PCT);
    };

    // Leaves search mode and immediately sends an explicit neutral CTRL so
    // the boat stops spinning right away instead of waiting on the Pi's
    // failsafe - then tele-op (WASD) is live again.
    const stopSearch = () => {
        setSearchMode(false);
        setDrive(computeDrive(new Set()));
        sendCtrl("");
    };

    // Telemetry polling doesn't depend on searchMode, so it gets its own
    // effect rather than being torn down and recreated every time search
    // mode toggles.
    useEffect(() => {
        getTelemetry();
        const telemetryInterval = setInterval(() => {
            getTelemetry();
        }, 1000);
        return () => clearInterval(telemetryInterval);
    }, []);

    useEffect(() => {
        // Tracks which WASD keys are currently held down. A plain mutable
        // Set (not React state) is intentional here - key state changes
        // far more often than a re-render is needed for, the heartbeat
        // interval below is what actually reads it. Recreated fresh each
        // time searchMode toggles, which is exactly what we want - no stale
        // held keys carrying over between tele-op and search.
        const heldKeys = new Set();
        const driveKeys = ["w", "a", "s", "d"];

        // Pushes the current key state out right away, instead of waiting
        // for the next heartbeat tick to pick it up (which could be up to
        // CTRL_SEND_INTERVAL_MS late for a just-pressed/released key).
        const sendUpdate = () => {
            setDrive(computeDrive(heldKeys));
            sendCtrl(Array.from(heldKeys).sort().join(""));
        };

        // WASD is inert while searchMode is active - only "Stop Search"
        // hands control back, so a stray keypress can't fight the spin. Also
        // inert after Emergency Stop, permanently, until the page reloads.
        const handleKeyDown = (event) => {
            if (searchMode || haltedRef.current) return;
            const k = event.key.toLowerCase();
            // event.repeat guards against the OS's key-auto-repeat firing
            // keydown continuously while held - heldKeys.has(k) is the same
            // extra guard for browsers that don't set event.repeat.
            if (driveKeys.includes(k) && !heldKeys.has(k)) {
                heldKeys.add(k);
                sendUpdate();
            }
        };

        const handleKeyUp = (event) => {
            if (searchMode || haltedRef.current) return;
            const k = event.key.toLowerCase();
            if (driveKeys.includes(k) && heldKeys.has(k)) {
                heldKeys.delete(k);
                sendUpdate();
            }
        };

        // Without this, alt-tabbing (or anything else that steals focus)
        // away while a key is physically held down never fires keyup, so
        // that key gets stuck "on" and the Pi keeps driving indefinitely.
        const handleBlur = () => {
            if (searchMode || haltedRef.current) return;
            if (heldKeys.size > 0) {
                heldKeys.clear();
                sendUpdate();
            }
        };

        window.addEventListener("keydown", handleKeyDown);
        window.addEventListener("keyup", handleKeyUp);
        window.addEventListener("blur", handleBlur);

        // Heartbeat: while searching, holds the fixed search-spin command;
        // otherwise only re-sends while a WASD key is actually held. The
        // radio link is half-duplex and shared with the Pi's own
        // telemetry/ack traffic, so continuously re-sending "CTRL:" at tele-
        // op idle keeps the channel busy for no reason - the Pi's own
        // FAILSAFE_TIMEOUT (1.0s) already forces a stop on its own once
        // packets stop arriving, so there's nothing for an idle heartbeat to
        // protect against there. Skipping it noticeably shortens how long a
        // real command has to wait behind unrelated traffic.
        const ctrlInterval = setInterval(() => {
            if (haltedRef.current) return;
            if (searchMode) {
                setDrive(computeSearchDrive());
                sendCtrl(SEARCH_SPIN_KEY, SEARCH_SPEED_PCT);
            } else if (heldKeys.size > 0) {
                sendUpdate();
            }
        }, CTRL_SEND_INTERVAL_MS);

        return () => {
            clearInterval(ctrlInterval);
            window.removeEventListener("keydown", handleKeyDown);
            window.removeEventListener("keyup", handleKeyUp);
            window.removeEventListener("blur", handleBlur);
        };

    }, [searchMode]);

    // Seconds since the Pi last communicated at all - a command ack or a
    // telemetry packet, whichever is more recent (lastContactAt, computed
    // once in App.jsx so this and isOnline always agree) - ticking smoothly
    // via `now` rather than only jumping when a fresh /status poll lands.
    const secondsSinceLastReply = lastContactAt
        ? Math.max(0, now / 1000 - lastContactAt)
        : null;

    // The top navbar badge is a plain online/offline read on the whole bot.
    // This one instead shows the Controls card's own mode - searching vs.
    // tele-op - but falls back to "Offline" whenever the bot itself is
    // offline (a mode reading is meaningless with no link at all), and to
    // "Stopped" once Emergency Stop has been pressed, which overrides
    // everything else since it's permanent until reload.
    const controlsStatusVariant = halted ? "stopped" : (!isOnline ? "offline" : (searchMode ? "searching" : "teleop"));
    const controlsStatusLabel = halted ? "Stopped" : (!isOnline ? "Offline" : (searchMode ? "Searching" : "Tele-Op"));

  return (
    <div className='cards-container'>
        <Card title='Controls' icon={ Joystick } alt='Joystick Icon' statusButton={<StatusButton variant={controlsStatusVariant} label={controlsStatusLabel} />}>
            <div className='controls-widget'>
                <div className='search-buttons'>
                    <button className='start-search' onClick={startSearch} disabled={halted}> Start Search </button>
                    <button className='stop-search' onClick={stopSearch} disabled={halted}> Stop Search </button>
                </div>

                <button className='emergency-stop' onClick={emergencyStop} disabled={halted}> Emergency Stop </button>

                <div className='info-container'>
                    <span className='info-label'> Elapsed Time: </span>
                    <span className='info-value'> { telemetry.elapsedTime } </span>
                </div>

                <div className='info-container'>
                    <span className='info-label'> ETA Completion: </span>
                    <span className='info-value'> { telemetry.etaCompletion } </span>
                </div>

                <div className='info-container'>
                    <span className='info-label'> Command Sent: </span>
                    <span className='info-value'> { drive.label } (L { drive.leftPower }% / R { drive.rightPower }%) </span>
                </div>

                <div className='info-container'>
                    <span className='info-label'> Command Received: </span>
                    <span className='info-value'> { linkStatus.lastAck ?? "No response yet" } </span>
                </div>

                <div className='info-container'>
                    <span className='info-label'> Time Since Last Contact: </span>
                    <span className='info-value'> { secondsSinceLastReply === null ? "No contact yet" : `${secondsSinceLastReply.toFixed(1)}s ago` } </span>
                </div>
            </div>

        </Card>
        <Card title='Live Feed' icon={ Videocall } alt='Videocall Icon' statusButton={<StatusButton isOnline={false} />} >
            <p>
                This body will later be replaced with the Trash Crab's live feed from the camera to show what it's collecting and seeing in its view.
            </p>
        </Card>
        <Card title='Live Stats' icon={ Live } alt='Live Icon'>
            <div className='livestats-widget'>
                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Battery } alt='Battery Icon' /> Battery
                        </span>
                        <span className='info-value'> { telemetry.battery } % </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'>
                            <img src={ Speed } alt='Speed Icon' /> Speed 
                        </span>
                        <span className='info-value'> { telemetry.speed } m/s </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Trash } alt='Trash Icon' /> Trash Collected 
                        </span>
                        <span className='info-value'> { telemetry.trashCollected } items </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'>
                            <img src={ Water } alt='Water Icon' /> Water Tempuature
                        </span>
                        <span className='info-value'> { telemetry.waterTemperature } F </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Address } alt='GPS Icon' /> GPS
                        </span>
                        <span className='info-value'> ( { telemetry.gps.latitude } , { telemetry.gps.longitude } ) </span>
                    </div>

                    <div className='progress-container'>
                        <span className='info-label'> Progress Meter </span>
                        <div className='progress-bar'>
                            <ProgressMeter color='#183A49' progress={ computeProgressPercent(telemetry.elapsedTime, telemetry.etaCompletion) } />
                        </div>
                    </div>
                    
            </div>
        </Card>
        <Card title='Map View' icon={ World } alt='World Icon'>
            <div className='map-container'>
                <MapDisplay/>
            </div>
        </Card>
        
    </div>
      

  )
}

export default HomeCards