import axios, { AxiosInstance } from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000';

const apiClient: AxiosInstance = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

const TOKEN_STORAGE_KEY = 'real_estate_auth_token';

export type UserRole = 'admin' | 'manager' | 'user';

export interface AuthUser {
  id: string;
  email: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_login_at?: string;
}

export interface AuthResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
  user: AuthUser;
}

export interface LoginPayload {
  email: string;
  password: string;
}

export interface RegisterPayload extends LoginPayload {
  full_name?: string;
}

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
  price_per_m2_vnd?: number;
  confidence_low_vnd?: number;
  confidence_high_vnd?: number;
  confidence_score?: number;
  feature_quality_score?: number;
  explanations?: string[];
  prediction_date: string;
  latency_ms: number;
}

export interface ModelMetrics {
  mae_vnd?: number;
  rmse_vnd?: number;
  r2?: number;
  sample_count?: number;
  median_absolute_percentage_error?: number;
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

export function getStoredToken(): string | null {
  return localStorage.getItem(TOKEN_STORAGE_KEY);
}

export function setAuthToken(token: string | null) {
  if (token) {
    localStorage.setItem(TOKEN_STORAGE_KEY, token);
    apiClient.defaults.headers.common.Authorization = `Bearer ${token}`;
    return;
  }
  localStorage.removeItem(TOKEN_STORAGE_KEY);
  delete apiClient.defaults.headers.common.Authorization;
}

const existingToken = getStoredToken();
if (existingToken) {
  setAuthToken(existingToken);
}

export async function checkHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/health');
  return response.data;
}

export async function registerAccount(payload: RegisterPayload): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/register', payload);
  setAuthToken(response.data.access_token);
  return response.data;
}

export async function loginAccount(payload: LoginPayload): Promise<AuthResponse> {
  const response = await apiClient.post<AuthResponse>('/auth/login', payload);
  setAuthToken(response.data.access_token);
  return response.data;
}

export async function getCurrentUser(): Promise<AuthUser> {
  const response = await apiClient.get<AuthUser>('/auth/me');
  return response.data;
}

export async function listUsers(): Promise<AuthUser[]> {
  const response = await apiClient.get<AuthUser[]>('/auth/users');
  return response.data;
}

export async function updateUserRole(userId: string, role: UserRole): Promise<AuthUser> {
  const response = await apiClient.patch<AuthUser>(`/auth/users/${userId}/role`, { role });
  return response.data;
}

export async function updateUserStatus(userId: string, isActive: boolean): Promise<AuthUser> {
  const response = await apiClient.patch<AuthUser>(`/auth/users/${userId}/status`, { is_active: isActive });
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
    if (error.response?.status === 401) return 'Please sign in again.';
    if (error.response?.status === 403) return 'Your role does not have permission for this action.';
    if (error.response?.status === 409) return String(error.response.data?.detail || 'This account already exists.');
    if (error.response?.status === 422) return String(error.response.data?.detail || 'Please check the submitted fields.');
    if (error.response?.status === 429) return 'Too many attempts. Please try again later.';
    if (error.response?.status === 503) return 'Model is not available yet.';
    if (error.response?.status === 400) return String(error.response.data?.detail || 'Invalid inputs.');
    if (error.response?.status === 500) return 'Server error. Please try again later.';
    return error.message;
  }
  return error instanceof Error ? error.message : 'An error occurred.';
}

export default apiClient;
