import { useEffect, useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'
import Navbar from './components/Navbar';
import Card from './components/Card';
import HomeCards from './components/HomeCards';

// How often to poll for the Pi's last reply. Shared by both the Navbar's
// top-level status badge and the Controls card, which is why this state
// lives here instead of inside HomeCards.
const STATUS_POLL_INTERVAL_MS = 100;

// Contact (an ack OR a telemetry packet) received more recently than this
// counts as "online". Deliberately based on contact FROM the Pi, not on
// whether we last SENT a command - telemetry keeps arriving once a second
// on its own, so basing this on outgoing commands would flip to "offline"
// the moment WASD is released even though the Pi is still very much there.
const ONLINE_WINDOW_MS = 2000;

function App() {
  const [mode, changeMode] = useState(true)

  const [linkStatus, setLinkStatus] = useState({
    lastCommandSent: null,
    lastCommandAt: null,
    lastAck: null,
    lastAckAt: null,
    lastTelemetryAt: null,
    connected: false
  });
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const getStatus = async () => {
      try {
        // 127.0.0.1, not localhost: on this dev setup, resolving "localhost"
        // was adding a large, inconsistent delay (measured ~300ms average,
        // spiking past 800ms) from an IPv6-then-IPv4 fallback in the
        // browser's connection setup. 127.0.0.1 skips that resolution
        // entirely and measured a consistent ~100ms.
        const response = await fetch("http://127.0.0.1:5001/status");
        const data = await response.json();
        setLinkStatus(data);
      } catch (error) {
        console.error("Failed to get status with error: ", error);
      }
    };

    getStatus();
    const statusInterval = setInterval(() => {
      getStatus();
      setNow(Date.now());
    }, STATUS_POLL_INTERVAL_MS);

    return () => clearInterval(statusInterval);
  }, []);

  // Whichever is more recent - a command ack or a telemetry packet - is the
  // last time the Pi actually communicated back. Computed once here (rather
  // than separately in HomeCards) since both isOnline and the dashboard's
  // "Time Since Last Contact" display need to agree on the same value.
  const lastContactAt = Math.max(
    linkStatus.lastAckAt ?? 0,
    linkStatus.lastTelemetryAt ?? 0
  ) || null;

  const isOnline = lastContactAt !== null
    && (now - lastContactAt * 1000) < ONLINE_WINDOW_MS;

  return (
    <div className='app-container' data-theme={ mode ? "dark" : "light" }>
      <Navbar darkMode={ mode } setDarkMode={ changeMode } isOnline={ isOnline } />
      <HomeCards linkStatus={ linkStatus } now={ now } isOnline={ isOnline } lastContactAt={ lastContactAt } />
    </div>
  );
}

export default App
