import { Building2, Github, LogOut, Shield } from 'lucide-react';
import { AuthUser } from '../api/client';

interface HeaderProps {
  user?: AuthUser | null;
  onLogout?: () => void;
}

export default function Header({ user, onLogout }: HeaderProps) {
  return (
    <header className="border-b border-slate-200 bg-white">
      <nav className="mx-auto flex max-w-7xl items-center justify-between px-4 py-4">
        <div className="flex items-center gap-3">
          <span className="grid h-10 w-10 place-items-center rounded bg-cyan-700 text-white">
            <Building2 className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-lg font-semibold">Real Estate ML System</h1>
            <p className="text-xs text-slate-500">Vietnam property price prediction</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {user && (
            <div className="hidden items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm text-slate-700 sm:inline-flex">
              <Shield className="h-4 w-4 text-cyan-700" />
              <span className="font-medium">{user.role}</span>
            </div>
          )}
          <a
            href="https://github.com/pnguyn183/real-estate-ml-system"
            className="inline-flex items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
          >
            <Github className="h-4 w-4" />
            GitHub
          </a>
          {user && onLogout && (
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
              onClick={onLogout}
            >
              <LogOut className="h-4 w-4" />
              Sign out
            </button>
          )}
        </div>
      </nav>
    </header>
  );
}
