import React from 'react'
import './Card.css'

// Reusable react component that can display an icon, title, status, and other content with the same format
const Card = ({ icon, title, statusButton, children, className }) => {
  return (
    <div className={`card${className ? ` ${className}` : ''}`}>
        <div className='card-header'>
            <h2 className='card-title'>
                <img src={icon} alt="icon" /> {title}
                {statusButton && <div className="card-status">{statusButton}</div>}
            </h2>    
        </div>

        {children}
    </div>
  )
}

export default Card
