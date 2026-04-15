import hashlib
import os
import threading
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import List, Optional

import feedparser
from authlib.integrations.starlette_client import OAuth
from dateutil import parser as date_parser
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import Environment, FileSystemLoader
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
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

app = FastAPI(title="feedr", description="A modern recreation of Google Reader")

app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "change-me-in-production"),
    max_age=3600 * 24 * 7,
)

app.mount("/static", StaticFiles(directory="static"), name="static")
jinja2_env = Environment(loader=FileSystemLoader("templates"), cache_size=0)
templates = Jinja2Templates(env=jinja2_env)

oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)

DB_PATH = os.getenv("DATABASE_URL", "sqlite:///storage/feedr.db").replace(
    "sqlite:///", ""
)
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


Base.metadata.create_all(bind=engine)


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


@app.get("/up")
async def up():
    return {
        "status": "ok",
        "service": "feedr",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "0.1.0",
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return RedirectResponse(url="/reader")


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    db = next(get_db())
    if get_current_user(request, db):
        return RedirectResponse(url="/")
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/auth/google")
async def auth_google(request: Request):
    redirect_uri = os.getenv("APP_URL", "http://localhost") + "/auth/callback"
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


# Feed Management


@app.get("/api/feeds")
async def api_feeds(request: Request):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    feeds = db.query(Feed).filter(Feed.user_id == user.id).all()
    folders = db.query(Folder).filter(Folder.user_id == user.id).all()
    folder_map = {f.id: f.name for f in folders}
    result = []
    for f in feeds:
        unread = (
            db.query(func.count(ReadState.id))
            .filter(
                ReadState.user_id == user.id,
                ReadState.article_id.in_(
                    db.query(Article.id).filter(Article.feed_id == f.id)
                ),
                ReadState.is_read == False,
            )
            .scalar()
            or 0
        )
        result.append(
            {
                "id": f.id,
                "title": f.title or f.url,
                "url": f.url,
                "folder_id": f.folder_id,
                "folder_name": folder_map.get(f.folder_id),
                "unread_count": unread,
                "site_url": f.site_url,
            }
        )
    return {"feeds": result, "folders": [{"id": f.id, "name": f.name} for f in folders]}


@app.post("/api/feeds")
async def api_add_feed(
    request: Request, url: str = Form(...), folder_id: Optional[int] = Form(None)
):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    parsed = feedparser.parse(url)
    title = parsed.feed.get("title", url)
    description = parsed.feed.get("description", "")
    site_url = parsed.feed.get("link", url)
    feed = Feed(
        user_id=user.id,
        url=url,
        folder_id=folder_id,
        title=title,
        description=description,
        site_url=site_url,
    )
    db.add(feed)
    db.commit()
    db.refresh(feed)
    fetch_feed_articles(db, feed)
    return {"success": True, "feed": {"id": feed.id, "title": feed.title}}


@app.delete("/api/feeds/{feed_id}")
async def api_delete_feed(request: Request, feed_id: int):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == user.id).first()
    if feed:
        db.delete(feed)
        db.commit()
    return {"success": True}


# Folders


@app.post("/api/folders")
async def api_create_folder(request: Request, name: str = Form(...)):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    folder = Folder(user_id=user.id, name=name)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    return {"success": True, "folder": {"id": folder.id, "name": folder.name}}


@app.delete("/api/folders/{folder_id}")
async def api_delete_folder(request: Request, folder_id: int):
    db = next(get_db())
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


# Articles


@app.get("/api/articles")
async def api_articles(
    request: Request,
    feed_id: Optional[int] = None,
    search: Optional[str] = None,
    unread_only: bool = False,
):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    query = db.query(Article).join(Feed).filter(Feed.user_id == user.id)
    if feed_id:
        query = query.filter(Article.feed_id == feed_id)
    if search:
        query = query.filter(
            or_(
                Article.title.contains(search),
                Article.summary.contains(search),
                Article.content.contains(search),
            )
        )
    if unread_only:
        read_ids = db.query(ReadState.article_id).filter(
            ReadState.user_id == user.id, ReadState.is_read == True
        )
        query = query.filter(~Article.id.in_(read_ids))
    articles = query.order_by(Article.published_at.desc()).limit(200).all()
    result = []
    for a in articles:
        rs = (
            db.query(ReadState)
            .filter(ReadState.user_id == user.id, ReadState.article_id == a.id)
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
                "feed_title": a.feed.title or a.feed.url,
                "is_read": rs.is_read if rs else False,
            }
        )
    return {"articles": result}


@app.post("/api/articles/{article_id}/read")
async def api_mark_read(request: Request, article_id: int):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    rs = (
        db.query(ReadState)
        .filter(ReadState.user_id == user.id, ReadState.article_id == article_id)
        .first()
    )
    if not rs:
        rs = ReadState(
            user_id=user.id,
            article_id=article_id,
            is_read=True,
            read_at=datetime.utcnow(),
        )
        db.add(rs)
    else:
        rs.is_read = True
        rs.read_at = datetime.utcnow()
    db.commit()
    return {"success": True}


