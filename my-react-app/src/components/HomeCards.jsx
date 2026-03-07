import Card from './Card'
import React from 'react'
import Joystick from '../assets/card_icons/Joystick.png';
import Videocall from '../assets/card_icons/Video_call.png';
import Live from '../assets/card_icons/Live.png';
import World from '../assets/card_icons/World_location.png';
import Battery from '../assets/card_icons/Full_battery.png';
import Speed from '../assets/card_icons/Speed.png';
import Water from '../assets/card_icons/Water.png';
import Trash from '../assets/card_icons/Trash.png';
import Address from '../assets/card_icons/Address.png';
import StatusButton from './StatusButton.jsx';

import ProgressMeter from './ProgressMeter.jsx'
import './HomeCards.css';



const HomeCards = () => {
  return (
    <div className='cards-container'>
        <Card title='Controls' icon={ Joystick } alt='Joystick Icon' statusButton={<StatusButton isOnline={false} />}>
            <div className='controls-widget'>
                <div className='search-buttons'>
                    <button className='start-search'> Start Search </button>
                    <button className='stop-search'> Stop Search </button>
                </div>

                <button className='return-dock'> Return to Dock </button>

                <div className='info-container'>
                    <span className='info-label'> Elapsed Time: </span>
                    <span className='info-value'> --:-- </span>
                </div>

                <div className='info-container'>
                    <span className='info-label'> ETA Completion: </span>
                    <span className='info-value'> --:-- </span>
                </div>
            </div>

        </Card>
        <Card title='Live Feed' icon={ Videocall } alt='Videocall Icon' statusButton={<StatusButton isOnline={false} />} >
            <p>
            The body will be in here....and there and filling up this square.jhfjadhfjdhfljsdfhjsdfh.
            </p>
        </Card>
        <Card title='Live Stats' icon={ Live } alt='Live Icon'>
            <div className='livestats-widget'>
                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Battery } alt='Battery Icon' /> Battery
                        </span>
                        <span className='info-value'> 99% </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'>
                            <img src={ Speed } alt='Speed Icon' /> Speed 
                        </span>
                        <span className='info-value'> 0.1 m/s </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Trash } alt='Trash Icon' /> Trash Collected 
                        </span>
                        <span className='info-value'> 2 items </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Water } alt='Water Icon' /> Water Tempuature
                        </span>
                        <span className='info-value'> 29 F </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Address } alt='GPS Icon' /> GPS
                        </span>
                        <span className='info-value'> 1294, 1493 </span>
                    </div>

                    <div className='progress-container'>
                        <span className='info-label'> Progress Meter</span>
                        <div className='progress-bar'>
                            <ProgressMeter color='#183A49' progress={55} />  
                        </div> 
                       
                    </div>
                    

            </div>
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
