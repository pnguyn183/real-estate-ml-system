"""
Real Estate Price Prediction API - FastAPI Service
Provides REST endpoints for price predictions with comprehensive documentation
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from modeling.auth import ROLES, AuthService, Role, UserRecord, build_auth_service, require_minimum_role
    from modeling.price_model import RealEstatePriceModel
except ImportError:
    from auth import ROLES, AuthService, Role, UserRecord, build_auth_service, require_minimum_role
    from price_model import RealEstatePriceModel


# Configuration
MODEL_PATH = Path(os.environ.get("MODEL_PATH", "artifacts/models/price_model.joblib"))
HOST = os.environ.get("API_HOST", "0.0.0.0")
PORT = int(os.environ.get("API_PORT", "8000"))

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Global model cache
_model: RealEstatePriceModel | None = None
_model_mtime: float | None = None
_model_metadata: Dict[str, Any] | None = None
_auth_service: AuthService = build_auth_service()
_login_attempts: dict[str, list[float]] = {}

LOGIN_ATTEMPT_LIMIT = int(os.environ.get("LOGIN_ATTEMPT_LIMIT", "5"))
LOGIN_ATTEMPT_WINDOW_SECONDS = int(os.environ.get("LOGIN_ATTEMPT_WINDOW_SECONDS", "900"))


# ============================================================================
# Pydantic Models for Request/Response Validation
# ============================================================================

class PropertyFeatures(BaseModel):
    """Property features for prediction"""
    
    area_m2: Optional[float] = Field(None, description="Property area in square meters")
    bedroom_count: Optional[int] = Field(None, description="Number of bedrooms")
    bathroom_count: Optional[int] = Field(None, description="Number of bathrooms")
    floor_count: Optional[int] = Field(None, description="Number of floors")
    front_width_m: Optional[float] = Field(None, description="Front width in meters")
    road_width_m: Optional[float] = Field(None, description="Road width in meters")
    
    property_type: Optional[str] = Field(None, description="Type: apartment, house, land, etc.")
    direction: Optional[str] = Field(None, description="Direction: north, south, east, west")
    legal: Optional[str] = Field(None, description="Legal status: redbook, pinkbook, other")
    listing_type: Optional[str] = Field(None, description="Type: sell, rent, other")
    
    province_slug: Optional[str] = Field(None, description="Province code")
    district_slug: Optional[str] = Field(None, description="District code")
    ward_slug: Optional[str] = Field(None, description="Ward code")
    project_hint: Optional[str] = Field(None, description="Project name if applicable")
    
    title: Optional[str] = Field(None, description="Property title")
    description: Optional[str] = Field(None, description="Property description")
    text_features: Optional[str] = Field(None, description="Additional text features")
    
    @validator("area_m2", "front_width_m", "road_width_m")
    def validate_positive_float(cls, v):
        if v is not None and v <= 0:
            raise ValueError("must be positive")
        return v
    
    @validator("bedroom_count", "bathroom_count", "floor_count")
    def validate_non_negative_int(cls, v):
        if v is not None and v < 0:
            raise ValueError("must be non-negative")
        return v


class PredictionRequest(BaseModel):
    """Single or batch prediction request"""
    properties: List[PropertyFeatures] = Field(..., description="List of properties to predict")
    include_confidence: bool = Field(False, description="Include confidence interval in response")


class PredictionResponse(BaseModel):
    """Single prediction response"""
    predicted_price_vnd: float = Field(..., description="Predicted price in VND")
    predicted_price_billion_vnd: float = Field(..., description="Predicted price in billion VND")
    price_per_m2_vnd: Optional[float] = Field(None, description="Predicted price per square meter")
    confidence_low_vnd: Optional[float] = Field(None, description="Lower bound of confidence range")
    confidence_high_vnd: Optional[float] = Field(None, description="Upper bound of confidence range")
    confidence_score: Optional[float] = Field(None, description="Model confidence score from 0 to 1")
    feature_quality_score: Optional[float] = Field(None, description="Input completeness score from 0 to 1")
    explanations: List[str] = Field(default_factory=list, description="Human-readable model signals")
    prediction_date: str = Field(..., description="Prediction timestamp")
    latency_ms: float = Field(..., description="Prediction latency in milliseconds")


class BatchPredictionResponse(BaseModel):
    """Batch prediction response"""
    predictions: List[PredictionResponse] = Field(..., description="List of predictions")
    total_count: int = Field(..., description="Total predictions made")
    successful_count: int = Field(..., description="Successful predictions")
    failed_count: int = Field(..., description="Failed predictions")
    total_latency_ms: float = Field(..., description="Total latency in milliseconds")


class HealthResponse(BaseModel):
    """Health check response"""
    status: str = Field(..., description="Service status: ready, initializing, error")
    model_path: str = Field(..., description="Model file path")
    model_exists: bool = Field(..., description="Whether model file exists")
    model_metadata: Optional[Dict[str, Any]] = Field(None, description="Model metadata if available")
    timestamp: str = Field(..., description="Response timestamp")


class ModelInfo(BaseModel):
    """Model information"""
    version: Optional[str] = Field(None, description="Model version")
    training_sample_count: Optional[int] = Field(None, description="Training sample count")
    model_metrics: Optional[Dict[str, float]] = Field(None, description="Model metrics (MAE, RMSE, R²)")
    model_path: str = Field(..., description="Model file path")
    last_update: Optional[str] = Field(None, description="Last model update time")


class UserPublic(BaseModel):
    """Safe user profile returned to clients"""
    id: str
    email: str
    full_name: str
    role: Role
    is_active: bool
    created_at: str
    updated_at: str
    last_login_at: Optional[str] = None


class RegisterRequest(BaseModel):
    """New account registration"""
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: str = Field("", max_length=120)


class LoginRequest(BaseModel):
    """Login credentials"""
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=8, max_length=128)


class AuthResponse(BaseModel):
    """Bearer token response"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserPublic


