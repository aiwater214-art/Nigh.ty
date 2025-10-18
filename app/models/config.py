from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer

from app.core.database import Base


class GameplayConfig(Base):
    __tablename__ = "gameplay_config"

    id = Column(Integer, primary_key=True, index=True)
    width = Column(Float, default=1000.0, nullable=False)
    height = Column(Float, default=1000.0, nullable=False)
    tick_rate = Column(Float, default=30.0, nullable=False)
    food_count = Column(Integer, default=200, nullable=False)
    snapshot_interval = Column(Float, default=10.0, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def as_dict(self) -> dict:
        return {
            "width": float(self.width),
            "height": float(self.height),
            "tick_rate": float(self.tick_rate),
            "food_count": int(self.food_count),
            "snapshot_interval": float(self.snapshot_interval),
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

