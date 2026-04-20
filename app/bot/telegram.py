from __future__ import annotations
import asyncio
import html
import logging
import random
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import text

from aiogram import Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import (
    Message,
    CallbackQuery,
    InlineQuery,
    InlineQueryResultPhoto,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
)
from app.bot.intent import detect_intent
from app.core.runtime import allow
from app.db.database import SessionLocal
from app.services.likes import likes_service
from app.services.enrichment import enrich_track_if_missing
from app.services.spotify import spotify_service

logger = logging.getLogger(__name__)

bot_dispatcher: Dispatcher = Dispatcher()
SAO_PAULO_TZ = ZoneInfo("America/Sao_Paulo")
BLOCKED_WORDS = ["palavra1", "palavra2"]


def _normalize_optional_text(value: object) -> str | None:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or None

    if value is None:
        return None

    try:
        cleaned = str(value).strip()
    except Exception:
        return None
    return cleaned or None

async def _handle_spotify_error(message: Message, exc: Exception) -> None:
    logger.exception("Telegram command failed", exc_info=exc)
    await message.answer(
        "Spotify is temporarily unavailable right now. Please try again in a few seconds."
    )


def _telegram_identity(message: Message) -> str:
    user = message.from_user
    if not user:
        return "unknown"

    if user.username:
        return f"@{user.username}"

    return user.full_name


def _format_play_status(track: dict[str, str | None], user_label: str) -> str:
    source = str(track.get("source") or "")
    if source == "current":
        return f"🎹 {html.escape(user_label)} está ouvindo"

    played_at_raw = track.get("played_at")
    formatted_time = "--h--min"
    if isinstance(played_at_raw, str):
        try:
            parsed = datetime.fromisoformat(played_at_raw.replace("Z", "+00:00"))
            formatted_time = parsed.astimezone(SAO_PAULO_TZ).strftime("%Hh%Mmin")
        except ValueError:
            formatted_time = "--h--min"

    return f"🎹 {html.escape(user_label)} ouviu às {formatted_time}"


def _play_caption(
    status_line: str,
    spotify_url: str | None,
    track_name: str,
    artist: str,
) -> str:
    escaped_track = html.escape(track_name)
    if spotify_url:
        track_text = f"<a href=\"{html.escape(spotify_url)}\"><b>{escaped_track}</b></a>"
    else:
        track_text = f"<b>{escaped_track}</b>"

    return (
        f"{status_line}\n"
        f"🎧 {track_text} - "
        f"<i>{html.escape(artist)}</i>"
    )


def _playing_keyboard(track_id: str, total_plays: int, total_likes: int, liked: bool) -> InlineKeyboardMarkup:
    heart = "♥" if liked else "♡"
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text=f"♫ {total_plays}",
                callback_data=f"plays:{track_id}"
            ),
            InlineKeyboardButton(
                text=f"{heart} {total_likes}",
                callback_data=f"like:{track_id}"
            )
        ]
    ])


