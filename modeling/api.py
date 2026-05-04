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
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from modeling.price_model import RealEstatePriceModel
except ImportError:
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


# ============================================================================
# Model Loading and Management
# ============================================================================

def get_model() -> RealEstatePriceModel:
    """Load model with caching and reload on file change"""
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
    
    return _model


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
    allow_origins=os.environ.get("CORS_ORIGINS", "*").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# API Endpoints
# ============================================================================

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
async def model_info():
    """Get current model information"""
    try:
        if not MODEL_PATH.exists():
            raise HTTPException(status_code=503, detail="Model not available")
        
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
async def predict_single(property: PropertyFeatures):
    """Predict price for a single property"""
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
async def predict_batch(request: PredictionRequest):
    """Predict prices for multiple properties (batch)"""
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
