import io
import json
import logging
import subprocess
import threading
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from time import time, sleep
from zoneinfo import ZoneInfo

import requests
import telebot
from telebot import apihelper
from telebot.apihelper import ApiTelegramException
from telebot.types import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup
from sqlalchemy import func
from sqlalchemy import text as sql_text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

apihelper.API_URL = "https://tapi.bale.ai/bot{0}/{1}"
apihelper.FILE_URL = "https://tapi.bale.ai/file/bot{0}/{1}"


def _request_sender(method, url, params=None, files=None, timeout=None, proxies=None):
    # telebot puts payload in `params` (URL query) — large messages trigger nginx 414.
    # POST JSON body instead; files stay multipart. telebot also JSON-stringifies
    # nested objects (reply_markup, reply_parameters) — decode them for the body.
    if files:
        return requests.request(method, url, data=params, files=files, timeout=timeout, proxies=proxies)
    if method.upper() == "POST":
        payload = dict(params or {})
        for key, value in payload.items():
            if isinstance(value, str) and value[:1] in "[{":
                try:
                    payload[key] = json.loads(value)
                except ValueError:
                    pass
        return requests.request(method, url, json=payload, timeout=timeout, proxies=proxies)
    return requests.request(method, url, params=params, timeout=timeout, proxies=proxies)


apihelper.CUSTOM_REQUEST_SENDER = _request_sender

from app.config import REQUIRED_CHANNELS, settings
from app.database import SessionLocal
from app.models import BotState, Search, User, Word
from app.scraper import scrape_word

logger = logging.getLogger(__name__)

bot = telebot.TeleBot(settings.BOT_TOKEN, threaded=True, num_threads=16)
logger.info("Bot ready")

user_timestamps: dict[int, list[float]] = defaultdict(list)
_rate_lock = threading.Lock()
MAX_REQUESTS = 5
RATE_WINDOW = 60

_member_cache: dict[int, tuple[float, dict[str, bool]]] = {}
_member_lock = threading.Lock()
MEMBER_TTL = 60


def _esc(s: object) -> str:
    t = str(s) if s is not None else ""
    return t.replace("\\", "\\\\").replace("*", "\\*").replace("_", "\\_").replace("`", "\\`").replace("[", "\\[")


def _footer(text: str) -> str:
    return f"{text}\n\n{settings.FOOTER}"


def _split_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            if current:
                parts.append(current.rstrip())
            current = line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current.rstrip())
    return parts


def _send_long(chat_id: int, text: str, reply_to: int | None = None, markup=None):
    parts = _split_message(text)
    for i, part in enumerate(parts):
        bot.send_message(
            chat_id,
            part,
            reply_to_message_id=reply_to,
            reply_markup=markup if i == 0 else None,
            parse_mode="Markdown",
        )


def _safe_answer(callback_id: str, text: str | None = None, show_alert: bool = False):
    try:
        bot.answer_callback_query(callback_id, text, show_alert=show_alert)
    except Exception:
        logger.debug("answer_callback_query failed", exc_info=True)


def _is_rate_limited(user_id: int) -> bool:
    now = time()
    with _rate_lock:
        user_timestamps[user_id] = [t for t in user_timestamps[user_id] if now - t < RATE_WINDOW]
        if len(user_timestamps[user_id]) >= MAX_REQUESTS:
            return True
        user_timestamps[user_id].append(now)
        if len(user_timestamps) > 3000:
            for uid, ts in list(user_timestamps.items()):
                if not [t for t in ts if now - t < RATE_WINDOW]:
                    user_timestamps.pop(uid, None)
    return False


JOIN_TEXT = (
    "🔒 برای استفاده از ربات ابتدا باید در این دو کانال عضو شوید:\n\n"
    "پس از عضویت، دوباره پیام بدهید."
)


