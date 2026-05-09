"""PostgreSQL + TimescaleDB Schema fuer die Brooklyn Bike Thesis.

Tabellen:
- stations           Stammdaten der Citi-Bike-Stationen
- station_status     Zeitreihen-Snapshots (TimescaleDB Hypertable)
- scenarios          Was-waere-wenn-Szenarien aus dem LLM-Generator
- predictions        Forecast-Outputs pro H3-Zelle und Horizont
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    Column, DateTime, Float, Integer, String, Text, JSON,
    create_engine, text,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# .env laden (DATABASE_URL muss dort stehen)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

Base = declarative_base()


# ---------------------------------------------------------------------------
# Tabellen
# ---------------------------------------------------------------------------

class Station(Base):
    __tablename__ = "stations"

    station_id = Column(String, primary_key=True)
    name       = Column(String)
    lat        = Column(Float)
    lon        = Column(Float)
    h3_id      = Column(String, index=True)
    capacity   = Column(Integer)


class StationStatus(Base):
    """Zeitreihen-Tabelle – wird nach init_db() zur TimescaleDB Hypertable.

    Primary Key ist (station_id, timestamp) – TimescaleDB verlangt dass die
    Partitionierungsspalte (timestamp) Teil des Primary Keys ist.
    """
    __tablename__ = "station_status"

    station_id      = Column(String, primary_key=True)
    timestamp       = Column(DateTime(timezone=True), primary_key=True, nullable=False)
    bikes_available = Column(Integer)
    docks_available = Column(Integer)
    is_renting      = Column(Integer)
    is_returning    = Column(Integer)


class Scenario(Base):
    __tablename__ = "scenarios"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    description         = Column(Text)
    event_type          = Column(String)
    h3_id               = Column(String)
    start_time          = Column(DateTime(timezone=True))
    duration_h          = Column(Float)
    magnitude           = Column(Float)
    conditioning_vector = Column(JSON)
    plausibility_score  = Column(Float)
    generation_method   = Column(String)


class Prediction(Base):
    __tablename__ = "predictions"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    h3_id            = Column(String, index=True)
    target_time      = Column(DateTime(timezone=True), index=True)
    horizon_h        = Column(Integer)
    predicted_demand = Column(Float)
    quantile_low     = Column(Float)
    quantile_high    = Column(Float)
    scenario_id      = Column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Engine, Session, Init
# ---------------------------------------------------------------------------

def get_engine():
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        raise EnvironmentError(
            "DATABASE_URL nicht gesetzt. Bitte .env-Datei pruefen.\n"
            "Erwartet: DATABASE_URL=postgresql://thesis_admin:PW@localhost:5432/brooklyn_bike_db"
        )
    return create_engine(db_url, pool_pre_ping=True)


def init_db():
    """Legt alle Tabellen an und aktiviert TimescaleDB Hypertable fuer station_status."""
    engine = get_engine()
    Base.metadata.create_all(engine)

    with engine.connect() as conn:
        conn.execute(text(
            "SELECT create_hypertable('station_status', 'timestamp', "
            "if_not_exists => TRUE);"
        ))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.commit()

    print(f"DB initialisiert: {engine.url}")
    return engine


def get_session():
    engine = get_engine()
    return sessionmaker(bind=engine)()


if __name__ == "__main__":
    init_db()
    print("Schema erfolgreich angelegt.")
