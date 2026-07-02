import './StatusButton.css';

// variant/label let callers show more than a plain online/offline badge
// (e.g. the Controls card's searching/tele-op indicator) while isOnline
// alone still works for the simple cases (Navbar, Live Feed).
function StatusButton({ isOnline, variant, label }) {
  const resolvedVariant = variant ?? (isOnline ? "online" : "offline");
  const resolvedLabel = label ?? (isOnline ? "Online" : "Offline");

  return (
    <div className={`status-btn ${resolvedVariant}`}>
      <span className="status-dot" />
      <span className="status-text">
        {resolvedLabel}
      </span>
    </div>
  );
}

export default StatusButton;