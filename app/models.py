from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    username: Mapped[str | None] = mapped_column(String, nullable=True)
    first_name: Mapped[str | None] = mapped_column(String, nullable=True)
    last_name: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_searched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    searches: Mapped[list["Search"]] = relationship(back_populates="user")


class Word(Base):
    __tablename__ = "words"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    word: Mapped[str] = mapped_column(String, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    phonetic: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_url_us: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_url_uk: Mapped[str | None] = mapped_column(String, nullable=True)
    voice_file_id_us: Mapped[str | None] = mapped_column(String, nullable=True)
    voice_file_id_uk: Mapped[str | None] = mapped_column(String, nullable=True)
    meanings: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    idioms: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    verb_forms: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    synonyms_antonyms: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    phrasal_verbs: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    collocations: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    related_words: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    faq: Mapped[list[dict]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (UniqueConstraint("word", "direction", name="uq_word_direction"),)

    searches: Mapped[list["Search"]] = relationship(back_populates="word")


class Search(Base):
    __tablename__ = "searches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    word_id: Mapped[int] = mapped_column(Integer, ForeignKey("words.id"), nullable=False)
    searched_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="searches")
    word: Mapped["Word"] = relationship(back_populates="searches")


class BotState(Base):
    __tablename__ = "bot_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
