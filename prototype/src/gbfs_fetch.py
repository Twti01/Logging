"""Holt einen Snapshot aus dem Citi-Bike-GBFS-Feed und schreibt ihn in die DB.

Laeuft als Systemd-Timer alle 5 Minuten (oneshot). Fuer einen manuellen
Einzelaufruf: python -m src.gbfs_fetch
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime, timezone

import requests
import h3
from shapely.geometry import Point, shape

from .config import load_config
from .db_schema import Station, StationStatus, get_session, init_db

# Vereinfachtes Brooklyn-Polygon (Kings County Grenzen, im Uhrzeigersinn)
# Genau genug um alle Citi-Bike-Stationen korrekt Brooklyn zuzuordnen.
_BROOKLYN_COORDS = [
    (-74.0421, 40.6160),  # Bay Ridge SW
    (-74.0300, 40.5870),  # Gravesend
    (-74.0042, 40.5707),  # Coney Island West
    (-73.9450, 40.5707),  # Coney Island East
    (-73.8800, 40.5870),  # Sheepshead Bay
    (-73.8334, 40.6100),  # Canarsie / SE-Ecke
    (-73.8334, 40.7000),  # East New York
    (-73.8600, 40.7384),  # Bushwick / Queens-Grenze
    (-73.9300, 40.7384),  # Greenpoint / Queens-Grenze Nord
    (-73.9505, 40.7295),  # Greenpoint NW
    (-73.9800, 40.7000),  # Brooklyn Heights
    (-74.0200, 40.6780),  # Sunset Park / Red Hook
    (-74.0421, 40.6160),  # zurueck zum Start
]
_brooklyn_polygon = None


def _get_brooklyn_polygon():
    """Gibt das statische Brooklyn-Polygon zurueck (wird einmal gebaut, dann gecacht)."""
    global _brooklyn_polygon
    if _brooklyn_polygon is None:
        from shapely.geometry import Polygon
        _brooklyn_polygon = Polygon(_BROOKLYN_COORDS)
        log.info("Brooklyn-Polygon initialisiert (%d Stuetzpunkte)", len(_BROOKLYN_COORDS))
    return _brooklyn_polygon

# ---------------------------------------------------------------------------
# Logging-Setup: Ausgabe nach stdout (landet in journalctl)
# ---------------------------------------------------------------------------
logging.basicConfig(
    stream=sys.stdout,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------

def _to_h3(lat: float, lon: float, res: int) -> str:
    """Kompatibel mit h3-py v3 und v4."""
    if hasattr(h3, "latlng_to_cell"):   # v4
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)  # v3


def _fetch_json(url: str, timeout: int = 15) -> dict:
    """HTTP-GET mit Fehlerbehandlung."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        log.error("HTTP-Fehler beim Abrufen von %s: %s", url, exc)
        raise


# ---------------------------------------------------------------------------
# Haupt-Funktion
# ---------------------------------------------------------------------------

def fetch_once() -> dict:
    """Holt einen GBFS-Snapshot und schreibt ihn in die PostgreSQL-DB.

    Rueckgabe: {n_stations_neu, n_status, ts}
    """
    cfg  = load_config()
    res  = cfg["study_area"]["h3_resolution"]

    log.info("GBFS-Abruf gestartet")

    info   = _fetch_json(cfg["gbfs"]["info_url"])
    status = _fetch_json(cfg["gbfs"]["status_url"])
    ts     = datetime.now(timezone.utc)

    # Datenbank-Schema anlegen falls noch nicht vorhanden
    init_db()
    session = get_session()

    n_stations_neu = 0
    n_status       = 0
    n_bbox_skip    = 0

    brooklyn = _get_brooklyn_polygon()
    info_index = {s["station_id"]: s for s in info["data"]["stations"]}

    try:
        for s in status["data"]["stations"]:
            meta = info_index.get(s["station_id"])
            if not meta:
                continue

            lat, lon = meta["lat"], meta["lon"]

            # Exakte Polygon-Pruefung statt Bounding Box
            if not brooklyn.contains(Point(lon, lat)):
                n_bbox_skip += 1
                continue

            h3_id = _to_h3(lat, lon, res)

            # Station anlegen falls noch nicht in DB
            existing = session.get(Station, meta["station_id"])
            if existing is None:
                session.add(Station(
                    station_id=meta["station_id"],
                    name=meta.get("name", ""),
                    lat=lat,
                    lon=lon,
                    h3_id=h3_id,
                    capacity=meta.get("capacity", 0),
                ))
                n_stations_neu += 1

            # Status-Snapshot schreiben
            session.add(StationStatus(
                station_id=meta["station_id"],
                timestamp=ts,
                bikes_available=s.get("num_bikes_available", 0),
                docks_available=s.get("num_docks_available", 0),
                is_renting=int(s.get("is_renting", 0)),
                is_returning=int(s.get("is_returning", 0)),
            ))
            n_status += 1

        session.commit()
        log.info(
            "GBFS OK — neue Stationen: %d, Status-Zeilen: %d, ausserhalb bbox: %d",
            n_stations_neu, n_status, n_bbox_skip,
        )

    except Exception as exc:
        session.rollback()
        log.error("DB-Fehler, Rollback: %s", exc)
        raise
    finally:
        session.close()

    return {
        "n_stations_neu": n_stations_neu,
        "n_status":       n_status,
        "ts":             ts.isoformat(),
    }


if __name__ == "__main__":
    fetch_once()
