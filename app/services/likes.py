from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.models.track_like import TrackLike
from app.models.track_play import TrackPlay


class LikesService:
    def _new_session(self) -> Session:
        return SessionLocal()

    async def register_play(self, user_id: int, track_id: str) -> None:
        with self._new_session() as db:
            db.add(TrackPlay(user_id=user_id, track_id=track_id))
            db.commit()

    async def get_track_play_count(self, track_id: str) -> int:
        with self._new_session() as db:
            stmt = select(func.count(TrackPlay.id)).where(TrackPlay.track_id == track_id)
            result = db.execute(stmt).scalar_one()
            return int(result)

    async def get_user_play_count(self, user_id: int, track_id: str) -> int:
        with self._new_session() as db:
            stmt = select(func.count(TrackPlay.id)).where(
                TrackPlay.user_id == user_id,
                TrackPlay.track_id == track_id,
            )
            result = db.execute(stmt).scalar_one()
            return int(result)

    async def is_track_liked(self, user_id: int, track_id: str) -> bool:
        with self._new_session() as db:
            stmt = select(TrackLike.id).where(
                TrackLike.user_id == user_id,
                TrackLike.track_id == track_id,
            )
            return db.execute(stmt).first() is not None

    async def get_total_likes(self, track_id: str) -> int:
        with self._new_session() as db:
            stmt = select(func.count(TrackLike.id)).where(TrackLike.track_id == track_id)
            result = db.execute(stmt).scalar_one()
            return int(result)

    async def get_user_total_likes(self, user_id: int) -> int:
        with self._new_session() as db:
            stmt = select(func.count(TrackLike.id)).where(TrackLike.user_id == user_id)
            result = db.execute(stmt).scalar_one()
            return int(result)

    async def toggle_track_like(self, user_id: int, track_id: str) -> bool:
        with self._new_session() as db:
            try:
                stmt = select(TrackLike).where(
                    TrackLike.user_id == user_id,
                    TrackLike.track_id == track_id,
                )
                existing = db.execute(stmt).scalar_one_or_none()
                if existing:
                    db.delete(existing)
                    db.commit()
                    return False

                db.add(TrackLike(user_id=user_id, track_id=track_id))
                db.commit()
                return True
            except IntegrityError:
                db.rollback()

        return await self.is_track_liked(user_id, track_id)


likes_service = LikesService()
