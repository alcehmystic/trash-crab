import React from 'react';
import './Navbar.css';
import icon from '../assets/card_icons/Logo_Crab.png';
import settingsIcon from '../assets/card_icons/Setting_Logo.png';
import StatusButton from "./StatusButton";

function Navbar() {
  return (
    <nav className="navbar">
      <div className="navbar-left">
        <img src={icon} alt="Trash Crab Logo" className="navbar-icon" />
        <h2 className="navbar-logo">Trash Crab</h2>
        <StatusButton isOnline={false} />
      </div>
      <div className="navbar-right">
        <img src={settingsIcon} alt="Settings Icon" className="settings-icon" />
      </div>
    </nav>
  );
};

export default Navbar;