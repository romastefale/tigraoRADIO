from __future__ import annotations

from sqlalchemy import func, select, text
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

    async def get_user_top_tracks(self, user_id: int, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            stmt = text(
                """
                SELECT track_name, plays
                FROM user_play_counts
                WHERE user_id = :user_id
                ORDER BY plays DESC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"user_id": user_id, "limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_user_top_artists(self, user_id: int, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            stmt = text(
                """
                SELECT artist_name, SUM(plays) AS total_plays
                FROM user_play_counts
                WHERE user_id = :user_id
                GROUP BY artist_name
                ORDER BY total_plays DESC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"user_id": user_id, "limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_group_top_tracks(self, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            stmt = text(
                """
                SELECT track_name, SUM(plays) AS total_plays
                FROM user_play_counts
                GROUP BY track_name
                ORDER BY total_plays DESC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_group_top_artists(self, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            stmt = text(
                """
                SELECT artist_name, SUM(plays) AS total_plays
                FROM user_play_counts
                GROUP BY artist_name
                ORDER BY total_plays DESC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_group_most_liked_tracks(self, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            stmt = text(
                """
                SELECT upc.track_name, likes.total_likes
                FROM (
                    SELECT track_id, COUNT(*) AS total_likes
                    FROM track_likes
                    GROUP BY track_id
                ) AS likes
                JOIN (
                    SELECT track_id, MAX(track_name) AS track_name
                    FROM user_play_counts
                    GROUP BY track_id
                ) AS upc ON upc.track_id = likes.track_id
                ORDER BY likes.total_likes DESC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

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