@app.post("/api/articles/{article_id}/unread")
async def api_mark_unread(request: Request, article_id: int):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    rs = (
        db.query(ReadState)
        .filter(ReadState.user_id == user.id, ReadState.article_id == article_id)
        .first()
    )
    if not rs:
        rs = ReadState(user_id=user.id, article_id=article_id, is_read=False)
        db.add(rs)
    else:
        rs.is_read = False
        rs.read_at = None
    db.commit()
    return {"success": True}


@app.post("/api/feeds/{feed_id}/mark-all-read")
async def api_mark_all_read(request: Request, feed_id: int):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    feed = db.query(Feed).filter(Feed.id == feed_id, Feed.user_id == user.id).first()
    if not feed:
        return JSONResponse({"error": "Not found"}, status_code=404)
    articles = db.query(Article).filter(Article.feed_id == feed_id).all()
    for article in articles:
        rs = (
            db.query(ReadState)
            .filter(ReadState.user_id == user.id, ReadState.article_id == article.id)
            .first()
        )
        if not rs:
            rs = ReadState(
                user_id=user.id,
                article_id=article.id,
                is_read=True,
                read_at=datetime.utcnow(),
            )
            db.add(rs)
        else:
            rs.is_read = True
            rs.read_at = datetime.utcnow()
    db.commit()
    return {"success": True}


# OPML


@app.post("/api/opml/import")
async def api_opml_import(request: Request, file: UploadFile):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)
    content = await file.read()
    root = ET.fromstring(content)
    imported = 0
    for outline in root.iter("outline"):
        if outline.get("type") == "rss" or outline.get("xmlUrl"):
            url = outline.get("xmlUrl")
            title = outline.get("text") or outline.get("title") or url
            if url:
                feed = (
                    db.query(Feed)
                    .filter(Feed.user_id == user.id, Feed.url == url)
                    .first()
                )
                if not feed:
                    feed = Feed(user_id=user.id, url=url, title=title)
                    db.add(feed)
                    imported += 1
    db.commit()
    return {"success": True, "imported": imported}


@app.get("/api/opml/export")
async def api_opml_export(request: Request):
    db = next(get_db())
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
    feeds = db.query(Feed).filter(Feed.user_id == user.id).all()
    for feed in feeds:
        if feed.folder_id and feed.folder_id in folder_map:
            folder_map[feed.folder_id].append(feed)
        else:
            no_folder.append(feed)
    for folder in folders:
        folder_el = ET.SubElement(body, "outline", text=folder.name, title=folder.name)
        for feed in folder_map.get(folder.id, []):
            ET.SubElement(
                folder_el,
                "outline",
                type="rss",
                text=feed.title or feed.url,
                title=feed.title or feed.url,
                xmlUrl=feed.url,
                htmlUrl=feed.site_url or feed.url,
            )
    for feed in no_folder:
        ET.SubElement(
            body,
            "outline",
            type="rss",
            text=feed.title or feed.url,
            title=feed.title or feed.url,
            xmlUrl=feed.url,
            htmlUrl=feed.site_url or feed.url,
        )
    xml_str = ET.tostring(root, encoding="unicode")
    return Response(
        content=xml_str,
        media_type="application/xml",
        headers={"Content-Disposition": "attachment; filename=feedr.opml"},
    )


# Fetcher


def fetch_feed_articles(db: Session, feed: Feed):
    try:
        parsed = feedparser.parse(feed.url)
    except Exception:
        return
    for entry in parsed.entries[:50]:
        guid = entry.get("id") or entry.get("link") or entry.get("title", "")
        if not guid:
            continue
        existing = (
            db.query(Article)
            .filter(Article.feed_id == feed.id, Article.guid == guid)
            .first()
        )
        if existing:
            continue
        title = entry.get("title", "Untitled")
        link = entry.get("link", "")
        summary = entry.get("summary", "")
        content = (
            entry.get("content", [{}])[0].get("value", "")
            if entry.get("content")
            else summary
        )
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6])
        elif entry.get("updated_parsed"):
            published = datetime(*entry.updated_parsed[:6])
        article = Article(
            feed_id=feed.id,
            guid=guid,
            title=title,
            link=link,
            summary=summary,
            content=content,
            published_at=published,
        )
        db.add(article)
    feed.last_fetched_at = datetime.utcnow()
    db.commit()


def background_fetcher():
    while True:
        try:
            db = SessionLocal()
            feeds = db.query(Feed).all()
            for feed in feeds:
                fetch_feed_articles(db, feed)
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
async def reader(request: Request):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("reader.html", {"request": request, "user": user})


@app.get("/reader/settings", response_class=HTMLResponse)
async def reader_settings(request: Request):
    db = next(get_db())
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse(
        "settings.html", {"request": request, "user": user}
    )
