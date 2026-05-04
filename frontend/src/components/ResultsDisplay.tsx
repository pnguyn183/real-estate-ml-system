import { CheckCircle, Clock, LucideIcon, TrendingUp } from 'lucide-react';
import { PredictionResult } from '../api/client';

interface ResultsDisplayProps {
  result: PredictionResult;
}

export default function ResultsDisplay({ result }: ResultsDisplayProps) {
  return (
    <div className="panel space-y-5">
      <div className="flex items-center gap-3">
        <span className="grid h-10 w-10 place-items-center rounded bg-emerald-50 text-emerald-700">
          <CheckCircle className="h-5 w-5" />
        </span>
        <div>
          <p className="text-sm text-slate-500">Predicted Price</p>
          <h2 className="text-3xl font-bold">{result.predicted_price_billion_vnd.toFixed(3)}B VND</h2>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <Info icon={TrendingUp} label="Full value" value={`${Math.round(result.predicted_price_vnd).toLocaleString('en-US')} VND`} />
        <Info icon={Clock} label="Latency" value={`${result.latency_ms.toFixed(1)} ms`} />
      </div>
    </div>
  );
}

function Info({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="rounded border border-slate-200 bg-slate-50 p-4">
      <Icon className="mb-2 h-5 w-5 text-cyan-700" />
      <p className="text-xs uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-sm font-semibold">{value}</p>
    </div>
  );
}
