import Card from './Card';
import React, { useEffect, useState } from 'react';
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
import Map from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';

import ProgressMeter from './ProgressMeter.jsx'
import './HomeCards.css';

import MapDisplay from './MapDisplay.jsx'

import { trashCrabData } from "../test/testData";

const HomeCards = () => {
    const [telemetry, setTelemetry] = useState({
        elapsedTime: "00:00:00",
        etaCompletion: "00:00:00",
        battery: 0,
        speed: 0,
        trashCollected: 0,
        waterTemperature: 0,
        gps: {
            latitude: 0,
            longitude: 0
        },
        progressMeter: 0,
        state: "NO DATA"
    });

    const getTelemetry = async () => {
        try{
            const response = await fetch("http://localhost:5001/telemetry")
            const data = await response.json();

            setTelemetry(data);
        }

        catch(error){
            console.error("Failed to get telemetry with error: ", error);
        }
    }
    
    // Function sends request to backend -> telecom -> Pi
    const sendCommand = async (command) => {
        try{
            const response = await fetch(`http://localhost:5001/command/${command}`, {
                method: "POST"
            });
        
            const data = await response.json();
            console.log("Command Request:", data);
        }
        
        catch(error){
            console.error("Failed to send command: ", error);
        }
               
    }; 

    useEffect(() => {
        getTelemetry();

        // Start continuation of fetching telemetry every second
        const interval = setInterval(() => {
            getTelemetry();
        }, 1000);
    
        const handleDirectionChange = (event) => {
            switch (event.key) {
                case "w":
                    sendCommand("move-forward");
                    break;
                case "s":
                    sendCommand("move-backward");
                    break;
                case "a":
                    sendCommand("turn-left");
                    break;
                case "d":
                    sendCommand("turn-right");
                    break;
                default:
                    break;
            }
        };
        
        window.addEventListener("keydown", handleDirectionChange);

        return () => {
            clearInterval(interval); // Turn off continuation of telemetry
            window.removeEventListener("keydown", handleDirectionChange);
        };

    }, []);
    
  return (
    <div className='cards-container'>
        <Card title='Controls' icon={ Joystick } alt='Joystick Icon' statusButton={<StatusButton isOnline={false} />}>
            <div className='controls-widget'>
                <div className='search-buttons'>
                    <button className='start-search' onClick={() => sendCommand("start")}> Start Search </button>
                    <button className='stop-search' onClick={() => sendCommand("stop")}> Stop Search </button>
                </div>

                <button className='return-dock' onClick={() => sendCommand("return-dock")}> Return to Dock </button>

                <div className='info-container'>
                    <span className='info-label'> Elapsed Time: </span>
                    <span className='info-value'> { telemetry.elapsedTime } </span>
                </div>

                <div className='info-container'>
                    <span className='info-label'> ETA Completion: </span>
                    <span className='info-value'> { telemetry.etaCompletion } </span>
                </div>
            </div>

        </Card>
        <Card title='Live Feed' icon={ Videocall } alt='Videocall Icon' statusButton={<StatusButton isOnline={false} />} >
            <p>
                This body will later be replaced with the Trash Crab's live feed from the camera to show what it's collecting and seeing in its view.
            </p>
        </Card>
        <Card title='Live Stats' icon={ Live } alt='Live Icon'>
            <div className='livestats-widget'>
                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Battery } alt='Battery Icon' /> Battery
                        </span>
                        <span className='info-value'> { telemetry.battery } % </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'>
                            <img src={ Speed } alt='Speed Icon' /> Speed 
                        </span>
                        <span className='info-value'> { telemetry.speed } m/s </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Trash } alt='Trash Icon' /> Trash Collected 
                        </span>
                        <span className='info-value'> { telemetry.trashCollected } items </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'>
                            <img src={ Water } alt='Water Icon' /> Water Tempuature
                        </span>
                        <span className='info-value'> { telemetry.waterTemperature } F </span>
                    </div>

                    <div className='info-container'>
                        <span className='info-label'> 
                            <img src={ Address } alt='GPS Icon' /> GPS
                        </span>
                        <span className='info-value'> ( { telemetry.gps.latitude } , { telemetry.gps.longitude } ) </span>
                    </div>

                    <div className='progress-container'>
                        <span className='info-label'> Progress Meter </span>
                        <div className='progress-bar'>
                            <ProgressMeter color='#183A49' progress={ telemetry.progressMeter } />  
                        </div>    
                    </div>
                    
            </div>
        </Card>
        <Card title='Map View' icon={ World } alt='World Icon'>
            <div className='map-container'>
                <MapDisplay/>
            </div>
        </Card>
        
    </div>
      

  )
}

export default HomeCards