def _membership_statuses(user_id: int) -> dict[str, bool]:
    now = time()
    with _member_lock:
        hit = _member_cache.get(user_id)
        if hit and hit[0] > now:
            return hit[1]
    statuses: dict[str, bool] = {}
    for chat_id in REQUIRED_CHANNELS:
        try:
            status = bot.get_chat_member(chat_id, user_id).status
            statuses[chat_id] = status in ("member", "administrator", "creator")
        except ApiTelegramException as e:
            # Bale returns 404 when the user simply isn't in the channel
            msg = str(e).lower()
            if getattr(e, "error_code", None) == 404 or "404" in msg or "not found" in msg or "no such group" in msg:
                logger.info("User %s not in channel %s (Bale 404)", user_id, chat_id)
            else:
                logger.warning("getChatMember failed: channel=%s user=%s err=%s", chat_id, user_id, e)
            statuses[chat_id] = False
        except Exception as e:
            logger.warning("getChatMember failed: channel=%s user=%s err=%s", chat_id, user_id, e, exc_info=True)
            statuses[chat_id] = False
    logger.info("Membership: user=%s %s", user_id, statuses)
    if all(statuses.values()):
        # Cache only fully-joined users so a freshly joined member isn't blocked for 60s
        with _member_lock:
            _member_cache[user_id] = (now + MEMBER_TTL, statuses)
            if len(_member_cache) > 5000:
                for uid, (exp, _) in list(_member_cache.items()):
                    if exp <= now:
                        _member_cache.pop(uid, None)
    else:
        with _member_lock:
            _member_cache.pop(user_id, None)
    return statuses


def _is_member(user_id: int) -> bool:
    return all(_membership_statuses(user_id).values())


def _join_markup(user_id: int) -> InlineKeyboardMarkup:
    statuses = _membership_statuses(user_id)
    rows = []
    for i, (chat_id, url) in enumerate(REQUIRED_CHANNELS.items(), 1):
        mark = "✅" if statuses.get(chat_id) else "❌"
        rows.append([InlineKeyboardButton(f"{mark} 📢 کانال {i}", url=url)])
    return InlineKeyboardMarkup(rows)


def _gate_message(message) -> bool:
    if _is_member(message.from_user.id):
        return True
    logger.info("Blocked non-member user=%s", message.from_user.id)
    bot.reply_to(message, _footer(JOIN_TEXT), reply_markup=_join_markup(message.from_user.id), parse_mode="Markdown")
    return False


def _gate_callback(callback) -> bool:
    if _is_member(callback.from_user.id):
        return True
    _safe_answer(callback.id, "ابتدا در کانال‌ها عضو شوید.", show_alert=True)
    _send_long(callback.message.chat.id, _footer(JOIN_TEXT), reply_to=callback.message.message_id, markup=_join_markup(callback.from_user.id))
    return False


BOT_STATUS_BTN = "📊 Bot Status"
MAINTENANCE_TEXT = "🔧 Bot is under maintenance. Please try again later."


def _admin_ids() -> set[int]:
    ids = set()
    for part in (settings.ADMIN_IDS or "").split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def _is_admin(user_id: int) -> bool:
    return user_id in _admin_ids()


def _maintenance_on() -> bool:
    session = SessionLocal()
    try:
        row = session.query(BotState).filter(BotState.id == 1).first()
        return bool(row and row.maintenance)
    finally:
        session.close()


def _set_maintenance(on: bool) -> None:
    session = SessionLocal()
    try:
        row = session.query(BotState).filter(BotState.id == 1).first()
        if not row:
            row = BotState(id=1, maintenance=on)
            session.add(row)
        else:
            row.maintenance = on
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _tz() -> ZoneInfo:
    return ZoneInfo(settings.SUMMARY_TZ)


def _local_date():
    return datetime.now(_tz()).date()


def _today_start() -> datetime:
    now_local = datetime.now(_tz())
    midnight_local = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight_local.astimezone(timezone.utc).replace(tzinfo=None)


def _bump_counter(kind: str) -> None:
    col = "db_hits" if kind == "db" else "fastdic_requests"
    other = "fastdic_requests" if kind == "db" else "db_hits"
    try:
        session = SessionLocal()
        try:
            session.execute(
                sql_text(
                    f"UPDATE bot_state SET "
                    f"{col} = CASE WHEN stats_date = :today THEN {col} + 1 ELSE 1 END, "
                    f"{other} = CASE WHEN stats_date = :today THEN {other} ELSE 0 END, "
                    f"stats_date = :today WHERE id = 1"
                ),
                {"today": _local_date()},
            )
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.debug("counter bump failed", exc_info=True)


