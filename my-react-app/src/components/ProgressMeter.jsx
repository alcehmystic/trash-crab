import React from 'react'

const ProgressMeter = ({ color = '#183A49', progress = 0, height = 20 }) => { // Passing in default vals

  const Parentdiv = {
    height: height,
    width: '100%',
    backgroundColor: '#FFF3F3',
    borderRadius: 40,
    border: '1px solid #183a49AA'
  };

  const Childdiv = {
    height: '100%',
    width: `${ progress }%`,
    backgroundColor: color,
    borderRadius: 40
  };

  const progressPercentage = {
    color: '#102833',
    fontWeight: 20,
    padding: '125px',
    fontSize: 11,
    verticalAlign: 'text-top'
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
