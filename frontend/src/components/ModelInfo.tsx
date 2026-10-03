import { BarChart3, Database, Gauge, LucideIcon, Target } from 'lucide-react';
import { ModelInfoType } from '../api/client';

interface ModelInfoProps {
  info: ModelInfoType;
}

export default function ModelInfo({ info }: ModelInfoProps) {
  return (
    <div className="panel space-y-4">
      <div>
        <h2 className="text-lg font-semibold">Price model evaluation</h2>
        <p className="text-sm text-slate-600">{info.model_path}</p>
        {info.last_update && <p className="text-xs text-slate-500">Trained: {formatDate(info.last_update)}</p>}
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        <Metric icon={Database} label="Samples" value={String(info.training_sample_count ?? '-')} />
        <Metric icon={Gauge} label="R² (higher is better)" value={formatMetric(info.model_metrics?.r2)} />
        <Metric icon={BarChart3} label="MAE (VND)" value={formatMoney(info.model_metrics?.mae_vnd)} />
        <Metric icon={BarChart3} label="RMSE (VND)" value={formatMoney(info.model_metrics?.rmse_vnd)} />
        <Metric icon={Target} label="MdAPE (median)" value={formatPercent(info.model_metrics?.median_absolute_percentage_error)} />
      </div>
      <p className="text-xs text-slate-500">
        Lower MAE, RMSE and MdAPE mean smaller price errors. MdAPE is the median percentage error.
      </p>
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
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(3) : '-';
}

function formatMoney(value?: number) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value / 1_000_000).toFixed(0)}M` : '-';
}

function formatPercent(value?: number) {
  return typeof value === 'number' && Number.isFinite(value) ? `${(value * 100).toFixed(1)}%` : '-';
}

function formatDate(value: string) {
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}
