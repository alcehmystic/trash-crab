import "./DarkMode.css";

export const colorDisplay = ({ colorChange, isChange}) => {
    return (
        <div className='toggle-container'>
            <input
                onChange={ colorChange }
                checked={ isChecked }
            />
        </div>
    )
}