def _reset_counters() -> None:
    session = SessionLocal()
    try:
        session.execute(
            sql_text("UPDATE bot_state SET fastdic_requests = 0, db_hits = 0, stats_date = :d WHERE id = 1"),
            {"d": _local_date()},
        )
        session.commit()
    finally:
        session.close()


def _track_user(tg) -> None:
    session = SessionLocal()
    try:
        try:
            user = session.query(User).filter(User.telegram_id == tg.id).first()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            if not user:
                user = User(telegram_id=tg.id, username=tg.username, first_name=tg.first_name, last_name=tg.last_name)
                session.add(user)
            else:
                user.username = tg.username
                user.first_name = tg.first_name
                user.last_name = tg.last_name
            user.last_searched_at = now
            session.commit()
        except IntegrityError:
            session.rollback()
            user = session.query(User).filter(User.telegram_id == tg.id).first()
            if user:
                user.last_searched_at = datetime.now(timezone.utc).replace(tzinfo=None)
                session.commit()
    except Exception:
        session.rollback()
        logger.exception("Failed to track user")
    finally:
        session.close()


def _admin_stats_text() -> tuple[str, bool]:
    session = SessionLocal()
    try:
        today = _today_start()
        all_users = session.query(func.count(User.id)).scalar() or 0
        today_users = session.query(func.count(User.id)).filter(User.last_searched_at >= today).scalar() or 0
        new_today = session.query(func.count(User.id)).filter(User.created_at >= today).scalar() or 0
        total_searches = session.query(func.count(Search.id)).scalar() or 0
        today_searches = session.query(func.count(Search.id)).filter(Search.searched_at >= today).scalar() or 0
        row = session.query(BotState).filter(BotState.id == 1).first()
        on = bool(row and row.maintenance)
        fastdic = row.fastdic_requests if row and row.fastdic_requests else 0
        dbhits = row.db_hits if row and row.db_hits else 0
        cdate = row.stats_date.isoformat() if row and row.stats_date else "—"
        stale = cdate != _local_date().isoformat()
    finally:
        session.close()
    state = "🔴 Offline" if on else "🟢 Online"
    day_note = f" ({cdate})" if stale else ""
    text = (
        f"*📊 Bot Status:* {state}\n\n"
        f"👥 All users: *{all_users}*\n"
        f"📅 Active today: *{today_users}*\n"
        f"🆕 New today: *{new_today}*\n"
        f"🔍 Total searches: *{total_searches}*\n"
        f"🔍 Searches today: *{today_searches}*\n"
        f"🌐 Fastdic requests today{day_note}: *{fastdic}*\n"
        f"💾 DB cache hits today{day_note}: *{dbhits}*"
    )
    return text, on


def _admin_toggle_markup(maintenance_on: bool) -> InlineKeyboardMarkup:
    if maintenance_on:
        btn = InlineKeyboardButton("🟢 Turn ON", callback_data="admin:on")
    else:
        btn = InlineKeyboardButton("🔴 Turn OFF", callback_data="admin:off")
    return InlineKeyboardMarkup([[btn]])


def _get_or_create_user(session: Session, message) -> User:
    tg = message.from_user
    user = session.query(User).filter(User.telegram_id == tg.id).first()
    if not user:
        user = User(telegram_id=tg.id, username=tg.username, first_name=tg.first_name, last_name=tg.last_name)
        session.add(user)
        session.flush()
    else:
        user.username = tg.username
        user.first_name = tg.first_name
        user.last_name = tg.last_name
    user.last_searched_at = datetime.now(timezone.utc)
    return user


def _get_word(session: Session, word: str, direction: str) -> Word | None:
    return session.query(Word).filter(Word.word == word, Word.direction == direction).first()


