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

    def _table_has_column(self, db: Session, table_name: str, column_name: str) -> bool:
        stmt = text(f"PRAGMA table_info({table_name})")
        rows = db.execute(stmt).all()
        return any(str(row[1]) == column_name for row in rows)

    def _normalize_optional_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    async def register_play(
        self,
        user_id: int,
        track_id: str,
        track_name: str | None = None,
        artist_name: str | None = None,
    ) -> None:
        normalized_track_name = self._normalize_optional_text(track_name)
        normalized_artist_name = self._normalize_optional_text(artist_name)
        with self._new_session() as db:
            db.add(
                TrackPlay(
                    user_id=user_id,
                    track_id=track_id,
                    track_name=normalized_track_name,
                    artist_name=normalized_artist_name,
                )
            )
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
            has_track_name = self._table_has_column(db, "track_plays", "track_name")
            if has_track_name:
                stmt = text(
                    """
                    SELECT
                        track_id,
                        COALESCE(MAX(track_name), track_id) AS track_label,
                        COUNT(*) AS plays
                    FROM track_plays
                    WHERE user_id = :user_id
                    GROUP BY track_id
                    ORDER BY plays DESC, track_label ASC
                    LIMIT :limit
                    """
                )
            else:
                stmt = text(
                    """
                    SELECT
                        track_id,
                        track_id AS track_label,
                        COUNT(*) AS plays
                    FROM track_plays
                    WHERE user_id = :user_id
                    GROUP BY track_id
                    ORDER BY plays DESC, track_label ASC
                    LIMIT :limit
                    """
                )
            rows = db.execute(stmt, {"user_id": user_id, "limit": limit}).all()
            return [(str(row[1]), int(row[2])) for row in rows]

    async def get_user_top_artists(self, user_id: int, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            if not self._table_has_column(db, "track_plays", "artist_name"):
                return []
            stmt = text(
                """
                SELECT artist_name, COUNT(*) AS total_plays
                FROM track_plays
                WHERE user_id = :user_id
                GROUP BY artist_name
                ORDER BY total_plays DESC, artist_name ASC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"user_id": user_id, "limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_group_top_tracks(self, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            has_track_name = self._table_has_column(db, "track_plays", "track_name")
            if has_track_name:
                stmt = text(
                    """
                    SELECT
                        track_id,
                        COALESCE(MAX(track_name), track_id) AS track_label,
                        COUNT(*) AS total_plays
                    FROM track_plays
                    GROUP BY track_id
                    ORDER BY total_plays DESC, track_label ASC
                    LIMIT :limit
                    """
                )
            else:
                stmt = text(
                    """
                    SELECT
                        track_id,
                        track_id AS track_label,
                        COUNT(*) AS total_plays
                    FROM track_plays
                    GROUP BY track_id
                    ORDER BY total_plays DESC, track_label ASC
                    LIMIT :limit
                    """
                )
            rows = db.execute(stmt, {"limit": limit}).all()
            return [(str(row[1]), int(row[2])) for row in rows]

    async def get_top_tracks(self, limit: int = 5) -> list[tuple[str, int]]:
        return await self.get_group_top_tracks(limit=limit)

    async def get_group_top_artists(self, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            if not self._table_has_column(db, "track_plays", "artist_name"):
                return []
            stmt = text(
                """
                SELECT artist_name, COUNT(*) AS total_plays
                FROM track_plays
                GROUP BY artist_name
                ORDER BY total_plays DESC, artist_name ASC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_top_artists(self, limit: int = 5) -> list[tuple[str, int]]:
        return await self.get_group_top_artists(limit=limit)

    async def get_group_most_liked_tracks(self, limit: int = 5) -> list[tuple[str, int]]:
        with self._new_session() as db:
            has_track_name = self._table_has_column(db, "track_plays", "track_name")
            has_liked_flag = self._table_has_column(db, "track_likes", "liked")
            liked_filter = "WHERE liked = 1" if has_liked_flag else ""
            track_label = "COALESCE(MAX(tp.track_name), likes.track_id)" if has_track_name else "likes.track_id"
            stmt = text(
                f"""
                SELECT {track_label} AS track_label, likes.total_likes
                FROM (
                    SELECT track_id, COUNT(*) AS total_likes
                    FROM track_likes
                    {liked_filter}
                    GROUP BY track_id
                ) AS likes
                LEFT JOIN track_plays tp ON tp.track_id = likes.track_id
                GROUP BY likes.track_id, likes.total_likes
                ORDER BY likes.total_likes DESC, track_label ASC
                LIMIT :limit
                """
            )
            rows = db.execute(stmt, {"limit": limit}).all()
            return [(str(row[0]), int(row[1])) for row in rows]

    async def get_most_liked_tracks(self, limit: int = 5) -> list[tuple[str, int]]:
        return await self.get_group_most_liked_tracks(limit=limit)

    async def toggle_track_like(
        self,
        user_id: int,
        track_id: str,
        track_name: str | None = None,
        artist_name: str | None = None,
    ) -> bool:
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

                normalized_track_name = self._normalize_optional_text(track_name)
                normalized_artist_name = self._normalize_optional_text(artist_name)

                if normalized_track_name is None or normalized_artist_name is None:
                    play_stmt = (
                        select(TrackPlay.track_name, TrackPlay.artist_name)
                        .where(TrackPlay.track_id == track_id)
                        .order_by(TrackPlay.played_at.desc())
                        .limit(1)
                    )
                    last_play = db.execute(play_stmt).first()
                    if last_play:
                        if normalized_track_name is None:
                            normalized_track_name = self._normalize_optional_text(last_play[0])
                        if normalized_artist_name is None:
                            normalized_artist_name = self._normalize_optional_text(last_play[1])

                db.add(
                    TrackLike(
                        user_id=user_id,
                        track_id=track_id,
                        track_name=normalized_track_name,
                        artist_name=normalized_artist_name,
                        liked=1,
                    )
                )
                db.commit()
                return True
            except IntegrityError:
                db.rollback()

        return await self.is_track_liked(user_id, track_id)


likes_service = LikesService()
