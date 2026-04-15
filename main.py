import base64
import hashlib
import os
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
from authlib.integrations.starlette_client import OAuth
from dateutil import parser as date_parser
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    or_,
    text,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from starlette.middleware.sessions import SessionMiddleware
from urllib.parse import urlparse

load_dotenv()


def normalize_feed_url(url: str) -> str:
    url = url.strip()
    if url.startswith("feed://"):
        url = "http://" + url[7:]
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() if parsed.scheme else "http"
    netloc = parsed.netloc.lower().strip()
    path = parsed.path.rstrip("/") or "/"
    if not netloc:
        return url
    return f"{scheme}://{netloc}{path}"


app = FastAPI(title="feedr", description="A modern recreation of Google Reader")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "change-me-in-production"),
    max_age=3600 * 24 * 7,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

DB_PATH = os.getenv("DATABASE_URL", "sqlite:////storage/feedr.db").replace(
    "sqlite:///", ""
)
try:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
except PermissionError:
    DB_PATH = "feedr.db"
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
engine = create_engine(
    f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    name = Column(String)
    picture = Column(String)
    password_hash = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    feeds = relationship("Feed", back_populates="user", cascade="all, delete-orphan")
    folders = relationship(
        "Folder", back_populates="user", cascade="all, delete-orphan"
    )
    read_states = relationship(
        "ReadState", back_populates="user", cascade="all, delete-orphan"
    )


class Folder(Base):
    __tablename__ = "folders"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="folders")
    feeds = relationship("Feed", back_populates="folder")


class Feed(Base):
    __tablename__ = "feeds"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=True)
    url = Column(String, nullable=False)
    title = Column(String)
    description = Column(Text)
    site_url = Column(String)
    last_fetched_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="feeds")
    folder = relationship("Folder", back_populates="feeds")
    articles = relationship(
        "Article", back_populates="feed", cascade="all, delete-orphan"
    )


class Article(Base):
    __tablename__ = "articles"
    id = Column(Integer, primary_key=True, index=True)
    feed_id = Column(Integer, ForeignKey("feeds.id"), nullable=False)
    guid = Column(String, nullable=False)
    title = Column(String)
    link = Column(String)
    summary = Column(Text)
    content = Column(Text)
    published_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    feed = relationship("Feed", back_populates="articles")
    read_states = relationship(
        "ReadState", back_populates="article", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("feed_id", "guid", name="uix_article_guid"),)


class ReadState(Base):
    __tablename__ = "read_states"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime)

    user = relationship("User", back_populates="read_states")
    article = relationship("Article", back_populates="read_states")


# v2 normalized schema (shared feeds and articles)


class FeedSource(Base):
    __tablename__ = "feed_sources"
    id = Column(Integer, primary_key=True, index=True)
    normalized_url = Column(String, nullable=False, unique=True, index=True)
    display_url = Column(String, nullable=False)
    site_url = Column(String)
    title = Column(String)
    description = Column(Text)
    etag = Column(String)
    last_modified = Column(String)
    last_fetched_at = Column(DateTime)
    last_successful_fetch_at = Column(DateTime)
    fetch_status = Column(String, default="unknown")
    fetch_error = Column(Text)
    is_fetching = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    subscriptions = relationship("FeedSubscription", back_populates="source")
    shared_articles = relationship("SharedArticle", back_populates="source")


class FeedSubscription(Base):
    __tablename__ = "feed_subscriptions"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    feed_source_id = Column(Integer, ForeignKey("feed_sources.id"), nullable=False)
    folder_id = Column(Integer, ForeignKey("folders.id"), nullable=True)
    custom_title = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "feed_source_id", name="uix_subscription_user_source"
        ),
    )

    user = relationship("User")
    source = relationship("FeedSource", back_populates="subscriptions")
    folder = relationship("Folder")


class SharedArticle(Base):
    __tablename__ = "shared_articles"
    id = Column(Integer, primary_key=True, index=True)
    feed_source_id = Column(Integer, ForeignKey("feed_sources.id"), nullable=False)
    guid = Column(String, nullable=False)
    canonical_key = Column(String, nullable=False)
    title = Column(String)
    link = Column(String)
    summary = Column(Text)
    content = Column(Text)
    author = Column(String)
    published_at = Column(DateTime)
    fetched_at = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "feed_source_id", "canonical_key", name="uix_shared_article_key"
        ),
    )

    source = relationship("FeedSource", back_populates="shared_articles")
    states = relationship(
        "UserArticleState", back_populates="article", cascade="all, delete-orphan"
    )
    shares = relationship(
        "ArticleShare", back_populates="article", cascade="all, delete-orphan"
    )


