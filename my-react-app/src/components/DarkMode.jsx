import "./DarkMode.css";

// Creates a toggle letting users switch between dark mode & light mode
export const colorDisplay = ({ colorChange, isChange}) => {
    return (
        <div className='toggle-container'>
            <input
                onChange={ colorChange }
                checked={ isChange }
            />
        </div>
    )
}