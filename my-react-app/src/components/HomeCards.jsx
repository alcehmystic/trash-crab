import Card from './Card'
import React from 'react'
import Joystick from '../assets/card_icons/Joystick.png';
import Videocall from '../assets/card_icons/Video_call.png';
import Live from '../assets/card_icons/Live.png';
import World from '../assets/card_icons/World_location.png';
import './HomeCards.css';


const HomeCards = () => {
  return (
    <div className='cards-container'>
        <Card title='Controls' icon={ Joystick } alt='Joystick Icon'>
            <div className='controls-buttons'>
                <div className='search-buttons'>
                    <button className='start-search'> Start Search </button>
                    <button className='stop-search'> Stop Search </button>
                </div>

                <button className='return-dock'> Return to Dock </button>

                <div className='info-widget'>
                    <span className='info-label'> Elapsed Time: </span>
                    <span className='info-value'> --:-- </span>
                </div>

                <div className='info-widget'>
                    <span className='info-label'> ETA Completion: </span>
                    <span className='info-value'> --:-- </span>
                </div>
            </div>

        </Card>
        <Card title='Live Feed' icon={ Videocall } alt='Videocall Icon'>
            <p>
            The body will be in here....and there and filling up this square
            </p>
        </Card>
        <Card title='Live Stats' icon={ Live } alt='Live Icon'>
            <p>
            The body will be in here....and there and filling up this square
            </p>
        </Card>
        <Card title='Map View' icon={ World } alt='World Icon'>
            <p>
            The body will be in here....and there and filling up this square
            </p>
        </Card>
    </div>
      

  )
}

export default HomeCards
