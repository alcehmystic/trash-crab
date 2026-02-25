import React from 'react';
import './Navbar.css';
import icon from '../assets/card_icons/Logo_Crab.png';
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
        <button className="navbar-toggle">Settings</button>
      </div>
    </nav>
  );
};

export default Navbar;