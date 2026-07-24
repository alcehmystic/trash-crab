import React from 'react'

/*
  Displays a horizontal progress bar.

  Values:
  - color : Color of the filled portion
  - progress : Percentage completed from 0 - 100
  - height : Height of the progress meter
*/

const ProgressMeter = ({ color = '#183A49', progress = 0, height = 20 }) => { // Passing in default vals

  // Style for the full progress bar
  const Parentdiv = {
    height: height,
    width: '100%',
    backgroundColor: '#FFF3F3',
    borderRadius: 40,
    border: '1px solid #183a49AA'
  };

  // Style for the filled portion of the progress bar
  const Childdiv = {
    height: '100%',
    width: `${ progress }%`,
    backgroundColor: color,
    borderRadius: 40
  };

  // Display for percentage value text
  const progressPercentage = {
    color: '#102833',
    fontWeight: 20,
    padding: '128px',
    fontSize: 15,
    verticalAlign: 'top'
  }

  return (
    <div style={ Parentdiv }>
        <div style={ Childdiv }>
            <span style={ progressPercentage }>{`${ progress }%`}</span>
        </div>
    </div>
  )
}

export default ProgressMeter
