import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'
import Navbar from './components/Navbar';
import Card from './components/Card';
import HomeCards from './components/HomeCards';

function App() {
  const [mode, changeMode] = useState(true)

  return (
    <div className='app-container' data-theme={ mode ? "dark" : "light" }>
      <Navbar darkMode={ mode } setDarkMode={ changeMode }  />
      <HomeCards />
    </div>
  );
}

export default App
