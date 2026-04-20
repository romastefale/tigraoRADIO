from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)


async def enrich_track_if_missing(track_id: str) -> None:
    if not track_id:
        return

    try:
        with SessionLocal() as db:
            existing = db.execute(
                text(
                    """
                    SELECT track_id
                    FROM track_audio_features
                    WHERE track_id = :track_id
                    """
                ),
                {"track_id": track_id},
            ).first()

            if existing:
                return

        features = await spotify_service.get_audio_features(track_id)
        if not features:
            return

        with SessionLocal() as db:
            db.execute(
                text(
                    """
                    INSERT INTO track_audio_features (
                        track_id,
                        valence,
                        energy,
                        danceability,
                        created_at
                    ) VALUES (
                        :track_id,
                        :valence,
                        :energy,
                        :danceability,
                        :created_at
                    )
                    """
                ),
                {
                    "track_id": track_id,
                    "valence": features["valence"],
                    "energy": features["energy"],
                    "danceability": features["danceability"],
                    "created_at": datetime.utcnow(),
                },
            )
            db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Track enrichment failed for track_id=%s: %s", track_id, exc)
