import { useState } from 'react'
import reactLogo from './assets/react.svg'
import viteLogo from '/vite.svg'
import './App.css'

function App() {
  return (
    <div style={{ padding: "2rem", fontFamily: "Arial" }}>
      <h1>🎉 React is working!</h1>
      <p>If you can see this and it updates when you save, you’re good.</p>

      <button
        onClick={() => alert("Button clicked!")}
        style={{
          padding: "10px 16px",
          backgroundColor: "#4f46e5",
          color: "white",
          border: "none",
          borderRadius: "8px",
          cursor: "pointer"
        }}
      >
        Click me
      </button>
    </div>
  );
}

export default App