def _register_handlers(dp: Dispatcher) -> None:

    # ========================
    # INLINE MODE
    # ========================
    @dp.inline_query()
    async def inline_play(query: InlineQuery) -> None:
        raw = (query.query or "").lower()
        text = raw.strip()

        if raw not in {"playing", "playing ", "mood", "mood "}:
            return

        user_id = query.from_user.id
        track = await spotify_service.get_current_or_last_played(user_id)
        if not track:
            return

        album_image_url = track.get("album_image_url")
        if not album_image_url:
            return

        if text == "playing":
            track_id = _normalize_optional_text(track.get("track_id"))
            if not track_id:
                return

            display_name = query.from_user.full_name
            plays = await likes_service.get_user_track_plays(user_id, track_id)
            track_name = str(track.get("track_name") or "")
            artist = str(track.get("artist") or "")
            caption = (
                f"{display_name} · ♪ {plays}\n"
                f"♫ {track_name} — {artist}"
            )

            result = InlineQueryResultPhoto(
                id=str(uuid.uuid4()),
                photo_url=album_image_url,
                thumbnail_url=album_image_url,
                caption=caption,
            )

            await query.answer([result], cache_time=1)
            return

        def bar(value: float) -> str:
            filled = max(1, int(max(0, min(1, value)) * 5))
            return "▰" * filled + "▱" * (5 - filled)

        valence = float(track.get("valence")) if track.get("valence") is not None else 0.5
        energy = float(track.get("energy")) if track.get("energy") is not None else 0.5
        danceability = float(track.get("danceability")) if track.get("danceability") is not None else 0.5
        trend = str(track.get("trend") or "estável")

        if valence >= 0.75:
            diagnostic = random.choice(
                [
                    "vibe muito feliz",
                    "vibe em alta",
                    "vibe radiante",
                ]
            )
        elif valence >= 0.55:
            diagnostic = random.choice(
                [
                    "vibe boa",
                    "vibe de boa",
                    "vibe leve",
                ]
            )
        elif valence >= 0.40:
            diagnostic = random.choice(
                [
                    "vibe estável",
                    "vibe equilibrada",
                ]
            )
        elif valence >= 0.25:
            diagnostic = random.choice(
                [
                    "vibe pensativa",
                    "vibe mais introspectiva",
                ]
            )
        else:
            diagnostic = random.choice(
                [
                    "vibe introspectiva",
                    "vibe reflexiva",
                ]
            )

        track_name = str(track.get("track_name") or "")
        artist = str(track.get("artist") or "")
        caption = (
            f"♫ {track_name} — {artist}\n\n"
            "♩ Mood\n\n"
            f"☻ {bar(valence)}  humor\n"
            f"ϟ {bar(energy)}  energia\n"
            f"✶ {bar(danceability)}  ritmo\n\n"
            f"≡ {diagnostic}\n"
            f"↗ {trend}"
        )

        result = InlineQueryResultPhoto(
            id=str(uuid.uuid4()),
            photo_url=album_image_url,
            thumbnail_url=album_image_url,
            caption=caption,
        )

        await query.answer([result], cache_time=1)

    # ========================
    # COMMANDS
    # ========================

    @dp.message(Command("start"))
    async def start(message: Message) -> None:
        start_text = (
            "♫ ♥ Bem-vindo ao tigraoRADIO\n\n"
            "Conecte sua conta e acompanhe o que você está ouvindo no Spotify.\n\n"
            "Comandos principais:\n"
            "/playing — mostrar música atual\n"
            "/mood — analisar o clima da faixa atual\n"
            "/myself — ver seu perfil musical\n"
            "/songcharts — ver ranking do grupo\n\n"
            "Conexão:\n"
            "/login — conectar Spotify\n"
            "/logout — desconectar conta\n\n"
            "Interações:\n"
            "Use os botões das mensagens de /playing para ver plays e curtir músicas"
        )
        if message.chat.type == "private":
            await message.answer(start_text)
            return

        await message.answer(start_text)

    @dp.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "COMANDOS\n\n"
            "♫ /playing\n"
            "Mostra a música que você está ouvindo agora ou a última música encontrada no Spotify.\n\n"
            "♩ /mood\n"
            "Analisa a faixa atual com base em humor, energia e ritmo.\n\n"
            "★ /myself\n"
            "Mostra seu perfil musical com top músicas, top artistas e total de curtidas.\n\n"
            "≡ /songcharts\n"
            "Mostra o ranking do grupo com músicas, artistas e faixas mais curtidas.\n\n"
            "↻ /login\n"
            "Conecte sua conta do Spotify.\n\n"
            "⨯ /logout\n"
            "Desconecte sua conta.\n\n"
            "♫♩Gatilhos de texto\n"
            "Também acionam a lógica do /playing:\n"
            "tocando, kur, xxt, ts, cebrutius, tigraofm, djpi, royalfm, geeksfm, radinho, qap\n\n"
            "♥♡ Interações\n"
            "Nos posts de /playing:\n\n"
            "* botão ♫ mostra quantas vezes você ouviu a faixa\n"
            "* botão ♥ alterna curtida"
        )

    @dp.message(Command("login"))
    async def login(message: Message) -> None:
        if message.chat.type != "private":
            await message.answer("🔒 Use /login no privado para conectar seu Spotify.")
            return

        user_id = message.from_user.id if message.from_user else 0
        link = spotify_service.build_auth_url(user_id)
        await message.answer(f"Authorize Spotify access: {link}")

    @dp.message(Command("playing"))
    async def play(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        try:
            track = await spotify_service.get_current_or_last_played(user_id)
            if not track:
                await message.answer("Nada está tocando agora.")
                return

            track_id = _normalize_optional_text(track.get("track_id"))
            if not track_id:
                await message.answer("Erro ao identificar a música.")
                return
            track_name_raw = _normalize_optional_text(track.get("track_name"))
            artist_name_raw = _normalize_optional_text(track.get("artist"))

            track_url = str(track.get("spotify_url") or "")
            await likes_service.register_play(
                user_id,
                track_id,
                track_name=track_name_raw,
                artist_name=artist_name_raw,
            )

            total_plays = await likes_service.get_track_play_count(track_id)
            total_likes = await likes_service.get_total_likes(track_id)
            user_total_likes = await likes_service.get_user_total_likes(user_id)
            liked = await likes_service.is_track_liked(user_id, track_id)

            display_name = message.from_user.full_name if message.from_user else "Unknown"
            if message.from_user and message.from_user.username:
                user_link = f"https://t.me/{message.from_user.username}"
            else:
                user_link = f"tg://user?id={user_id}"

            track_name = track_name_raw or ""
            artist_name = artist_name_raw or ""

            track_name = html.escape(track_name)
            artist_name = html.escape(artist_name)
            display_name = html.escape(display_name)

            caption = (
                f"<b><a href=\"{html.escape(user_link)}\">{display_name}</a></b> · ♥ <code>{user_total_likes}</code>\n\n"
                f"♫ <b><a href=\"{html.escape(track_url)}\">{track_name}</a></b> — <i>{artist_name}</i>"
            )

            keyboard = _playing_keyboard(track_id, total_plays, total_likes, liked)

            album_image_url = track.get("album_image_url")
            if album_image_url:
                await message.answer_photo(
                    photo=str(album_image_url),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )
                asyncio.create_task(enrich_track_if_missing(track_id))
                return

            await message.answer(caption, parse_mode="HTML", reply_markup=keyboard)
            asyncio.create_task(enrich_track_if_missing(track_id))

        except Exception as exc:
            await _handle_spotify_error(message, exc)


    @dp.message(Command("mood"))
    async def mood(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0

        def bar(value: float) -> str:
            filled = max(1, int(max(0, min(1, value)) * 5))
            return "▰" * filled + "▱" * (5 - filled)

        try:
            track = await spotify_service.get_current_or_last_played(user_id)
            if not track:
                await message.answer("Nada está tocando agora.")
                return

            track_id = _normalize_optional_text(track.get("track_id"))
            if not track_id:
                await message.answer("Erro ao identificar a música.")
                return

            valence = None
            energy = None
            danceability = None
            history_count = 0
            trend = "estável"
            with SessionLocal() as db:
                enriched_rows = db.execute(
                    text(
                        """
                        SELECT t.track_id, f.valence, f.energy, f.danceability
                        FROM track_plays t
                        JOIN track_audio_features f ON t.track_id = f.track_id
                        WHERE t.user_id = :user_id
                        ORDER BY t.played_at DESC
                        LIMIT 20
                        """
                    ),
                    {"user_id": user_id},
                ).all()

                valid_rows = [
                    row
                    for row in enriched_rows
                    if row.valence is not None
                    and row.energy is not None
                    and row.danceability is not None
                ]
                history_count = len(valid_rows)

                if len(valid_rows) >= 3:
                    valence = sum(float(row.valence) for row in valid_rows) / len(valid_rows)
                    energy = sum(float(row.energy) for row in valid_rows) / len(valid_rows)
                    danceability = sum(float(row.danceability) for row in valid_rows) / len(valid_rows)

                    recent_energy = [float(row.energy) for row in valid_rows[:3]]
                    baseline_energy = [float(row.energy) for row in valid_rows[3:]]
                    threshold = 0.05

                    if baseline_energy:
                        recent_avg = sum(recent_energy) / len(recent_energy)
                        baseline_avg = sum(baseline_energy) / len(baseline_energy)
                        if recent_avg > baseline_avg + threshold:
                            trend = "subindo"
                        elif recent_avg < baseline_avg - threshold:
                            trend = "caindo"
                else:
                    row = db.execute(
                        text(
                            """
                            SELECT valence, energy, danceability
                            FROM track_audio_features
                            WHERE track_id = :track_id
                            """
                        ),
                        {"track_id": track_id},
                    ).first()

                    if row:
                        valence, energy, danceability = row

            if valence is None and energy is None and danceability is None and track_id:
                asyncio.create_task(enrich_track_if_missing(track_id))

            valence = float(valence) if valence is not None else 0.5
            energy = float(energy) if energy is not None else 0.5
            danceability = float(danceability) if danceability is not None else 0.5

            display_name = message.from_user.full_name if message.from_user else "Usuário"
            track_name = _normalize_optional_text(track.get("track_name")) or "Desconhecida"
            artist = _normalize_optional_text(track.get("artist")) or "Desconhecido"

            safe_name = html.escape(display_name)
            safe_track = html.escape(track_name)
            safe_artist = html.escape(artist)

            if message.from_user and message.from_user.username:
                profile_link = f"https://t.me/{message.from_user.username}"
            else:
                profile_link = f"tg://user?id={user_id}"
            user_link = f"<a href=\"{html.escape(profile_link)}\">{safe_name}</a>"
            score = await likes_service.get_user_total_likes(user_id)
            total_plays = await likes_service.get_track_play_count(track_id)
            total_likes = await likes_service.get_total_likes(track_id)
            liked = await likes_service.is_track_liked(user_id, track_id)
            keyboard = _playing_keyboard(track_id, total_plays, total_likes, liked)

            if valence >= 0.75:
                diagnostic = random.choice(
                    [
                        "Acho que {user} está muito feliz!",
                        "Acho que {user} está em ótima vibe!",
                        "Acho que {user} está radiante!",
                    ]
                )
            elif valence >= 0.55:
                diagnostic = random.choice(
                    [
                        "Acho que {user} está bem!",
                        "Acho que {user} está de boa!",
                        "Acho que {user} está numa boa vibe!",
                    ]
                )
            elif valence >= 0.40:
                diagnostic = random.choice(
                    [
                        "Acho que {user} está estável.",
                        "Acho que {user} está equilibrado.",
                    ]
                )
            elif valence >= 0.25:
                diagnostic = random.choice(
                    [
                        "Acho que {user} está pensativo.",
                        "Acho que {user} está mais na dele.",
                    ]
                )
            else:
                diagnostic = random.choice(
                    [
                        "Acho que {user} está introspectivo.",
                        "Acho que {user} está reflexivo.",
                    ]
                )

            diagnostic = diagnostic.format(user=user_link)

            caption = (
                f"{safe_name} · ♥ {score}\n"
                f"♫ {safe_track} — {safe_artist}\n\n"
                "♩ Mood\n\n"
                f"☻ {bar(valence)}  humor\n"
                f"ϟ {bar(energy)}  energia\n"
                f"✶ {bar(danceability)}  ritmo\n\n"
                f"≡ {diagnostic}\n"
                f"↗ {trend}"
            )

            album_image_url = track.get("album_image_url")
            if album_image_url:
                await message.answer_photo(
                    photo=str(album_image_url),
                    caption=caption,
                    parse_mode="HTML",
                )
            else:
                await message.answer(
                    caption,
                    parse_mode="HTML",
                    reply_markup=keyboard,
                )

        except Exception as exc:
            await _handle_spotify_error(message, exc)

    @dp.message(Command("myself"))
    async def handle_myself(message: Message):
        print("MYSELF HANDLER HIT")
        user_id = message.from_user.id
        display_name = message.from_user.full_name if message.from_user else "Usuário"
        safe_name = html.escape(display_name)
        header = f"<a href='tg://user?id={user_id}'>{safe_name}</a> · ♥ {{likes}} curtidas"

        print("CALLING SERVICE")
        total_likes = await likes_service.get_user_total_likes(user_id)
        top_tracks = await likes_service.get_user_top_tracks(user_id, limit=5)
        top_artists = await likes_service.get_user_top_artists(user_id, limit=5)

        if not top_tracks and not top_artists and total_likes == 0:
            await message.answer(
                f"{header.format(likes=total_likes)}\n\n"
                "Nenhum dado disponível ainda.",
                parse_mode="HTML",
            )
            return

        tracks_lines = ["♫ Músicas"]
        for index, (track_label, plays) in enumerate(top_tracks, start=1):
            tracks_lines.append(f"♫ {index}. {track_label} — {plays}")

        artists_lines = ["★ Artistas"]
        for index, (artist_name, plays) in enumerate(top_artists, start=1):
            artist_label = artist_name if artist_name else "Desconhecido"
            artists_lines.append(f"★ {index}. {artist_label} — {plays}")

        tracks_block = "\n".join(tracks_lines)
        artists_block = "\n".join(artists_lines)
        text = (
            f"{header.format(likes=total_likes)}\n\n"
            f"{tracks_block}\n\n"
            f"{artists_block}"
        )
        print("SENDING RESPONSE")
        await message.answer(text, parse_mode="HTML")

    @dp.message(Command("songcharts"))
    async def handle_songcharts(message: Message):
        print("SONGCHARTS HANDLER HIT")
        print("CALLING SERVICE")
        top_tracks = await likes_service.get_top_tracks(limit=5)
        top_artists = await likes_service.get_top_artists(limit=5)
        most_liked_tracks = await likes_service.get_most_liked_tracks(limit=5)

        if not top_tracks and not top_artists and not most_liked_tracks:
            await message.answer(
                "♫ Charts\n\n"
                "Nenhum dado disponível ainda."
            )
            return

        tracks_lines = ["♫ Músicas"]
        for index, (track_label, plays) in enumerate(top_tracks, start=1):
            tracks_lines.append(f"♫ {index}. {track_label} — {plays}")

        artists_lines = ["★ Artistas"]
        for index, (artist_name, plays) in enumerate(top_artists, start=1):
            artist_label = artist_name if artist_name else "Desconhecido"
            artists_lines.append(f"★ {index}. {artist_label} — {plays}")

        liked_lines = ["♥ Mais curtidas"]
        for index, (track_label, likes) in enumerate(most_liked_tracks, start=1):
            liked_lines.append(f"♥ {index}. {track_label} — {likes}")

        tracks_block = "\n".join(tracks_lines)
        artists_block = "\n".join(artists_lines)
        liked_block = "\n".join(liked_lines)
        text = (
            "♫ Ranking do grupo\n\n"
            f"{tracks_block}\n\n"
            f"{artists_block}\n\n"
            f"{liked_block}"
        )
        print("SENDING RESPONSE")
        await message.answer(text)

    @dp.message(Command("logout"))
    async def logout(message: Message) -> None:
        user_id = message.from_user.id if message.from_user else 0
        try:
            await spotify_service.clear_user_session(user_id)
            await message.answer(
                "🔌 Desconectado do Spotify.\n"
                "Use /login para conectar novamente."
            )
        except Exception as exc:
            await _handle_spotify_error(message, exc)

    @dp.message(F.text & ~F.text.startswith("/"))
    async def natural_handler(message: Message) -> None:
        if not message.text or not message.from_user:
            return

        text = message.text.strip().lower()

        if text in BLOCKED_WORDS:
            await message.answer("Mensagem não permitida.")
            return

        user_id = message.from_user.id

        if not allow(user_id):
            return

        intent = detect_intent(message.text)
        if intent == "play":
            await play(message)

    @dp.callback_query(lambda c: c.data and c.data.startswith("plays:"))
    async def playing_stats(callback: CallbackQuery) -> None:
        if not callback.data or ":" not in callback.data:
            await callback.answer()
            return

        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer()
            return

        track_id = parts[1]
        if not track_id:
            await callback.answer()
            return

        user_id = callback.from_user.id
        user_plays = await likes_service.get_user_play_count(user_id, track_id)
        vez = "vez" if user_plays == 1 else "vezes"
        try:
            await callback.answer(
                f"♫ Você já ouviu {user_plays} {vez}",
                show_alert=True
            )
        except TelegramBadRequest:
            pass

    @dp.callback_query(lambda c: c.data and c.data.startswith("like:"))
    async def like_track(callback: CallbackQuery) -> None:
        await callback.answer()

        if not callback.data or ":" not in callback.data:
            await callback.answer()
            return

        parts = callback.data.split(":", 1)
        if len(parts) < 2:
            await callback.answer()
            return

        track_id = parts[1]
        if not track_id:
            await callback.answer()
            return

        user_id = callback.from_user.id

        liked = await likes_service.toggle_track_like(user_id, track_id)
        total_likes = await likes_service.get_total_likes(track_id)
        total_plays = await likes_service.get_track_play_count(track_id)

        keyboard = _playing_keyboard(track_id, total_plays, total_likes, liked)
        try:
            await callback.message.edit_reply_markup(reply_markup=keyboard)
        except TelegramBadRequest:
            pass


async def shutdown_telegram_bot() -> None:
    return
