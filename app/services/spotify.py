from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SpotifyService:
    def __init__(self) -> None:
        # inicialização leve — sem chamadas externas aqui
        pass

    async def startup(self) -> None:
        logger.info("Spotify service startup (noop).")

    async def shutdown(self) -> None:
        logger.info("Spotify service shutdown (noop).")

    def build_auth_url(self, user_id: int) -> str:
        return f"/spotify/login?user_id={user_id}"

    def resolve_user_id_from_state(self, state: str) -> int | None:
        try:
            return int(state)
        except Exception:
            return None

    async def exchange_code_for_token(self, db, code: str, user_id: int) -> None:
        logger.info("Exchange token (noop).")

    async def get_current_or_last_played(self, db, user_id: int):
        return {
            "track_name": "Spotify temporarily disabled",
            "artist": "System",
            "album": "Maintenance",
            "spotify_url": None,
            "album_image_url": None,
            "is_playing": False,
            "played_at": None,
        }


# instância segura (não quebra startup)
spotify_service = SpotifyService()