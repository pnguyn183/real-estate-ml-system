import { LucideIcon } from 'lucide-react';

interface StatsCardProps {
  icon: LucideIcon;
  label: string;
  value: string;
}

export default function StatsCard({ icon: Icon, label, value }: StatsCardProps) {
  return (
    <div className="panel">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded bg-cyan-50 text-cyan-700">
          <Icon className="h-5 w-5" />
        </span>
        <div>
          <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
          <p className="text-base font-semibold">{value}</p>
        </div>
      </div>
    </div>
  );
}