def _save_word(session: Session, data: dict) -> Word:
    word = Word(
        word=data["word"],
        direction=data["direction"],
        phonetic=data.get("phonetic"),
        audio_url_us=data.get("audio_url_us"),
        audio_url_uk=data.get("audio_url_uk"),
        meanings=data["meanings"],
        idioms=data.get("idioms", []),
        verb_forms=data.get("verb_forms", {}),
        synonyms_antonyms=data.get("synonyms_antonyms", []),
        phrasal_verbs=data.get("phrasal_verbs", []),
        collocations=data.get("collocations", []),
        related_words=data.get("related_words", []),
        faq=data.get("faq", []),
    )
    session.add(word)
    session.flush()
    return word


HINT = "💡 برای اطلاعات بیشتر از دکمه‌های زیر استفاده کنید."


def _format_reply(word: Word) -> str:
    phonetic_part = f" {_esc(word.phonetic)}" if word.phonetic else ""
    lines = [f"📖 *{_esc(word.word.upper())}*{phonetic_part}\n"]

    for i, m in enumerate(word.meanings, 1):
        lines.append(f"*{i}.* {_esc(m.get('text', ''))}")
        lines.append("")

    lines.append(f"\n\n*{HINT}*")
    return "\n".join(lines)


def _build_keyboard(word: Word) -> InlineKeyboardMarkup | None:
    buttons = []

    if word.audio_url_us or word.audio_url_uk:
        row = []
        if word.audio_url_us:
            row.append(InlineKeyboardButton("🇺🇸 تلفظ امریکایی", callback_data=f"audio:{word.id}:us"))
        if word.audio_url_uk:
            row.append(InlineKeyboardButton("🇬🇧 تلفظ بریتیش", callback_data=f"audio:{word.id}:uk"))
        buttons.append(row)

    data_buttons = []
    has_examples = any(m.get("examples") for m in word.meanings)
    if has_examples:
        data_buttons.append(InlineKeyboardButton("💬 نمونه جمله", callback_data=f"examples:{word.id}"))
    if word.phrasal_verbs:
        data_buttons.append(InlineKeyboardButton("🔗 افعال چند بخشی", callback_data=f"phrasal:{word.id}"))
    if word.collocations:
        data_buttons.append(InlineKeyboardButton("📝 ترکیب‌های رایج", callback_data=f"colloc:{word.id}"))
    if word.idioms:
        data_buttons.append(InlineKeyboardButton("📚 اصطلاح", callback_data=f"idioms:{word.id}"))
    if any(g.get("synonyms") for g in word.synonyms_antonyms):
        data_buttons.append(InlineKeyboardButton("🔄 مترادف‌ها", callback_data=f"syn:{word.id}"))
    if any(g.get("antonyms") for g in word.synonyms_antonyms):
        data_buttons.append(InlineKeyboardButton("↔️ متضادها", callback_data=f"ant:{word.id}"))
    if word.related_words:
        data_buttons.append(InlineKeyboardButton("👨‍👩‍👧 لغت خانواده", callback_data=f"related:{word.id}"))
    if word.faq:
        data_buttons.append(InlineKeyboardButton(f"❓ سوالات رایج درمورد {word.word}", callback_data=f"faq:{word.id}"))
    if word.verb_forms:
        data_buttons.append(InlineKeyboardButton("🔤 صرف فعل", callback_data=f"forms:{word.id}"))

    for i in range(0, len(data_buttons), 2):
        buttons.append(data_buttons[i:i + 2])

    if not buttons:
        return None

    return InlineKeyboardMarkup(buttons)


def _send_section(chat_id: int, word: Word, title: str, lines: list[str], message_id: int | None = None):
    lines.append(f"\n\n*{HINT}*")
    text = _footer("\n".join(lines))
    _send_long(chat_id, text, reply_to=message_id, markup=_build_keyboard(word))


def _section_title(word: Word, suffix: str) -> str:
    return f"📖 *{_esc(word.word.upper())} — {suffix}*\n"


def _maintenance_callback(callback) -> bool:
    if _maintenance_on() and not _is_admin(callback.from_user.id):
        _safe_answer(callback.id, "🔧 Bot is under maintenance.", show_alert=True)
        return True
    return False


