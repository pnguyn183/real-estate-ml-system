import { useEffect, useState } from 'react';
import { Activity, Building2, Database, Gauge, Loader2 } from 'lucide-react';
import Header from './components/Header';
import ModelInfo from './components/ModelInfo';
import PredictionForm from './components/PredictionForm';
import ResultsDisplay from './components/ResultsDisplay';
import StatsCard from './components/StatsCard';
import { checkHealth, getModelInfo, HealthResponse, ModelInfoType, PredictionResult, PropertyFeatures, predictPrice } from './api/client';

export default function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [modelInfo, setModelInfo] = useState<ModelInfoType | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function loadStatus() {
      try {
        const healthResult = await checkHealth();
        setHealth(healthResult);
        if (healthResult.model_exists) {
          setModelInfo(await getModelInfo());
        }
      } catch {
        setHealth(null);
      }
    }
    void loadStatus();
  }, []);

  async function handlePredict(features: PropertyFeatures) {
    setLoading(true);
    setError(null);
    try {
      setPrediction(await predictPrice(features));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Prediction failed');
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-950">
      <Header />
      <main className="mx-auto grid max-w-7xl gap-6 px-4 py-6 lg:grid-cols-[1.05fr_0.95fr]">
        <section className="space-y-6">
          <div className="grid gap-4 sm:grid-cols-3">
            <StatsCard icon={Activity} label="API" value={health?.status || 'offline'} />
            <StatsCard icon={Database} label="Model" value={health?.model_exists ? 'available' : 'waiting'} />
            <StatsCard icon={Gauge} label="Latency" value={prediction ? `${prediction.latency_ms.toFixed(0)} ms` : '-'} />
          </div>
          <PredictionForm onSubmit={handlePredict} loading={loading} />
        </section>

        <section className="space-y-6">
          {loading && (
            <div className="panel flex items-center gap-3">
              <Loader2 className="h-5 w-5 animate-spin text-cyan-700" />
              <span className="text-sm font-medium">Calculating prediction...</span>
            </div>
          )}
          {error && <div className="panel border-red-200 bg-red-50 text-sm text-red-700">{error}</div>}
          {prediction ? (
            <ResultsDisplay result={prediction} />
          ) : (
            <div className="panel flex min-h-72 flex-col items-center justify-center text-center">
              <Building2 className="mb-4 h-12 w-12 text-cyan-700" />
              <h2 className="text-xl font-semibold">Real estate valuation workspace</h2>
              <p className="mt-2 max-w-md text-sm text-slate-600">
                Enter property features to estimate the listing price from the trained model.
              </p>
            </div>
          )}
          {modelInfo && <ModelInfo info={modelInfo} />}
        </section>
      </main>
    </div>
  );
}
