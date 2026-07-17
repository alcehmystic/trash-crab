import React from 'react'
import './Card.css'


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
