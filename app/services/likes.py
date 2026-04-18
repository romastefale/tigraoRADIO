from __future__ import annotations

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.track_like import TrackLike
from app.models.track_play import TrackPlay


class LikesService:
    async def register_play(self, db: Session, user_id: int, track_id: str) -> None:
        db.add(TrackPlay(user_id=user_id, track_id=track_id))
        db.commit()

    async def get_track_play_count(self, db: Session, track_id: str) -> int:
        stmt = select(func.count(TrackPlay.id)).where(TrackPlay.track_id == track_id)
        result = db.execute(stmt).scalar_one()
        return int(result)

    async def get_user_play_count(self, db: Session, user_id: int, track_id: str) -> int:
        stmt = select(func.count(TrackPlay.id)).where(
            TrackPlay.user_id == user_id,
            TrackPlay.track_id == track_id,
        )
        result = db.execute(stmt).scalar_one()
        return int(result)

    async def is_track_liked(self, db: Session, user_id: int, track_id: str) -> bool:
        stmt = select(TrackLike.id).where(
            TrackLike.user_id == user_id,
            TrackLike.track_id == track_id,
        )
        return db.execute(stmt).first() is not None

    async def get_track_like_count(self, db: Session, track_id: str) -> int:
        stmt = select(func.count(TrackLike.id)).where(TrackLike.track_id == track_id)
        result = db.execute(stmt).scalar_one()
        return int(result)


likes_service = LikesService()
