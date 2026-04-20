from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text

from app.db.database import SessionLocal
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)


async def enrich_track_if_missing(track_id: str) -> None:
    normalized_track_id = track_id.strip() if isinstance(track_id, str) else ""
    if not normalized_track_id:
        return

    logger.info("ENRICH_START track_id=%s", normalized_track_id)

    try:
        features = await spotify_service.get_audio_features(normalized_track_id)
        logger.info("ENRICH_FEATURES track_id=%s data=%s", normalized_track_id, features)
        if features is None:
            logger.error("ENRICH_NO_DATA track_id=%s", normalized_track_id)
            return

        with SessionLocal() as db:
            logger.info("ENRICH_INSERT track_id=%s", normalized_track_id)
            db.execute(
                text(
                    """
                    INSERT OR IGNORE INTO track_audio_features (
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
                    "track_id": normalized_track_id,
                    "valence": features["valence"],
                    "energy": features["energy"],
                    "danceability": features["danceability"],
                    "created_at": datetime.utcnow(),
                },
            )
            db.commit()
            logger.info("ENRICH_SUCCESS track_id=%s", normalized_track_id)
    except Exception:
        logger.exception("Failed to enrich track audio features for track_id=%s", normalized_track_id)