@bot.callback_query_handler(func=lambda c: c.data in ("admin:on", "admin:off"))
def handle_admin_toggle(callback):
    logger.info("Admin toggle: data=%s user=%s msg_id=%s", callback.data, callback.from_user.id, callback.message.message_id if callback.message else None)
    if not _is_admin(callback.from_user.id):
        _safe_answer(callback.id, "Not allowed.")
        return
    maintenance = callback.data == "admin:off"
    try:
        _set_maintenance(maintenance)
    except Exception:
        logger.exception("Failed to set maintenance mode")
        _safe_answer(callback.id, "Failed to update.")
        return
    _safe_answer(callback.id)
    try:
        text, maintenance_on = _admin_stats_text()
        bot.edit_message_text(
            _footer(text),
            callback.message.chat.id,
            callback.message.message_id,
            reply_markup=_admin_toggle_markup(maintenance_on),
            parse_mode="Markdown",
        )
        logger.info("Admin toggle edited msg to maintenance=%s", maintenance_on)
    except Exception:
        logger.exception("Failed to edit admin status message")


def _handle_section(callback, suffix: str, build_lines) -> None:
    if _maintenance_callback(callback):
        return
    if not _gate_callback(callback):
        return
    try:
        word_id = int(callback.data.split(":")[1])
    except (ValueError, IndexError):
        _safe_answer(callback.id, "Invalid request.")
        return
    logger.info("%s callback: word_id=%s user=%s", suffix, word_id, callback.from_user.id)
    session = SessionLocal()
    try:
        word = session.query(Word).filter(Word.id == word_id).first()
        if not word:
            _safe_answer(callback.id, "Word not found.")
            return
        lines = [_section_title(word, suffix)]
        lines.extend(build_lines(word))
        _safe_answer(callback.id)
        _send_section(callback.message.chat.id, word, suffix, lines, callback.message.message_id)
    except Exception:
        logger.exception("Section callback failed: %s", suffix)
        _safe_answer(callback.id, "Failed to load section.")
    finally:
        session.close()


@bot.callback_query_handler(func=lambda c: c.data.startswith("audio:"))
def handle_pronunciation(callback):
    if _maintenance_callback(callback):
        return
    if not _gate_callback(callback):
        return
    try:
        _, word_id, accent = callback.data.split(":")
        word_id = int(word_id)
    except (ValueError, IndexError):
        _safe_answer(callback.id, "Invalid request.")
        return
    if accent not in ("us", "uk"):
        _safe_answer(callback.id, "Invalid request.")
        return
    logger.info("Pronunciation callback: word_id=%s accent=%s user=%s", word_id, accent, callback.from_user.id)

    session = SessionLocal()
    try:
        word = session.query(Word).filter(Word.id == word_id).first()
        if not word:
            _safe_answer(callback.id, "Word not found.")
            return
        word_text = word.word
        cached_file_id = word.voice_file_id_us if accent == "us" else word.voice_file_id_uk
        url = word.audio_url_us if accent == "us" else word.audio_url_uk
    finally:
        session.close()

    accent_label = "🇺🇸 American" if accent == "us" else "🇬🇧 British"
    caption = f"{word_text} - {accent_label}\n\n{settings.FOOTER}"

    if cached_file_id:
        logger.info("Sending cached voice: word=%s accent=%s", word_text, accent)
        try:
            bot.send_voice(callback.message.chat.id, voice=cached_file_id, caption=caption, reply_to_message_id=callback.message.message_id)
        except Exception:
            logger.exception("Failed to send cached voice")
            _safe_answer(callback.id, "Failed to send audio.")
            return
        _safe_answer(callback.id)
        return

    if not url:
        _safe_answer(callback.id, "Audio not available.")
        return

    logger.info("Downloading audio from %s", url)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
    except Exception:
        logger.exception("Audio download failed")
        _safe_answer(callback.id, "Failed to send audio.")
        return

    try:
        ogg_bytes = subprocess.run(
            ["ffmpeg", "-i", "pipe:0", "-f", "ogg", "-c:a", "libopus", "-b:a", "48k", "-ar", "48000", "pipe:1"],
            input=resp.content,
            capture_output=True,
            timeout=30,
        ).stdout
    except Exception:
        logger.exception("ffmpeg failed")
        _safe_answer(callback.id, "Failed to process audio.")
        return

    if not ogg_bytes:
        _safe_answer(callback.id, "Failed to process audio.")
        return

    voice_file = io.BytesIO(ogg_bytes)
    voice_file.name = "voice.ogg"
    try:
        msg = bot.send_voice(callback.message.chat.id, voice=voice_file, caption=caption, reply_to_message_id=callback.message.message_id)
    except Exception:
        logger.exception("Failed to send audio")
        _safe_answer(callback.id, "Failed to send audio.")
        return
    _safe_answer(callback.id)

    if msg.voice:
        session = SessionLocal()
        try:
            word = session.query(Word).filter(Word.id == word_id).first()
            if word:
                if accent == "us":
                    word.voice_file_id_us = msg.voice.file_id
                else:
                    word.voice_file_id_uk = msg.voice.file_id
                session.commit()
        except Exception:
            session.rollback()
            logger.exception("Failed to cache voice file_id")
        finally:
            session.close()


