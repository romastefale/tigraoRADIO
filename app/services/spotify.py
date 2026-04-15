from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class SpotifyService:
    def __init__(self) -> None:
        # inicialização leve, sem rede e sem validação agressiva
        pass

    async def startup(self) -> None:
        logger.info("Spotify service started.")

    async def shutdown(self) -> None:
        logger.info("Spotify service stopped.")

    def build_auth_url(self, user_id: int) -> str:
        return f"/spotify/login?user_id={user_id}"

    def resolve_user_id_from_state(self, state: str) -> int | None:
        try:
            return int(state)
        except Exception:
            return None

    async def exchange_code_for_token(self, db, code: str, user_id: int) -> None:
        logger.info("Token exchange skipped in safe mode for user_id=%s", user_id)

    async def get_current_or_last_played(
        self, db, user_id: int
    ) -> dict[str, Any] | None:
        return {
            "source": "last",
            "played_at": None,
            "track_name": "Spotify ainda não configurado",
            "artist": "Sistema",
            "album": "Inicialização",
            "spotify_url": None,
            "album_image_url": None,
        }

    async def clear_user_session(self, db, user_id: int) -> bool:
        logger.info("Clear session called for user_id=%s", user_id)
        return True


spotify_service = SpotifyService()