import { BarChart3, Database, Gauge, LucideIcon } from 'lucide-react';
import { ModelInfoType } from '../api/client';

interface ModelInfoProps {
  info: ModelInfoType;
}

export default function ModelInfo({ info }: ModelInfoProps) {
  return (
    <div className="panel space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Model Info</h2>
        <p className="text-sm text-slate-600">{info.model_path}</p>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric icon={Database} label="Samples" value={String(info.training_sample_count ?? '-')} />
        <Metric icon={Gauge} label="R2" value={formatMetric(info.model_metrics?.r2)} />
        <Metric icon={BarChart3} label="MAE" value={formatMoney(info.model_metrics?.mae_vnd)} />
      </div>
    </div>
  );
}

function Metric({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 p-3">
      <Icon className="mb-2 h-4 w-4 text-cyan-700" />
      <p className="text-xs text-slate-500">{label}</p>
      <p className="font-semibold">{value}</p>
    </div>
  );
}

function formatMetric(value?: number) {
  return typeof value === 'number' ? value.toFixed(3) : '-';
}

function formatMoney(value?: number) {
  return typeof value === 'number' ? `${(value / 1_000_000).toFixed(0)}M` : '-';
}