class RoleUpdateRequest(BaseModel):
    role: Role


class UserStatusRequest(BaseModel):
    is_active: bool


# ============================================================================
# Model Loading and Management
# ============================================================================

def get_model() -> RealEstatePriceModel:
    """Load model with caching and reload on file change.

    Caches an in-memory `RealEstatePriceModel` instance and reloads it when the
    underlying joblib file's modification time changes. Also attempts to load
    the most recent metadata JSON next to the model file.
    Raises FileNotFoundError if the model artifact is missing.
    """
    global _model, _model_mtime, _model_metadata
    
    if not MODEL_PATH.exists():
        raise FileNotFoundError(f"Model file not found: {MODEL_PATH}")
    
    mtime = MODEL_PATH.stat().st_mtime
    if _model is None or _model_mtime != mtime:
        logger.info("Loading model from %s", MODEL_PATH)
        _model = RealEstatePriceModel.load(str(MODEL_PATH))
        _model_mtime = mtime
        
        # Try to load metadata
        metadata_path = MODEL_PATH.parent / f"metadata_v{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        existing_metadata = sorted(
            MODEL_PATH.parent.glob("metadata_v*.json"),
            reverse=True
        )
        if existing_metadata:
            try:
                _model_metadata = json.loads(existing_metadata[0].read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("Failed to load metadata: %s", e)
                _model_metadata = None
        if getattr(_model, "metadata", None):
            _model_metadata = {**(_model_metadata or {}), **_model.metadata}
    
    return _model


# ============================================================================
# Authentication Helpers
# ============================================================================

def user_response(user: UserRecord) -> UserPublic:
    """Convert internal user record to a safe response model."""
    return UserPublic(**user.public())


def get_bearer_token(authorization: str | None = Header(default=None)) -> str:
    """Extract and validate the bearer token from the `Authorization` header.

    This dependency is used by route handlers to obtain the raw token string.
    Raises 401 if header is missing or malformed.
    """
    if not authorization:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Authorization header")
    return token


def current_user(token: str = Depends(get_bearer_token)) -> UserRecord:
    """Resolve a `UserRecord` from the provided bearer token.

    Delegates to the injected `AuthService`. Used as a FastAPI dependency to
    provide the authenticated user object to route handlers.
    """
    return _auth_service.user_from_token(token)


def require_roles(*roles: Role) -> Callable[[UserRecord], UserRecord]:
    def dependency(user: UserRecord = Depends(current_user)) -> UserRecord:
        """Dependency factory that enforces the given minimum roles.

        Example usage in route: `Depends(require_roles('manager'))`.
        Raises 403 if the user's role is not in the allowed set.
        """
        require_minimum_role(user, roles)
        return user

    return dependency


def login_rate_limit_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{email.strip().lower()}"


def assert_login_allowed(request: Request, email: str) -> str:
    """Check simple in-memory rate limit for login attempts by (host,email).

    Tracks recent attempts in `_login_attempts` and raises HTTP 429 when the
    configured limit is exceeded. Returns the rate-limit key on success.
    """
    key = login_rate_limit_key(request, email)
    now = time.time()
    attempts = [item for item in _login_attempts.get(key, []) if now - item < LOGIN_ATTEMPT_WINDOW_SECONDS]
    _login_attempts[key] = attempts
    if len(attempts) >= LOGIN_ATTEMPT_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Please try again later.",
        )
    return key


