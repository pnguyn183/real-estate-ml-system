import { FormEvent, useState } from 'react';
import { LockKeyhole, LogIn, UserPlus } from 'lucide-react';
import { AuthResponse, handleApiError, loginAccount, registerAccount } from '../api/client';

interface AuthPanelProps {
  onAuthenticated: (auth: AuthResponse) => void;
}

type AuthMode = 'login' | 'register';

export default function AuthPanel({ onAuthenticated }: AuthPanelProps) {
  const [mode, setMode] = useState<AuthMode>('login');
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const auth =
        mode === 'register'
          ? await registerAccount({ email, password, full_name: fullName })
          : await loginAccount({ email, password });
      onAuthenticated(auth);
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="mx-auto grid min-h-[calc(100vh-73px)] max-w-5xl place-items-center px-4 py-8">
      <form onSubmit={submit} className="panel w-full max-w-md space-y-5">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded bg-cyan-700 text-white">
            <LockKeyhole className="h-5 w-5" />
          </span>
          <div>
            <h2 className="text-xl font-semibold">{mode === 'register' ? 'Create account' : 'Sign in'}</h2>
            <p className="text-sm text-slate-600">Access is protected by role-based permissions.</p>
          </div>
        </div>

        <div className="grid grid-cols-2 rounded border border-slate-200 bg-slate-50 p-1">
          <button
            type="button"
            className={`rounded px-3 py-2 text-sm font-medium ${mode === 'login' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-600'}`}
            onClick={() => setMode('login')}
          >
            Sign in
          </button>
          <button
            type="button"
            className={`rounded px-3 py-2 text-sm font-medium ${mode === 'register' ? 'bg-white text-slate-950 shadow-sm' : 'text-slate-600'}`}
            onClick={() => setMode('register')}
          >
            Register
          </button>
        </div>

        {mode === 'register' && (
          <label className="block">
            <span className="label">Full name</span>
            <input className="input" value={fullName} onChange={(event) => setFullName(event.target.value)} autoComplete="name" />
          </label>
        )}

        <label className="block">
          <span className="label">Email</span>
          <input className="input" type="email" value={email} onChange={(event) => setEmail(event.target.value)} autoComplete="email" required />
        </label>

        <label className="block">
          <span className="label">Password</span>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={mode === 'register' ? 'new-password' : 'current-password'}
            minLength={8}
            required
          />
        </label>

        {error && <div className="rounded border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">{error}</div>}

        <button
          type="submit"
          disabled={loading}
          className="inline-flex w-full items-center justify-center gap-2 rounded bg-cyan-700 px-4 py-3 font-semibold text-white hover:bg-cyan-800 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {mode === 'register' ? <UserPlus className="h-5 w-5" /> : <LogIn className="h-5 w-5" />}
          {loading ? 'Please wait...' : mode === 'register' ? 'Create Account' : 'Sign In'}
        </button>
      </form>
    </section>
  );
}
