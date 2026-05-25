import { CheckCircle, Clock, Gauge, Lightbulb, LucideIcon, Ruler, TrendingUp } from 'lucide-react';
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
        <Info icon={Ruler} label="Price / m2" value={formatMoney(result.price_per_m2_vnd)} />
        <Info icon={Gauge} label="Confidence" value={formatPercent(result.confidence_score)} />
        <Info icon={Clock} label="Latency" value={`${result.latency_ms.toFixed(1)} ms`} />
      </div>
      {typeof result.confidence_low_vnd === 'number' && typeof result.confidence_high_vnd === 'number' && (
        <div className="rounded border border-emerald-200 bg-emerald-50 p-4">
          <p className="text-xs uppercase tracking-wide text-emerald-700">AI range</p>
          <p className="mt-1 text-sm font-semibold text-emerald-950">
            {formatBillion(result.confidence_low_vnd)} - {formatBillion(result.confidence_high_vnd)}
          </p>
        </div>
      )}
      {!!result.explanations?.length && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-800">
            <Lightbulb className="h-4 w-4 text-cyan-700" />
            Model signals
          </div>
          <div className="space-y-2">
            {result.explanations.map((item) => (
              <p key={item} className="rounded border border-slate-200 bg-white px-3 py-2 text-sm text-slate-700">
                {item}
              </p>
            ))}
          </div>
        </div>
      )}
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

function formatMoney(value?: number) {
  return typeof value === 'number' ? `${Math.round(value).toLocaleString('en-US')} VND` : '-';
}

function formatBillion(value: number) {
  return `${(value / 1_000_000_000).toFixed(3)}B VND`;
}

function formatPercent(value?: number) {
  return typeof value === 'number' ? `${Math.round(value * 100)}%` : '-';
}
