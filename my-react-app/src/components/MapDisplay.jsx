import { useEffect, useRef, useState } from 'react'
import Map, { Marker } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import './MapDisplay.css'

// Placeholder center used only until the very first GPS fix arrives - after
// that the map snaps to the boat's real position.
const DEFAULT_CENTER = { longitude: -81.2, latitude: 28.7 };

export default function MapDisplay({ position, hasFix, course, courseValid })
{
    // Map's current view
    const [viewState, setViewState] = useState({
        ...DEFAULT_CENTER,
        zoom: 16
    });

    // Whether the map should keep re-centering on every new fix. Turned off
    // the moment the user manually drags/scrolls the map, so we don't fight
    // their navigation - "Recenter" brings it back to following the boat.
    const [following, setFollowing] = useState(true);

    // True once we've snapped the view to a real fix at least once, so the
    // placeholder DEFAULT_CENTER only ever shows before the first fix.
    const hasCenteredOnce = useRef(false);

    useEffect(() => {
        if (!hasFix || !position) return;

        if (!hasCenteredOnce.current) {
            hasCenteredOnce.current = true;
            setViewState((vs) => ({ ...vs, longitude: position.longitude, latitude: position.latitude }));
            return;
        }

        if (following) {
            setViewState((vs) => ({ ...vs, longitude: position.longitude, latitude: position.latitude }));
        }
    }, [position?.latitude, position?.longitude, hasFix, following]);

    // Falls back to DEFAULT_CENTER when there's no fix yet, so the button
    // always visibly does something instead of appearing broken while
    // waiting on the first fix.
    const recenter = () => {
        const target = position ?? DEFAULT_CENTER;
        setViewState((vs) => ({ ...vs, longitude: target.longitude, latitude: target.latitude }));
        setFollowing(true);
    };

    return(
        <div className='map-wrapper'>
            <Map
                {...viewState} // Easy way of setting long. and lat.
                onMove = {(evt) => setViewState(evt.viewState)} // Update map when it moves
                onDragStart = {() => setFollowing(false)}
                // onZoomStart (not onWheel) so this fires for every kind of
                // zoom interaction - scroll wheel, pinch, +/- buttons,
                // double-click - not just wheel events. Without this, the
                // once-a-second GPS recenter effect below kept re-snapping
                // the view back onto the boat mid-zoom, fighting the map's
                // own zoom-to-cursor panning and making the marker look like
                // it wasn't tracking correctly while zooming.
                onZoomStart = {() => setFollowing(false)}
                mapStyle = 'https://tiles.openfreemap.org/styles/bright'
            >
                {position && (
                    <Marker
                        longitude={ position.longitude }
                        latitude={ position.latitude }
                        // 'bottom' so the label stacked above the icon (see
                        // gps-marker-wrapper below) only adds height upward -
                        // with 'center' the label would push the icon itself
                        // down away from the true coordinate.
                        anchor='bottom'
                        // course is GPS course-over-ground: a bearing derived
                        // from movement between fixes, not a compass reading.
                        // The receiver can't report one until it detects real
                        // movement, so until courseValid flips true, rotation
                        // stays at 0 rather than pointing the icon "north" as
                        // if that were real heading data.
                        rotation={ courseValid ? (course ?? 0) : 0 }
                        rotationAlignment='map'
                    >
                        <div className='gps-marker-wrapper'>
                            {/* rotation above spins this whole wrapper with
                                the boat's heading - counter-rotate just the
                                label so "Trash Crab" always reads upright
                                instead of spinning with the icon. */}
                            <span
                                className='gps-marker-label'
                                style={{ transform: courseValid ? `rotate(${-(course ?? 0)}deg)` : 'none' }}
                            >
                                Trash Crab
                            </span>
                            {courseValid ? (
                                <div className='gps-marker-icon'>
                                    {/* Thin forward-direction arrow, stacked
                                        right above the triangle's tip. It
                                        rotates with the rest of this wrapper
                                        (no counter-rotation, unlike the label
                                        above), so it always points the same
                                        way the triangle does. */}
                                    <svg
                                        className={`gps-marker-arrow${hasFix ? '' : ' gps-marker-arrow-stale'}`}
                                        viewBox='0 0 10 16'
                                        width='10'
                                        height='16'
                                        aria-hidden='true'
                                    >
                                        <line x1='5' y1='15' x2='5' y2='3' strokeWidth='1.5' strokeLinecap='round' />
                                        <path d='M1.5 6.5 L5 1 L8.5 6.5' fill='none' strokeWidth='1.5' strokeLinecap='round' strokeLinejoin='round' />
                                    </svg>
                                    <div
                                        className={`gps-marker${hasFix ? '' : ' gps-marker-stale'}`}
                                        title={hasFix ? `Heading ${Math.round(course ?? 0)}°` : 'Last known position (no current fix)'}
                                    />
                                </div>
                            ) : (
                                // No course-over-ground reading yet (boat
                                // hasn't moved enough since GPS lock) - a
                                // plain dot avoids implying a heading that
                                // doesn't exist. Switches to the directional
                                // icon above automatically once it moves.
                                <div
                                    className={`gps-marker-dot${hasFix ? '' : ' gps-marker-dot-stale'}`}
                                    title='Waiting for the boat to move before a heading can be determined'
                                />
                            )}
                        </div>
                    </Marker>
                )}
            </Map>

            {!following && (
                <button type='button' className='recenter-btn' onClick={recenter}>
                    Recenter
                </button>
            )}
        </div>
    )
}