class UserArticleState(Base):
    __tablename__ = "user_article_states"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    article_id = Column(Integer, ForeignKey("shared_articles.id"), nullable=False)
    is_read = Column(Boolean, default=False)
    read_at = Column(DateTime)
    is_starred = Column(Boolean, default=False)
    starred_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uix_user_article_state"),
    )

    user = relationship("User")
    article = relationship("SharedArticle", back_populates="states")


class Friendship(Base):
    __tablename__ = "friendships"
    id = Column(Integer, primary_key=True, index=True)
    requester_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    addressee_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    status = Column(String, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    accepted_at = Column(DateTime)

    __table_args__ = (
        UniqueConstraint(
            requester_user_id, addressee_user_id, name="uix_friendship_pair"
        ),
    )

    requester = relationship("User", foreign_keys=[requester_user_id])
    addressee = relationship("User", foreign_keys=[addressee_user_id])


class ArticleShare(Base):
    __tablename__ = "article_shares"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    article_id = Column(Integer, ForeignKey("shared_articles.id"), nullable=False)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "article_id", name="uix_article_share_user_article"
        ),
    )

    user = relationship("User")
    article = relationship("SharedArticle", back_populates="shares")


Base.metadata.create_all(bind=engine)


def ensure_schema():
    with engine.begin() as conn:
        columns = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(users)").fetchall()
        }
        if "password_hash" not in columns:
            conn.execute(text("ALTER TABLE users ADD COLUMN password_hash VARCHAR"))


ensure_schema()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request, db: Session) -> Optional[User]:
    session_user = request.session.get("user")
    if not session_user:
        return None
    user = db.query(User).filter(User.email == session_user["email"]).first()
    if not user:
        user = User(
            email=session_user["email"],
            name=session_user.get("name"),
            picture=session_user.get("picture"),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def is_local_auth_enabled(request: Request) -> bool:
    hostname = (request.url.hostname or "").lower()
    if hostname in {"localhost", "127.0.0.1", "::1"}:
        return True
    return os.getenv("LOCAL_AUTH_ENABLED", "").lower() in {"1", "true", "yes", "on"}


def local_account_email(identifier: str) -> str:
    normalized = identifier.strip().lower()
    if "@" in normalized:
        return normalized
    return f"{normalized}@local.feedr"


def password_display_name(identifier: str) -> str:
    base = identifier.strip()
    if not base:
        return "Local User"
    return base.split("@", 1)[0]


def hash_password(password: str, salt: Optional[str] = None) -> str:
    salt = salt or base64.urlsafe_b64encode(os.urandom(16)).decode("utf-8")
    iterations = 390000
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations
    )
    encoded = base64.urlsafe_b64encode(digest).decode("utf-8")
    return f"pbkdf2_sha256${iterations}${salt}${encoded}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt, expected = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    candidate = hash_password(password, salt=salt)
    return secrets.compare_digest(candidate, stored_hash)


def friendship_data_for_user(user: User, db: Session) -> dict:
    sent = db.query(Friendship).filter(Friendship.requester_user_id == user.id).all()
    received = (
        db.query(Friendship).filter(Friendship.addressee_user_id == user.id).all()
    )

    friends = []
    pending_sent = []
    pending_received = []

    for friendship in sent:
        other = db.query(User).filter(User.id == friendship.addressee_user_id).first()
        if not other:
            continue
        item = {
            "friendship_id": friendship.id,
            "user_id": other.id,
            "email": other.email,
            "name": other.name,
            "status": friendship.status,
            "created_at": friendship.created_at.isoformat()
            if friendship.created_at
            else None,
            "accepted_at": friendship.accepted_at.isoformat()
            if friendship.accepted_at
            else None,
            "direction": "outgoing",
        }
        if friendship.status == "accepted":
            friends.append(item)
        else:
            pending_sent.append(item)

    for friendship in received:
        other = db.query(User).filter(User.id == friendship.requester_user_id).first()
        if not other:
            continue
        item = {
            "friendship_id": friendship.id,
            "user_id": other.id,
            "email": other.email,
            "name": other.name,
            "status": friendship.status,
            "created_at": friendship.created_at.isoformat()
            if friendship.created_at
            else None,
            "accepted_at": friendship.accepted_at.isoformat()
            if friendship.accepted_at
            else None,
            "direction": "incoming",
        }
        if friendship.status == "accepted":
            friends.append(item)
        elif friendship.status == "pending":
            pending_received.append(item)

    friends.sort(key=lambda item: (item["name"] or item["email"]).lower())
    pending_sent.sort(key=lambda item: (item["name"] or item["email"]).lower())
    pending_received.sort(key=lambda item: (item["name"] or item["email"]).lower())

    return {
        "friends": friends,
        "pending_sent": pending_sent,
        "pending_received": pending_received,
    }