def record_failed_login(key: str) -> None:
    _login_attempts.setdefault(key, []).append(time.time())


def clear_login_attempts(key: str) -> None:
    _login_attempts.pop(key, None)


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Real Estate Price Prediction API",
    description="API for predicting property prices using machine learning models",
    version="1.0.0"
)

# Enable CORS for frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.environ.get("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000").split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.middleware("http")
async def add_security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    return response


# ============================================================================
# API Endpoints
# ============================================================================

@app.post("/auth/register", response_model=AuthResponse, status_code=status.HTTP_201_CREATED, tags=["Auth"])
async def register_account(request: RegisterRequest):
    """Register a new account. The first account becomes admin; later accounts become user."""
    user = _auth_service.register(request.email, request.password, request.full_name)
    token = _auth_service.create_access_token(user)
    return AuthResponse(
        access_token=token,
        expires_in=_auth_service.token_expire_seconds,
        user=user_response(user),
    )


@app.post("/auth/login", response_model=AuthResponse, tags=["Auth"])
async def login(request: LoginRequest, http_request: Request):
    """Authenticate and issue a short-lived bearer token."""
    rate_key = assert_login_allowed(http_request, request.email)
    try:
        user = _auth_service.authenticate(request.email, request.password)
    except HTTPException:
        record_failed_login(rate_key)
        raise
    clear_login_attempts(rate_key)
    token = _auth_service.create_access_token(user)
    return AuthResponse(
        access_token=token,
        expires_in=_auth_service.token_expire_seconds,
        user=user_response(user),
    )


@app.get("/auth/me", response_model=UserPublic, tags=["Auth"])
async def get_current_profile(user: UserRecord = Depends(current_user)):
    """Return the authenticated user's profile."""
    return user_response(user)


@app.get("/auth/roles", tags=["Auth"])
async def get_roles(user: UserRecord = Depends(current_user)):
    """Return available roles for authenticated users."""
    return {"roles": list(ROLES), "current_role": user.role}


@app.get("/auth/users", response_model=List[UserPublic], tags=["Admin"])
async def list_users(user: UserRecord = Depends(require_roles("admin"))):
    """List users. Admin only."""
    return [user_response(item) for item in _auth_service.list_users()]


@app.patch("/auth/users/{user_id}/role", response_model=UserPublic, tags=["Admin"])
async def update_user_role(
    user_id: str,
    request: RoleUpdateRequest,
    user: UserRecord = Depends(require_roles("admin")),
):
    """Change a user's role. Admin only."""
    updated = _auth_service.update_user_role(user_id, request.role, user)
    return user_response(updated)


@app.patch("/auth/users/{user_id}/status", response_model=UserPublic, tags=["Admin"])
async def update_user_status(
    user_id: str,
    request: UserStatusRequest,
    user: UserRecord = Depends(require_roles("admin")),
):
    """Enable or disable a user. Admin only."""
    updated = _auth_service.set_user_active(user_id, request.is_active, user)
    return user_response(updated)


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check():
    """Check service health and model availability"""
    try:
        model_exists = MODEL_PATH.exists()
        status = "ready" if model_exists else "initializing"
        
        if model_exists:
            try:
                get_model()
            except Exception as e:
                status = "error"
                logger.error("Model load failed: %s", e)
        
        return HealthResponse(
            status=status,
            model_path=str(MODEL_PATH),
            model_exists=model_exists,
            model_metadata=_model_metadata,
            timestamp=datetime.now(timezone.utc).isoformat()
        )
    except Exception as e:
        logger.error("Health check failed: %s", e)
        raise HTTPException(status_code=500, detail="Health check failed")


