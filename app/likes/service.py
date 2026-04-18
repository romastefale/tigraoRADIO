from __future__ import annotations

from dataclasses import dataclass

from app.likes.storage import LikesStorage, PlayEvent


@dataclass(frozen=True)
class LikeToggleResult:
    user_id: int
    track_id: str
    liked: bool


class LikesService:
    """Business layer for likes and play-tracking features."""

    def __init__(self, storage: LikesStorage) -> None:
        self.storage = storage

    def initialize(self) -> None:
        self.storage.ensure_schema()

    def register_play(self, *, user_id: int, track_id: str) -> PlayEvent:
        return self.storage.track_play(user_id=user_id, track_id=track_id)

    def toggle_track_like(self, *, user_id: int, track_id: str) -> LikeToggleResult:
        liked = self.storage.toggle_like(user_id=user_id, track_id=track_id)
        return LikeToggleResult(user_id=user_id, track_id=track_id, liked=liked)

    def get_track_play_count(self, *, track_id: str) -> int:
        return self.storage.get_track_play_count(track_id=track_id)

    def get_user_play_count(self, *, user_id: int) -> int:
        return self.storage.get_user_play_count(user_id=user_id)

    def is_track_liked(self, *, user_id: int, track_id: str) -> bool:
        return self.storage.is_liked(user_id=user_id, track_id=track_id)


likes_service = LikesService(LikesStorage(database_path="./data/app.db"))