@bot.callback_query_handler(func=lambda c: c.data.startswith("examples:"))
def handle_examples(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for i, m in enumerate(word.meanings, 1):
            examples = m.get("examples", []) or []
            if not examples:
                continue
            lines.append(f"*{i}.* {_esc(m.get('text', ''))}")
            for ex in examples[:3]:
                lines.append(f"  • {_esc(ex.get('english', ''))}")
                lines.append(f"    {_esc(ex.get('persian', ''))}")
            lines.append("\n————————————\n")
        return lines

    _handle_section(callback, "نمونه جمله‌ها", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("idioms:"))
def handle_idioms(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for i, idiom in enumerate(word.idioms[:10], 1):
            lines.append(f"*{i}.* {_esc(idiom.get('phrase', ''))}")
            for meaning in idiom.get("meanings", []) or []:
                lines.append(f"  {_esc(meaning)}")
            lines.append("")
        return lines

    _handle_section(callback, "Idioms", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("phrasal:"))
def handle_phrasal(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for i, pv in enumerate(word.phrasal_verbs[:10], 1):
            lines.append(f"*{i}.* {_esc(pv.get('phrase', ''))}")
            for meaning in pv.get("meanings", []) or []:
                lines.append(f"  {_esc(meaning)}")
            lines.append("")
        return lines

    _handle_section(callback, "Phrasal Verbs", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("colloc:"))
def handle_collocations(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for i, col in enumerate(word.collocations[:10], 1):
            lines.append(f"*{i}.* {_esc(col.get('phrase', ''))}")
            for meaning in col.get("meanings", []) or []:
                lines.append(f"  {_esc(meaning)}")
            lines.append("")
        return lines

    _handle_section(callback, "Collocations", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("syn:"))
def handle_synonyms(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for i, group in enumerate([g for g in word.synonyms_antonyms if g.get("synonyms")], 1):
            lines.append(f"*{i}.* {_esc(group.get('pos', ''))} — {_esc(group.get('definition', ''))}")
            lines.append(f"  {_esc(', '.join(group.get('synonyms', [])[:10]))}")
            lines.append("")
        return lines

    _handle_section(callback, "Synonyms", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("ant:"))
def handle_antonyms(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for i, group in enumerate([g for g in word.synonyms_antonyms if g.get("antonyms")], 1):
            lines.append(f"*{i}.* {_esc(group.get('pos', ''))} — {_esc(group.get('definition', ''))}")
            lines.append(f"  {_esc(', '.join(group.get('antonyms', [])[:10]))}")
            lines.append("")
        return lines

    _handle_section(callback, "Antonyms", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("related:"))
def handle_related(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for group in word.related_words or []:
            lines.append(f"*{_esc(group.get('pos', ''))}:* {_esc(', '.join(group.get('words', [])))}")
        return lines

    _handle_section(callback, "Related Words", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("forms:"))
def handle_forms(callback):
    def build(word: Word) -> list[str]:
        return [f"*{_esc(label)}:* {_esc(value)}" for label, value in (word.verb_forms or {}).items()]

    _handle_section(callback, "صرف فعل", build)


@bot.callback_query_handler(func=lambda c: c.data.startswith("faq:"))
def handle_faq(callback):
    def build(word: Word) -> list[str]:
        lines = []
        for item in word.faq or []:
            lines.append(f"❓ {_esc(item.get('question', ''))}")
            lines.append(f"  ✅ {_esc(item.get('answer', ''))}\n")
        return lines

    _handle_section(callback, "FAQ", build)


START_TEXT = (
    "👋 سلام! به دیکشنری انگلیسی خوش اومدی.\n\n"
    "🔎 فقط کافیه یک کلمه یا عبارت انگلیسی یا فارسی بفرستی تا معنی و اطلاعات کاملش رو برات بیارم.\n\n"
    "📖 برای هر کلمه می‌تونی ببینی:\n"
    "• معنی‌ها و ترجمه‌ها\n"
    "• تلفظ آمریکایی و بریتیش 🇺🇸🇬🇧\n"
    "• مثال‌های کاربردی در جمله\n\n"
    "💡 علاوه بر این، اطلاعات مفید دیگه‌ای هم در اختیارت می‌ذارم:\n"
    "• Phrasal Verbs — افعال چندبخشی\n"
    "• Collocations — ترکیب‌های رایج\n"
    "• Idioms — اصطلاحات\n"
    "• Synonyms & Antonyms — مترادف و متضاد\n"
    "• Word Family — خانواده کلمات\n"
    "• Common Questions — سوالات رایج\n\n"
    "✍️ حالا یک کلمه بفرست تا شروع کنیم!"
)


@bot.message_handler(commands=["start"])
def handle_start(message):
    logger.info("/start from user=%s", message.from_user.id)
    _track_user(message.from_user)
    if _maintenance_on() and not _is_admin(message.from_user.id):
        bot.reply_to(message, _footer(MAINTENANCE_TEXT), parse_mode="Markdown")
        return
    if not _gate_message(message):
        return
    if _is_admin(message.from_user.id):
        kb = ReplyKeyboardMarkup(resize_keyboard=True)
        kb.add(KeyboardButton(BOT_STATUS_BTN))
        bot.reply_to(message, _footer(START_TEXT), reply_markup=kb, parse_mode="Markdown")
    else:
        bot.reply_to(message, _footer(START_TEXT), parse_mode="Markdown")


@bot.message_handler(func=lambda m: True)
def handle_word(message):
    raw = message.text or ""
    text = raw.strip()
    logger.info("Search request: '%s' from user=%s", text, message.from_user.id)
    if not text:
        bot.reply_to(message, _footer("لطفاً یک کلمه یا عبارت متنی بفرستید."), parse_mode="Markdown")
        return
    _track_user(message.from_user)
    if text == BOT_STATUS_BTN and _is_admin(message.from_user.id):
        try:
            stats, maintenance_on = _admin_stats_text()
            _send_long(message.chat.id, _footer(stats), reply_to=message.message_id, markup=_admin_toggle_markup(maintenance_on))
        except Exception:
            logger.exception("Failed to send admin stats")
        return
    if _maintenance_on() and not _is_admin(message.from_user.id):
        bot.reply_to(message, _footer(MAINTENANCE_TEXT), parse_mode="Markdown")
        return
    if not _gate_message(message):
        return
    if len(text) > 100:
        bot.reply_to(message, _footer("❌ Please send a valid word (max 100 characters)."), parse_mode="Markdown")
        return

    if _is_rate_limited(message.from_user.id):
        with _rate_lock:
            first = user_timestamps[message.from_user.id][0] if user_timestamps.get(message.from_user.id) else time()
        remaining = RATE_WINDOW - (time() - first)
        bot.reply_to(message, _footer(f"🚫 Rate limit reached. Try again in {int(remaining)} seconds."), parse_mode="Markdown")
        return

    direction = "fa-en" if any("\u0600" <= ch <= "\u06FF" for ch in text) else "en-fa"
    searching_msg = bot.reply_to(message, _footer(f"🔍 Searching for *{_esc(text)}*..."), parse_mode="Markdown")

    session = SessionLocal()
    try:
        user = _get_or_create_user(session, message)
        session.commit()
        user_id = user.id
        word = _get_word(session, text, direction)
        if word:
            logger.info("Cache hit for '%s' (id=%s)", text, word.id)
            _bump_counter("db")
            session.add(Search(user_id=user_id, word_id=word.id))
            session.commit()
            reply = _format_reply(word)
            markup = _build_keyboard(word)
            try:
                bot.edit_message_text(_footer(reply), searching_msg.chat.id, searching_msg.message_id, reply_markup=markup, parse_mode="Markdown")
            except Exception:
                logger.exception("Failed to edit reply message")
            logger.info("Reply sent for '%s' to user=%s", text, message.from_user.id)
            return
        session.commit()
    except Exception:
        session.rollback()
        logger.exception("DB error on cache lookup")
    finally:
        session.close()

    logger.info("Cache miss for '%s' (%s), scraping...", text, direction)
    _bump_counter("fast")
    try:
        data = scrape_word(text)
    except Exception:
        logger.exception("Scrape failed for %s", text)
        try:
            bot.edit_message_text(_footer("⚠️ Failed to look up the word. Try again later."), searching_msg.chat.id, searching_msg.message_id, parse_mode="Markdown")
        except Exception:
            logger.exception("Failed to edit error message")
        return

    if not data:
        try:
            bot.edit_message_text(_footer(f"🔍 Could not find *{_esc(text)}* in the dictionary."), searching_msg.chat.id, searching_msg.message_id, parse_mode="Markdown")
        except Exception:
            logger.exception("Failed to edit not-found message")
        return

    session = SessionLocal()
    try:
        try:
            word = _save_word(session, data)
            session.flush()
        except IntegrityError:
            session.rollback()
            logger.info("Concurrent insert for '%s', re-querying", text)
            word = _get_word(session, text, direction)
            if word is None:
                try:
                    bot.edit_message_text(_footer("⚠️ An error occurred. Try again."), searching_msg.chat.id, searching_msg.message_id, parse_mode="Markdown")
                except Exception:
                    logger.exception("Failed to edit error message")
                return
        session.add(Search(user_id=user_id, word_id=word.id))
        session.commit()

        reply = _format_reply(word)
        markup = _build_keyboard(word)
        try:
            bot.edit_message_text(_footer(reply), searching_msg.chat.id, searching_msg.message_id, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            logger.exception("Failed to edit reply message")
        logger.info("Reply sent for '%s' to user=%s", text, message.from_user.id)
    except Exception:
        session.rollback()
        logger.exception("DB error on save")
        try:
            bot.edit_message_text(_footer("⚠️ An error occurred. Try again."), searching_msg.chat.id, searching_msg.message_id, parse_mode="Markdown")
        except Exception:
            logger.exception("Failed to edit error message")
    finally:
        session.close()


def _midnight_summary_loop() -> None:
    # ponytail: process-local timer — missed only if the bot is down at midnight
    while True:
        now = datetime.now(_tz())
        next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep(max((next_midnight - now).total_seconds(), 1) + 5)
        try:
            stats, maintenance_on = _admin_stats_text()
            summary = f"*🕛 Daily Summary*\n\n{stats}"
            for admin_id in _admin_ids():
                try:
                    bot.send_message(admin_id, summary, reply_markup=_admin_toggle_markup(maintenance_on), parse_mode="Markdown")
                except Exception:
                    logger.exception("Failed to send daily summary to admin %s", admin_id)
            _reset_counters()
            logger.info("Daily summary sent to %s admins, counters reset", len(_admin_ids()))
        except Exception:
            logger.exception("Daily summary failed")


threading.Thread(target=_midnight_summary_loop, daemon=True).start()
