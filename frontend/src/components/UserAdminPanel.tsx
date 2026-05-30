import { useEffect, useState } from 'react';
import { ShieldCheck, UserCog } from 'lucide-react';
import { AuthUser, handleApiError, listUsers, updateUserRole, updateUserStatus, UserRole } from '../api/client';

interface UserAdminPanelProps {
  currentUser: AuthUser;
}

const ROLES: UserRole[] = ['admin', 'manager', 'user'];

export default function UserAdminPanel({ currentUser }: UserAdminPanelProps) {
  const [users, setUsers] = useState<AuthUser[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function refreshUsers() {
    setLoading(true);
    setError(null);
    try {
      setUsers(await listUsers());
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void refreshUsers();
  }, []);

  async function changeRole(userId: string, role: UserRole) {
    setError(null);
    try {
      const updated = await updateUserRole(userId, role);
      setUsers((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      setError(handleApiError(err));
    }
  }

  async function changeStatus(userId: string, isActive: boolean) {
    setError(null);
    try {
      const updated = await updateUserStatus(userId, isActive);
      setUsers((items) => items.map((item) => (item.id === updated.id ? updated : item)));
    } catch (err) {
      setError(handleApiError(err));
    }
  }

  return (
    <div className="panel space-y-4">
      <div className="flex items-center gap-3">
        <span className="grid h-9 w-9 place-items-center rounded bg-emerald-50 text-emerald-700">
          <ShieldCheck className="h-5 w-5" />
        </span>
        <div>
          <h2 className="text-lg font-semibold">Access Control</h2>
          <p className="text-sm text-slate-600">Admin-only user and role management.</p>
        </div>
      </div>

      {error && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}
      {loading ? (
        <div className="text-sm text-slate-600">Loading users...</div>
      ) : (
        <div className="space-y-3">
          {users.map((user) => (
            <div key={user.id} className="rounded border border-slate-200 p-3">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <UserCog className="h-4 w-4 shrink-0 text-cyan-700" />
                    <p className="truncate font-medium">{user.full_name}</p>
                  </div>
                  <p className="mt-1 truncate text-sm text-slate-600">{user.email}</p>
                </div>
                <span className={`rounded px-2 py-1 text-xs font-semibold ${user.is_active ? 'bg-emerald-50 text-emerald-700' : 'bg-slate-100 text-slate-500'}`}>
                  {user.is_active ? 'active' : 'disabled'}
                </span>
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_auto]">
                <select
                  className="input"
                  value={user.role}
                  disabled={user.id === currentUser.id}
                  onChange={(event) => void changeRole(user.id, event.target.value as UserRole)}
                >
                  {ROLES.map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  disabled={user.id === currentUser.id}
                  className="rounded border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                  onClick={() => void changeStatus(user.id, !user.is_active)}
                >
                  {user.is_active ? 'Disable' : 'Enable'}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
