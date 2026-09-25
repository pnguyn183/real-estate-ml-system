import { useEffect, useState } from 'react';
import { Activity, Building2, Database, Gauge, Loader2 } from 'lucide-react';
import AuthPanel from './components/AuthPanel';
import Header from './components/Header';
import ModelInfo from './components/ModelInfo';
import PredictionForm from './components/PredictionForm';
import ResultsDisplay from './components/ResultsDisplay';
import StatsCard from './components/StatsCard';
import UserAdminPanel from './components/UserAdminPanel';
import {
  AuthResponse,
  AuthUser,
  checkHealth,
  getCurrentUser,
  getModelInfo,
  handleApiError,
  HealthResponse,
  ModelInfoType,
  PredictionResult,
  PropertyFeatures,
  predictPrice,
  setAuthToken,
} from './api/client';

const MODEL_REFRESH_INTERVAL_MS = 30_000;

export default function App() {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [checkingSession, setCheckingSession] = useState(true);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [modelInfo, setModelInfo] = useState<ModelInfoType | null>(null);
  const [prediction, setPrediction] = useState<PredictionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canViewModel = user?.role === 'manager' || user?.role === 'admin';

  useEffect(() => {
    async function loadSession() {
      try {
        setUser(await getCurrentUser());
      } catch {
        setAuthToken(null);
        setUser(null);
      } finally {
        setCheckingSession(false);
      }
    }
    void loadSession();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let refreshTimer: ReturnType<typeof setTimeout> | undefined;

    async function refreshModelStatus() {
      try {
        const healthResult = await checkHealth();
        if (cancelled) return;
        setHealth(healthResult);
        if (canViewModel && healthResult.model_exists) {
          try {
            const info = await getModelInfo();
            if (!cancelled) setModelInfo(info);
          } catch {
            if (!cancelled) setModelInfo(null);
          }
        } else {
          setModelInfo(null);
        }
      } catch {
        if (!cancelled) {
          setHealth(null);
          setModelInfo(null);
        }
      } finally {
        // Schedule after completion so a slow request cannot overlap the next one.
        if (!cancelled) {
          refreshTimer = setTimeout(() => void refreshModelStatus(), MODEL_REFRESH_INTERVAL_MS);
        }
      }
    }

    void refreshModelStatus();
    return () => {
      cancelled = true;
      clearTimeout(refreshTimer);
    };
  }, [canViewModel, user?.id]);

  function handleAuthenticated(auth: AuthResponse) {
    setUser(auth.user);
  }

  function handleLogout() {
    setAuthToken(null);
    setUser(null);
    setPrediction(null);
    setModelInfo(null);
  }

  async function handlePredict(features: PropertyFeatures) {
    setLoading(true);
    setError(null);
    try {
      setPrediction(await predictPrice(features));
    } catch (err) {
      setError(handleApiError(err));
    } finally {
      setLoading(false);
    }
  }

  if (checkingSession) {
    return (
      <div className="min-h-screen bg-slate-50 text-slate-950">
        <Header />
        <main className="grid min-h-[calc(100vh-73px)] place-items-center">
          <div className="panel flex items-center gap-3">
            <Loader2 className="h-5 w-5 animate-spin text-cyan-700" />
            <span className="text-sm font-medium">Checking session...</span>
          </div>
        </main>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-slate-50 text-slate-950">
        <Header />
        <AuthPanel onAuthenticated={handleAuthenticated} />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-slate-50 text-slate-950">
      <Header user={user} onLogout={handleLogout} />
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
          {user.role === 'admin' && <UserAdminPanel currentUser={user} />}
        </section>
      </main>
    </div>
  );
}
