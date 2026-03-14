from pydantic import BaseModel, Field
from typing import Dict, Any
from datetime import datetime


class FeatureRow(BaseModel):
    symbol: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    features: Dict[str, Any]


class PipelineEvent(BaseModel):
    event_type: str
    symbol: str
    stage: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=datetime.utcnow)