@app.get("/model/info", response_model=ModelInfo, tags=["Model"])
async def model_info(user: UserRecord = Depends(require_roles("manager", "admin"))):
    """Get current model information. Manager or admin only."""
    try:
        if not MODEL_PATH.exists():
            raise HTTPException(status_code=503, detail="Model not available")

        get_model()
        metadata = _model_metadata or {}
        return ModelInfo(
            version=metadata.get("version"),
            training_sample_count=metadata.get("sample_count"),
            model_metrics=metadata.get("metrics"),
            model_path=str(MODEL_PATH),
            last_update=metadata.get("updated_at")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Model info retrieval failed: %s", e)
        raise HTTPException(status_code=500, detail="Model info retrieval failed")


@app.post("/predict", response_model=PredictionResponse, tags=["Predictions"])
async def predict_single(
    property: PropertyFeatures,
    user: UserRecord = Depends(require_roles("user", "manager", "admin")),
):
    """Predict price for a single property. Any authenticated active user."""
    try:
        if not MODEL_PATH.exists():
            raise HTTPException(status_code=503, detail="Model not available")
        
        start_time = time.time()
        model = get_model()
        
        # Convert to dict format expected by model
        record = property.model_dump(exclude_none=False)
        prediction = model.predict(record)
        
        latency_ms = (time.time() - start_time) * 1000
        
        logger.info(
            "Prediction successful: %s VND, latency: %.2fms",
            prediction.get("predicted_price_vnd"),
            latency_ms
        )
        
        return PredictionResponse(
            predicted_price_vnd=prediction["predicted_price_vnd"],
            predicted_price_billion_vnd=prediction["predicted_price_billion_vnd"],
            price_per_m2_vnd=prediction.get("price_per_m2_vnd"),
            confidence_low_vnd=prediction.get("confidence_low_vnd"),
            confidence_high_vnd=prediction.get("confidence_high_vnd"),
            confidence_score=prediction.get("confidence_score"),
            feature_quality_score=prediction.get("feature_quality_score"),
            explanations=prediction.get("explanations", []),
            prediction_date=datetime.now(timezone.utc).isoformat(),
            latency_ms=latency_ms
        )
    except HTTPException:
        raise
    except FileNotFoundError as e:
        logger.error("Model not found: %s", e)
        raise HTTPException(status_code=503, detail="Model not available")
    except Exception as e:
        logger.error("Prediction failed: %s", e)
        raise HTTPException(status_code=400, detail=f"Prediction failed: {str(e)}")


@app.post("/predict/batch", response_model=BatchPredictionResponse, tags=["Predictions"])
async def predict_batch(
    request: PredictionRequest,
    user: UserRecord = Depends(require_roles("manager", "admin")),
):
    """Predict prices for multiple properties. Manager or admin only."""
    try:
        if not MODEL_PATH.exists():
            raise HTTPException(status_code=503, detail="Model not available")
        
        start_time = time.time()
        model = get_model()
        
        predictions = []
        failed_count = 0
        
        for i, property in enumerate(request.properties):
            try:
                record = property.model_dump(exclude_none=False)
                prediction_result = model.predict(record)
                
                latency_ms = (time.time() - start_time) * 1000
                
                predictions.append(
                    PredictionResponse(
                        predicted_price_vnd=prediction_result["predicted_price_vnd"],
                        predicted_price_billion_vnd=prediction_result["predicted_price_billion_vnd"],
                        price_per_m2_vnd=prediction_result.get("price_per_m2_vnd"),
                        confidence_low_vnd=prediction_result.get("confidence_low_vnd"),
                        confidence_high_vnd=prediction_result.get("confidence_high_vnd"),
                        confidence_score=prediction_result.get("confidence_score"),
                        feature_quality_score=prediction_result.get("feature_quality_score"),
                        explanations=prediction_result.get("explanations", []),
                        prediction_date=datetime.now(timezone.utc).isoformat(),
                        latency_ms=latency_ms
                    )
                )
            except Exception as e:
                logger.warning("Batch prediction failed for item %d: %s", i, e)
                failed_count += 1
        
        total_latency_ms = (time.time() - start_time) * 1000
        
        return BatchPredictionResponse(
            predictions=predictions,
            total_count=len(request.properties),
            successful_count=len(predictions),
            failed_count=failed_count,
            total_latency_ms=total_latency_ms
        )
    except HTTPException:
        raise
    except FileNotFoundError as e:
        logger.error("Model not found: %s", e)
        raise HTTPException(status_code=503, detail="Model not available")
    except Exception as e:
        logger.error("Batch prediction failed: %s", e)
        raise HTTPException(status_code=400, detail=f"Batch prediction failed: {str(e)}")


# ============================================================================
# Application Startup
# ============================================================================

@app.on_event("startup")
async def startup_event():
    """Initialize model on startup"""
    try:
        logger.info("Starting Real Estate Price Prediction API")
        if MODEL_PATH.exists():
            get_model()
            logger.info("Model loaded successfully")
        else:
            logger.warning("Model file not found at %s", MODEL_PATH)
    except Exception as e:
        logger.error("Startup failed: %s", e)


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on shutdown"""
    logger.info("Shutting down Real Estate Price Prediction API")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host=HOST,
        port=PORT,
        log_level="info"
    )
