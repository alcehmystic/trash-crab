import React from 'react'
import './Card.css'

// children = whatever JSX is placed between <Card>..</Card>
const Card = ({ icon, title, children}) => {
  return (
    <div className = 'card'>
        <div className = 'card-header'>
            <h2 className = 'card-title'>
                <img src={ icon } /> { title }
            </h2>          
        </div>

       { children }
    </div>
  )
}

export default Card
