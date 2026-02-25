import './StatusButton.css';

function StatusButton({ isOnline }) {
  return (
    <div className={`status-btn ${isOnline ? "online" : "offline"}`}>
      <span className="status-dot" />
      <span className="status-text">
        {isOnline ? "Online" : "Offline"}
      </span>
    </div>
  );
}

export default StatusButton;