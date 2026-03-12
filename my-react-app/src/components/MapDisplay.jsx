import { useState } from 'react' // Import useState
import Map, { Marker } from 'react-map-gl/maplibre'; // Two react comp.
import 'maplibre-gl/dist/maplibre-gl.css';
import './MapDisplay.css'

export default function MapDisplay()
{
    // Map's current view
    const[viewState, setViewState] = useState({
        longitude: -81.2, // Left/right
        latitude: 28.7, // Up/down
        zoom: 14
    });

    // Bot's location
    const [botPosition] = useState({
        longitude: -81.2,
        latitude: 28.7
    });

    return(
        <Map
            {...viewState} // Easy way of setting long. and lat.
            onMove = {(evt) => setViewState(evt.viewState)} // Update map when it moves
            mapStyle = 'https://tiles.openfreemap.org/styles/bright'
        >
            <Marker
                longitude={ botPosition.longitude }
                latitude={ botPosition.latitude }
                anchor='bottom'
            />

        </Map>
    )
}