@app.get("/up")
async def up():
    return {
        "status": "ok",
        "service": "feedr",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/reader")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user(request, db):
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        request,
        "login.html",
        {"local_auth_enabled": is_local_auth_enabled(request)},
    )


@app.post("/auth/local", response_class=HTMLResponse)
async def auth_local(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if not is_local_auth_enabled(request):
        return JSONResponse({"error": "Local auth is disabled"}, status_code=403)

    username = username.strip()
    error = None
    if not username or not password:
        error = "Enter both username and password."
    elif len(password) < 8:
        error = "Use at least 8 characters for the password."

    if error:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": error,
                "local_auth_enabled": True,
                "last_username": username,
            },
            status_code=400,
        )

    email = local_account_email(username)
    user = db.query(User).filter(User.email == email).first()

    if user and not user.password_hash:
        error = (
            "That account exists without a local password. Sign in with Google instead."
        )
    elif user and not verify_password(password, user.password_hash):
        error = "Incorrect password."
    elif not user:
        user = User(
            email=email,
            name=password_display_name(username),
            password_hash=hash_password(password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    if error:
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": error,
                "local_auth_enabled": True,
                "last_username": username,
            },
            status_code=400,
        )

    request.session["user"] = {
        "email": user.email,
        "name": user.name,
        "picture": user.picture,
    }
    return RedirectResponse(url="/", status_code=303)


@app.get("/auth/google")
async def auth_google(request: Request):
    app_url = os.getenv("APP_URL", "http://localhost")
    if not app_url.startswith(("http://", "https://")):
        app_url = "http://" + app_url
    redirect_uri = app_url + "/auth/callback"
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await oauth.google.authorize_access_token(request)
    except Exception:
        return RedirectResponse(url="/login")
    user_info = token.get("userinfo")
    if user_info:
        request.session["user"] = {
            "email": user_info.get("email"),
            "name": user_info.get("name"),
            "picture": user_info.get("picture"),
        }
    return RedirectResponse(url="/")


@app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/login")


# Feed Management (v2 — subscriptions over shared sources)


