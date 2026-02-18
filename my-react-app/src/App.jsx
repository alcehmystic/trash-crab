import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'
import Navbar from './components/Navbar';
import Card from './components/Card';
import HomeCards from './components/HomeCards';

function App() {
  return (
    <div>
      <Navbar />
      <HomeCards />
    </div>
  );
}

export default App
