import { Building2, Github } from 'lucide-react';

export default function Header() {
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
        <a
          href="https://github.com/pnguyn183/real-estate-ml-system"
          className="inline-flex items-center gap-2 rounded border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50"
        >
          <Github className="h-4 w-4" />
          GitHub
        </a>
      </nav>
    </header>
  );
}