@app.get("/api/feeds")
async def api_feeds(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    folders = db.query(Folder).filter(Folder.user_id == user.id).all()
    folder_map = {f.id: f.name for f in folders}
    subscriptions = (
        db.query(FeedSubscription).filter(FeedSubscription.user_id == user.id).all()
    )
    result = []
    for sub in subscriptions:
        source = sub.source
        unread = (
            db.query(func.count(SharedArticle.id))
            .filter(
                SharedArticle.feed_source_id == source.id,
                ~SharedArticle.id.in_(
                    db.query(UserArticleState.article_id).filter(
                        UserArticleState.user_id == user.id,
                        UserArticleState.is_read == True,
                    )
                ),
            )
            .scalar()
            or 0
        )
        result.append(
            {
                "id": sub.id,
                "title": sub.custom_title or source.title or source.display_url,
                "url": source.normalized_url,
                "folder_id": sub.folder_id,
                "folder_name": folder_map.get(sub.folder_id),
                "unread_count": unread,
                "site_url": source.site_url,
                "feed_source_id": source.id,
            }
        )
    return {"feeds": result, "folders": [{"id": f.id, "name": f.name} for f in folders]}


@app.post("/api/feeds")
async def api_add_feed(
    request: Request,
    url: str = Form(...),
    folder_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    norm = normalize_feed_url(url)
    if not norm or not norm.startswith(("http://", "https://")):
        return JSONResponse({"error": "Invalid URL"}, status_code=400)

    # Check for existing subscription
    existing_sub = (
        db.query(FeedSubscription)
        .join(FeedSource)
        .filter(
            FeedSubscription.user_id == user.id,
            FeedSource.normalized_url == norm,
        )
        .first()
    )
    if existing_sub:
        return JSONResponse(
            {"error": "Already subscribed", "subscription_id": existing_sub.id},
            status_code=409,
        )

    # Probe the feed for metadata
    parsed = feedparser.parse(
        norm, agent="feedr/1.0 (+https://github.com/1c3m4n/feedr)"
    )
    title = parsed.feed.get("title", norm)
    description = parsed.feed.get("description", "")
    site_url = parsed.feed.get("link", norm)

    # Resolve or create source
    source = db.query(FeedSource).filter(FeedSource.normalized_url == norm).first()
    if not source:
        source = FeedSource(
            normalized_url=norm,
            display_url=url,
            site_url=site_url,
            title=title,
            description=description,
        )
        db.add(source)
        db.commit()
        db.refresh(source)

    sub = FeedSubscription(
        user_id=user.id,
        feed_source_id=source.id,
        folder_id=folder_id,
        custom_title=title if title != norm else None,
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    fetch_result = fetch_source_articles(db, source)
    return {
        "success": True,
        "subscription": {
            "id": sub.id,
            "title": sub.custom_title or source.title or source.display_url,
        },
        "source": {"id": source.id, "normalized_url": source.normalized_url},
        "fetch": fetch_result,
    }


@app.delete("/api/feeds/{feed_id}")
async def api_delete_feed(
    request: Request, feed_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    sub = (
        db.query(FeedSubscription)
        .filter(FeedSubscription.id == feed_id, FeedSubscription.user_id == user.id)
        .first()
    )
    if sub:
        db.delete(sub)
        db.commit()
    return {"success": True}


# Folders


@app.post("/api/folders")
async def api_create_folder(
    request: Request, name: str = Form(...), db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    folder = Folder(user_id=user.id, name=name)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"success": True, "folder": {"id": folder.id, "name": folder.name}}


@app.delete("/api/folders/{folder_id}")
async def api_delete_folder(
    request: Request, folder_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    folder = (
        db.query(Folder)
        .filter(Folder.id == folder_id, Folder.user_id == user.id)
        .first()
    )
    if folder:
        db.delete(folder)
        db.commit()
    return {"success": True}


# Articles (v2)


@app.get("/api/articles")
async def api_articles(
    request: Request,
    feed_id: Optional[int] = None,
    search: Optional[str] = None,
    unread_only: bool = False,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Base query: articles from sources the user subscribes to
    sub_source_ids = (
        db.query(FeedSubscription.feed_source_id)
        .filter(FeedSubscription.user_id == user.id)
        .subquery()
    )
    query = db.query(SharedArticle).filter(
        SharedArticle.feed_source_id.in_(sub_source_ids)
    )

    if feed_id:
        # feed_id here is a subscription id; resolve to source
        sub = (
            db.query(FeedSubscription)
            .filter(FeedSubscription.id == feed_id, FeedSubscription.user_id == user.id)
            .first()
        )
        if sub:
            query = query.filter(SharedArticle.feed_source_id == sub.feed_source_id)

    if search:
        query = query.filter(
            or_(
                SharedArticle.title.contains(search),
                SharedArticle.summary.contains(search),
                SharedArticle.content.contains(search),
            )
        )

    if unread_only:
        read_ids = db.query(UserArticleState.article_id).filter(
            UserArticleState.user_id == user.id, UserArticleState.is_read == True
        )
        query = query.filter(~SharedArticle.id.in_(read_ids))

    articles = query.order_by(SharedArticle.published_at.desc()).limit(200).all()

    result = []
    for a in articles:
        state = (
            db.query(UserArticleState)
            .filter(
                UserArticleState.user_id == user.id,
                UserArticleState.article_id == a.id,
            )
            .first()
        )
        result.append(
            {
                "id": a.id,
                "title": a.title or "Untitled",
                "link": a.link,
                "summary": a.summary,
                "content": a.content,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "feed_title": a.source.title or a.source.display_url,
                "is_read": state.is_read if state else False,
            }
        )
    return {"articles": result}


@app.post("/api/articles/{article_id}/read")
async def api_mark_read(
    request: Request, article_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    state = (
        db.query(UserArticleState)
        .filter(
            UserArticleState.user_id == user.id,
            UserArticleState.article_id == article_id,
        )
        .first()
    )
    if not state:
        state = UserArticleState(
            user_id=user.id,
            article_id=article_id,
            is_read=True,
            read_at=datetime.utcnow(),
        )
        db.add(state)
    else:
        state.is_read = True
        state.read_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@app.post("/api/articles/{article_id}/unread")
async def api_mark_unread(
    request: Request, article_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    state = (
        db.query(UserArticleState)
        .filter(
            UserArticleState.user_id == user.id,
            UserArticleState.article_id == article_id,
        )
        .first()
    )
    if not state:
        state = UserArticleState(user_id=user.id, article_id=article_id, is_read=False)
        db.add(state)
    else:
        state.is_read = False
        state.read_at = None
    db.commit()
    return {"success": True}


@app.post("/api/feeds/{feed_id}/mark-all-read")
async def api_mark_all_read(
    request: Request, feed_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    sub = (
        db.query(FeedSubscription)
        .filter(FeedSubscription.id == feed_id, FeedSubscription.user_id == user.id)
        .first()
    )
    if not sub:
        return JSONResponse({"error": "Not found"}, status_code=404)
    articles = (
        db.query(SharedArticle)
        .filter(SharedArticle.feed_source_id == sub.feed_source_id)
        .all()
    )
    for article in articles:
        state = (
            db.query(UserArticleState)
            .filter(
                UserArticleState.user_id == user.id,
                UserArticleState.article_id == article.id,
            )
            .first()
        )
        if not state:
            state = UserArticleState(
                user_id=user.id,
                article_id=article.id,
                is_read=True,
                read_at=datetime.utcnow(),
            )
            db.add(state)
        else:
            state.is_read = True
            state.read_at = datetime.utcnow()
    db.commit()
    return {"success": True}


# Shared Articles


@app.get("/api/articles/shared")
async def api_shared_articles(
    request: Request,
    friend_id: Optional[int] = None,
    search: Optional[str] = None,
    unread_only: bool = False,
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    # Find accepted friends
    friend_ids_subquery = (
        db.query(Friendship.requester_user_id)
        .filter(
            Friendship.addressee_user_id == user.id, Friendship.status == "accepted"
        )
        .union(
            db.query(Friendship.addressee_user_id).filter(
                Friendship.requester_user_id == user.id, Friendship.status == "accepted"
            )
        )
        .subquery()
    )

    shares_query = db.query(ArticleShare).filter(
        ArticleShare.user_id.in_(friend_ids_subquery)
    )
    if friend_id:
        shares_query = shares_query.filter(ArticleShare.user_id == friend_id)

    shares = shares_query.order_by(ArticleShare.created_at.desc()).all()

    # Deduplicate by article_id while preserving sharer info
    article_map = {}
    for share in shares:
        aid = share.article_id
        sharer = db.query(User).filter(User.id == share.user_id).first()
        sharer_info = {
            "user_id": sharer.id,
            "name": sharer.name or sharer.email,
            "comment": share.comment,
            "shared_at": share.created_at.isoformat() if share.created_at else None,
        }
        if aid not in article_map:
            article_map[aid] = {"article": share.article, "sharers": []}
        article_map[aid]["sharers"].append(sharer_info)

    results = []
    for aid, data in article_map.items():
        a = data["article"]
        if search:
            haystack = f"{a.title or ''} {a.summary or ''} {a.content or ''}".lower()
            if search.lower() not in haystack:
                continue
        state = (
            db.query(UserArticleState)
            .filter(
                UserArticleState.user_id == user.id,
                UserArticleState.article_id == a.id,
            )
            .first()
        )
        if unread_only and (state and state.is_read):
            continue
        results.append(
            {
                "id": a.id,
                "title": a.title or "Untitled",
                "link": a.link,
                "summary": a.summary,
                "content": a.content,
                "published_at": a.published_at.isoformat() if a.published_at else None,
                "feed_title": a.source.title or a.source.display_url,
                "is_read": state.is_read if state else False,
                "sharers": data["sharers"],
            }
        )

    return {"articles": results}


@app.post("/api/articles/{article_id}/share")
async def api_share_article(
    request: Request,
    article_id: int,
    comment: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    article = db.query(SharedArticle).filter(SharedArticle.id == article_id).first()
    if not article:
        return JSONResponse({"error": "Article not found"}, status_code=404)

    existing = (
        db.query(ArticleShare)
        .filter(ArticleShare.user_id == user.id, ArticleShare.article_id == article_id)
        .first()
    )
    if existing:
        existing.comment = comment
        db.commit()
        return {"success": True, "share_id": existing.id, "updated": True}

    share = ArticleShare(user_id=user.id, article_id=article_id, comment=comment)
    db.add(share)
    db.commit()
    db.refresh(share)
    return {"success": True, "share_id": share.id}


@app.delete("/api/articles/{article_id}/share")
async def api_unshare_article(
    request: Request, article_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    share = (
        db.query(ArticleShare)
        .filter(ArticleShare.user_id == user.id, ArticleShare.article_id == article_id)
        .first()
    )
    if share:
        db.delete(share)
        db.commit()
    return {"success": True}


# Friends


@app.get("/api/friends")
async def api_friends(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return friendship_data_for_user(user, db)


@app.post("/api/friends/request")
async def api_request_friend(
    request: Request, email: str = Form(...), db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    target = db.query(User).filter(User.email == email.strip()).first()
    if not target:
        return JSONResponse({"error": "User not found"}, status_code=404)
    if target.id == user.id:
        return JSONResponse({"error": "Cannot friend yourself"}, status_code=400)

    a, b = (user.id, target.id)
    existing = (
        db.query(Friendship)
        .filter(
            Friendship.requester_user_id == a,
            Friendship.addressee_user_id == b,
        )
        .first()
    )
    if not existing:
        existing = (
            db.query(Friendship)
            .filter(
                Friendship.requester_user_id == b,
                Friendship.addressee_user_id == a,
            )
            .first()
        )

    if existing:
        if existing.status == "accepted":
            return JSONResponse({"error": "Already friends"}, status_code=409)
        if existing.addressee_user_id == user.id:
            requester = (
                db.query(User).filter(User.id == existing.requester_user_id).first()
            )
            requester_name = None
            if requester:
                requester_name = requester.name or requester.email
            message = "This friend request is waiting for your reply."
            if requester_name:
                message = f"{requester_name} already sent you a request. Accept or decline it from Profile."
            return JSONResponse(
                {
                    "error": message,
                    "friendship_id": existing.id,
                    "direction": "incoming",
                },
                status_code=409,
            )
        return JSONResponse(
            {
                "error": "Request already pending",
                "friendship_id": existing.id,
                "direction": "outgoing",
            },
            status_code=409,
        )

    friendship = Friendship(
        requester_user_id=user.id,
        addressee_user_id=target.id,
        status="pending",
    )
    db.add(friendship)
    db.commit()
    db.refresh(friendship)
    return {"success": True, "friendship_id": friendship.id}


@app.post("/api/friends/{friendship_id}/accept")
async def api_accept_friend(
    request: Request, friendship_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    friendship = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not friendship or friendship.addressee_user_id != user.id:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if friendship.status != "pending":
        return JSONResponse({"error": "Not pending"}, status_code=400)

    friendship.status = "accepted"
    friendship.accepted_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@app.post("/api/friends/{friendship_id}/decline")
async def api_decline_friend(
    request: Request, friendship_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    friendship = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not friendship or friendship.addressee_user_id != user.id:
        return JSONResponse({"error": "Not found"}, status_code=404)

    db.delete(friendship)
    db.commit()
    return {"success": True}


@app.delete("/api/friends/{friendship_id}")
async def api_remove_friend(
    request: Request, friendship_id: int, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    friendship = db.query(Friendship).filter(Friendship.id == friendship_id).first()
    if not friendship:
        return JSONResponse({"error": "Not found"}, status_code=404)
    if (
        friendship.requester_user_id != user.id
        and friendship.addressee_user_id != user.id
    ):
        return JSONResponse({"error": "Not found"}, status_code=404)

    db.delete(friendship)
    db.commit()
    return {"success": True}


# OPML


@app.post("/api/opml/import")
async def api_opml_import(
    request: Request, file: UploadFile, db: Session = Depends(get_db)
):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    content = await file.read()
    root = ET.fromstring(content)
    imported = 0
    skipped = 0

    # Build folder name -> folder id map
    user_folders = {
        f.name: f.id for f in db.query(Folder).filter(Folder.user_id == user.id).all()
    }

    for outline in root.iter("outline"):
        # Folder container with nested outlines
        if outline.get("xmlUrl") is None:
            for child in outline:
                url = child.get("xmlUrl")
                title = child.get("text") or child.get("title") or url
                folder_name = outline.get("text") or outline.get("title")
                if url:
                    norm = normalize_feed_url(url)
                    existing_sub = (
                        db.query(FeedSubscription)
                        .join(FeedSource)
                        .filter(
                            FeedSubscription.user_id == user.id,
                            FeedSource.normalized_url == norm,
                        )
                        .first()
                    )
                    if existing_sub:
                        skipped += 1
                        continue

                    source = (
                        db.query(FeedSource)
                        .filter(FeedSource.normalized_url == norm)
                        .first()
                    )
                    if not source:
                        source = FeedSource(
                            normalized_url=norm,
                            display_url=url,
                            site_url=url,
                            title=title,
                        )
                        db.add(source)
                        db.commit()
                        db.refresh(source)

                    folder_id = user_folders.get(folder_name)
                    sub = FeedSubscription(
                        user_id=user.id,
                        feed_source_id=source.id,
                        folder_id=folder_id,
                        custom_title=title if title != norm else None,
                    )
                    db.add(sub)
                    imported += 1
        else:
            url = outline.get("xmlUrl")
            title = outline.get("text") or outline.get("title") or url
            if url:
                norm = normalize_feed_url(url)
                existing_sub = (
                    db.query(FeedSubscription)
                    .join(FeedSource)
                    .filter(
                        FeedSubscription.user_id == user.id,
                        FeedSource.normalized_url == norm,
                    )
                    .first()
                )
                if existing_sub:
                    skipped += 1
                    continue

                source = (
                    db.query(FeedSource)
                    .filter(FeedSource.normalized_url == norm)
                    .first()
                )
                if not source:
                    source = FeedSource(
                        normalized_url=norm,
                        display_url=url,
                        site_url=url,
                        title=title,
                    )
                    db.add(source)
                    db.commit()
                    db.refresh(source)

                sub = FeedSubscription(
                    user_id=user.id,
                    feed_source_id=source.id,
                    custom_title=title if title != norm else None,
                )
                db.add(sub)
                imported += 1

    db.commit()
    return {"success": True, "imported": imported, "skipped": skipped}


@app.get("/api/opml/export")
async def api_opml_export(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    root = ET.Element("opml", version="1.0")
    head = ET.SubElement(root, "head")
    ET.SubElement(head, "title").text = f"{user.name or user.email}'s feedr feeds"
    body = ET.SubElement(root, "body")

    folders = db.query(Folder).filter(Folder.user_id == user.id).all()
    folder_map = {f.id: [] for f in folders}
    no_folder = []

    subscriptions = (
        db.query(FeedSubscription).filter(FeedSubscription.user_id == user.id).all()
    )
    for sub in subscriptions:
        source = sub.source
        if sub.folder_id and sub.folder_id in folder_map:
            folder_map[sub.folder_id].append(sub)
        else:
            no_folder.append(sub)

    for folder in folders:
        folder_el = ET.SubElement(body, "outline", text=folder.name, title=folder.name)
        for sub in folder_map.get(folder.id, []):
            src = sub.source
            ET.SubElement(
                folder_el,
                "outline",
                type="rss",
                text=sub.custom_title or src.title or src.display_url,
                title=sub.custom_title or src.title or src.display_url,
                xmlUrl=src.normalized_url,
                htmlUrl=src.site_url or src.normalized_url,
            )

    for sub in no_folder:
        src = sub.source
        ET.SubElement(
            body,
            "outline",
            type="rss",
            text=sub.custom_title or src.title or src.display_url,
            title=sub.custom_title or src.title or src.display_url,
            xmlUrl=src.normalized_url,
            htmlUrl=src.site_url or src.normalized_url,
        )

    xml_str = ET.tostring(root, encoding="unicode")
    return Response(
        content=xml_str,
        media_type="application/xml",
        headers={"Content-Disposition": "attachment; filename=feedr.opml"},
    )


# Fetcher


def _derive_canonical_key(entry: dict) -> str:
    guid = entry.get("id") or ""
    if guid:
        return guid.strip()
    link = entry.get("link") or ""
    if link:
        return link.strip()
    title = entry.get("title") or ""
    return hashlib.sha256(f"{title}:{link}".encode()).hexdigest()


def fetch_source_articles(db: Session, source: FeedSource) -> dict:
    if source.is_fetching:
        return {"success": False, "error": "Already fetching", "fetched": 0}

    source.is_fetching = True
    db.commit()

    try:
        agent = "feedr/1.0 (+https://github.com/1c3m4n/feedr)"
        kwargs = {"agent": agent}
        if source.etag:
            kwargs["etag"] = source.etag
        if source.last_modified:
            kwargs["modified"] = source.last_modified

        parsed = feedparser.parse(source.normalized_url, **kwargs)
    except Exception as exc:
        source.fetch_status = "error"
        source.fetch_error = str(exc)
        source.last_fetched_at = datetime.utcnow()
        source.is_fetching = False
        db.commit()
        return {"success": False, "error": str(exc), "fetched": 0}

    # Update conditional request headers if provided by server
    if hasattr(parsed, "etag") and parsed.etag:
        source.etag = parsed.etag
    if hasattr(parsed, "modified") and parsed.modified:
        source.last_modified = parsed.modified

    if parsed.get("status") == 304:
        source.fetch_status = "ok"
        source.last_fetched_at = datetime.utcnow()
        source.fetch_error = None
        source.is_fetching = False
        db.commit()
        return {"success": True, "fetched": 0, "message": "Not modified"}

    if parsed.get("bozo") and parsed.get("bozo_exception"):
        pass

    added = 0
    skipped = 0
    for entry in parsed.entries[:50]:
        ckey = _derive_canonical_key(entry)
        if not ckey:
            skipped += 1
            continue
        existing = (
            db.query(SharedArticle)
            .filter(
                SharedArticle.feed_source_id == source.id,
                SharedArticle.canonical_key == ckey,
            )
            .first()
        )
        if existing:
            skipped += 1
            continue
        title = entry.get("title", "Untitled")
        link = entry.get("link", "")
        summary = entry.get("summary", "")
        content = (
            entry.get("content", [{}])[0].get("value", "")
            if entry.get("content")
            else summary
        )
        author = entry.get("author", "")
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6])
        elif entry.get("updated_parsed"):
            published = datetime(*entry.updated_parsed[:6])
        article = SharedArticle(
            feed_source_id=source.id,
            guid=entry.get("id", ""),
            canonical_key=ckey,
            title=title,
            link=link,
            summary=summary,
            content=content,
            author=author,
            published_at=published,
        )
        db.add(article)
        added += 1

    source.last_fetched_at = datetime.utcnow()
    source.fetch_status = "ok"
    source.fetch_error = None
    source.last_successful_fetch_at = datetime.utcnow()
    source.is_fetching = False
    db.commit()
    return {"success": True, "fetched": added, "skipped": skipped}


def fetch_feed_articles(db: Session, feed: Feed) -> dict:
    """Legacy compatibility wrapper. Finds or creates the v2 source and fetches it."""
    from urllib.parse import urlparse

    url = feed.url.strip()
    if url.startswith("feed://"):
        url = "http://" + url[7:]
    parsed = urlparse(url)
    scheme = parsed.scheme.lower() if parsed.scheme else "http"
    netloc = parsed.netloc.lower().strip()
    path = parsed.path.rstrip("/") or "/"
    norm = f"{scheme}://{netloc}{path}"

    source = db.query(FeedSource).filter(FeedSource.normalized_url == norm).first()
    if not source:
        source = FeedSource(
            normalized_url=norm,
            display_url=feed.url,
            site_url=feed.site_url or feed.url,
            title=feed.title,
            description=feed.description,
        )
        db.add(source)
        db.commit()
        db.refresh(source)

    return fetch_source_articles(db, source)


def background_fetcher():
    while True:
        try:
            db = SessionLocal()
            sources = db.query(FeedSource).all()
            for source in sources:
                fetch_source_articles(db, source)
                time.sleep(1)
        except Exception:
            pass
        finally:
            db.close()
        time.sleep(300)


fetcher_thread = threading.Thread(target=background_fetcher, daemon=True)
fetcher_thread.start()


# Reader UI


@app.get("/reader", response_class=HTMLResponse)
async def reader(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "reader.html", {"user": user})


@app.get("/reader/settings", response_class=HTMLResponse)
async def reader_settings(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(request, "settings.html", {"user": user})


@app.get("/reader/profile", response_class=HTMLResponse)
async def reader_profile(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": user,
            "friendships": friendship_data_for_user(user, db),
        },
    )
