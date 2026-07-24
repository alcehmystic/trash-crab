import React, { useState } from 'react';
import './Navbar.css';
import icon from '../assets/card_icons/Logo_Crab.png';
import settingsIcon from '../assets/card_icons/Setting_Logo.png';
import StatusButton from "./StatusButton";

// Navigation bar at the top of the user interface displaying online status, main logo, and color preference
function Navbar({ darkMode, setDarkMode, isOnline}) {
  const [open, setOpen] = useState(false)
  const toggleSettings = () => {
    setOpen(!open)
  }

  return (
    <nav className="navbar">
      <div className="navbar-left">
        <img src={icon} alt="Trash Crab Logo" className="navbar-icon" />
        <h2 className="navbar-logo">Trash Crab</h2>
        <StatusButton isOnline={isOnline} />
      </div>

      <div className="navbar-right">
        <button className='settings-button' onClick={ toggleSettings }>
          <img src={ settingsIcon } alt="Settings Icon" className="settings-icon" />
        </button>

        {open && (
            <div className='settings-menu'>
              <label className='display-toggle'>
                <input
                  type='checkbox'
                  checked={ darkMode }
                  onChange={() => setDarkMode(prev => !prev)}
                />
                <span className='display-bar'></span>
                <span className='bar-label'>{darkMode ? "Dark Mode" : "Light Mode"}</span>
              </label>
            </div>
          )}
      </div>
      
    </nav>
  );
};

export default Navbar;