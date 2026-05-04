import axios, { AxiosInstance } from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

export interface PropertyFeatures {
  area_m2?: number;
  bedroom_count?: number;
  bathroom_count?: number;
  floor_count?: number;
  front_width_m?: number;
  road_width_m?: number;
  property_type?: string;
  direction?: string;
  legal?: string;
  listing_type?: string;
  province_slug?: string;
  district_slug?: string;
  ward_slug?: string;
  project_hint?: string;
  title?: string;
  description?: string;
  text_features?: string;
}

export interface PredictionResult {
  predicted_price_vnd: number;
  predicted_price_billion_vnd: number;
  prediction_date: string;
  latency_ms: number;
}

export interface ModelMetrics {
  mae_vnd?: number;
  rmse_vnd?: number;
  r2?: number;
  sample_count?: number;
}

export interface ModelInfoType {
  version?: string;
  training_sample_count?: number;
  model_metrics?: ModelMetrics;
  model_path: string;
  last_update?: string;
}

export interface HealthResponse {
  status: string;
  model_path: string;
  model_exists: boolean;
  model_metadata?: unknown;
  timestamp: string;
}

export async function checkHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/health');
  return response.data;
}

export async function getModelInfo(): Promise<ModelInfoType> {
  const response = await apiClient.get<ModelInfoType>('/model/info');
  return response.data;
}

export async function predictPrice(property: PropertyFeatures): Promise<PredictionResult> {
  try {
    const response = await apiClient.post<PredictionResult>('/predict', property);
    return response.data;
  } catch (error: any) {
    const message = error.response?.data?.detail || error.message || 'Prediction failed';
    throw new Error(message);
  }
}

export function handleApiError(error: unknown): string {
  if (axios.isAxiosError(error)) {
    if (error.response?.status === 503) return 'Model is not available yet.';
    if (error.response?.status === 400) return String(error.response.data?.detail || 'Invalid inputs.');
    if (error.response?.status === 500) return 'Server error. Please try again later.';
    return error.message;
  }
  return error instanceof Error ? error.message : 'An error occurred.';
}

export default apiClient;
