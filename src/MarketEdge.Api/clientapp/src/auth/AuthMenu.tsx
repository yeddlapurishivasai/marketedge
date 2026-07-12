import { LogOut } from 'lucide-react';
import { useAuth } from './AuthProvider';

/** Shows the signed-in user and a sign-out button. Renders nothing when auth is disabled. */
export default function AuthMenu() {
  const { enabled, name, username, signOut } = useAuth();
  if (!enabled) return null;

  const label = name ?? username ?? 'Account';
  return (
    <div className="auth-menu" title={username ?? undefined}>
      <span className="auth-user">{label}</span>
      <button className="btn btn-ghost" onClick={signOut} title="Sign out">
        <LogOut size={18} />
      </button>
    </div>
  );
}
