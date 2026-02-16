import React from 'react';
import './Navbar.css';

function Navbar() {
  return (
    <nav className="navbar">
      <div className="navbar-left">
        <h2 className="navbar-logo">Trash Crab</h2>
        <button className="navbar-toggle">Status</button>
      </div>
      <div className="navbar-right">
        <button className="navbar-toggle">Settings</button>
      </div>
    </nav>
  );
};

export default Navbar;