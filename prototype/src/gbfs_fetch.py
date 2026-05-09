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

from .config import load_config
from .db_schema import Station, StationStatus, get_session, init_db

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
    bbox = cfg["study_area"]["bbox"]

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

    info_index = {s["station_id"]: s for s in info["data"]["stations"]}

    try:
        for s in status["data"]["stations"]:
            meta = info_index.get(s["station_id"])
            if not meta:
                continue

            lat, lon = meta["lat"], meta["lon"]

            # Nur Stationen innerhalb der Bounding Box behalten
            if not (
                bbox["south"] <= lat <= bbox["north"]
                and bbox["west"] <= lon <= bbox["east"]
            ):
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
