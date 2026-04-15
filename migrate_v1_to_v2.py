"""
Migration script: v1 (user-owned feeds/articles) -> v2 (shared sources + subscriptions)
Run: python migrate_v1_to_v2.py [--dry-run]
"""

import argparse
import hashlib
import sys
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from main import (
    Article,
    Base,
    Feed,
    FeedSource,
    FeedSubscription,
    Folder,
    ReadState,
    SessionLocal,
    SharedArticle,
    User,
    UserArticleState,
    engine,
)


def normalize_url(url: str) -> str:
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


def derive_canonical_key(entry) -> str:
    guid = entry.get("id") or ""
    if guid:
        return guid.strip()
    link = entry.get("link") or ""
    if link:
        return link.strip()
    title = entry.get("title") or ""
    return hashlib.sha256(f"{title}:{link}".encode()).hexdigest()


def run_migration(dry_run: bool = False):
    db = SessionLocal()
    try:
        inspector = inspect(engine)
        v2_tables = {
            "feed_sources",
            "feed_subscriptions",
            "shared_articles",
            "user_article_states",
            "friendships",
            "article_shares",
        }
        existing = set(inspector.get_table_names())
        missing = v2_tables - existing
        if not missing:
            print("V2 tables already exist. Nothing to create.")
        else:
            print(f"Creating missing v2 tables: {missing}")
            if not dry_run:
                Base.metadata.create_all(
                    bind=engine,
                    tables=[
                        t for name, t in Base.metadata.tables.items() if name in missing
                    ],
                )

        # Check if there is v1 data to migrate
        v1_feed_count = db.query(Feed).count()
        if v1_feed_count == 0:
            print("No v1 feed data found. Migration complete (nothing to backfill).")
            return

        print(f"Found {v1_feed_count} v1 feeds to migrate.")

        # Step 1: URL canonicalization and collision detection
        feeds = db.query(Feed).all()
        url_map = {}
        collisions = []
        for f in feeds:
            norm = normalize_url(f.url)
            if norm in url_map and url_map[norm] != f.url:
                collisions.append((norm, f.url, url_map[norm]))
            else:
                url_map[norm] = f.url

        if collisions:
            print("WARNING: URL collisions detected:")
            for norm, url_a, url_b in collisions:
                print(f"  {norm}")
                print(f"    -> {url_a}")
                print(f"    -> {url_b}")
        else:
            print("No URL collisions detected.")

        if dry_run:
            print("Dry-run: stopping before writes.")
            return

        # Step 2: Backfill feed_sources
        source_id_by_norm = {}
        existing_sources = db.query(FeedSource).all()
        for s in existing_sources:
            source_id_by_norm[s.normalized_url] = s.id

        for f in feeds:
            norm = normalize_url(f.url)
            if norm in source_id_by_norm:
                continue
            source = FeedSource(
                normalized_url=norm,
                display_url=f.url,
                site_url=f.site_url or f.url,
                title=f.title,
                description=f.description,
                last_fetched_at=f.last_fetched_at,
                last_successful_fetch_at=f.last_fetched_at,
                fetch_status="ok" if f.last_fetched_at else "unknown",
            )
            db.add(source)
            db.flush()
            source_id_by_norm[norm] = source.id
            print(f"  Created feed_source {source.id}: {norm}")

        db.commit()

        # Step 3: Backfill feed_subscriptions
        subscription_id_by_old_feed = {}
        existing_subs = {
            (s.user_id, s.feed_source_id): s.id
            for s in db.query(FeedSubscription).all()
        }
        for f in feeds:
            norm = normalize_url(f.url)
            source_id = source_id_by_norm[norm]
            key = (f.user_id, source_id)
            if key in existing_subs:
                subscription_id_by_old_feed[f.id] = existing_subs[key]
                continue
            sub = FeedSubscription(
                user_id=f.user_id,
                feed_source_id=source_id,
                folder_id=f.folder_id,
                custom_title=f.title if f.title != norm else None,
            )
            db.add(sub)
            db.flush()
            subscription_id_by_old_feed[f.id] = sub.id
            existing_subs[key] = sub.id

        db.commit()

        # Step 4: Backfill shared_articles
        articles = db.query(Article).all()
        print(f"Migrating {len(articles)} v1 articles...")
        article_id_map = {}
        existing_articles = {
            (a.feed_source_id, a.canonical_key): a.id
            for a in db.query(SharedArticle).all()
        }
        for a in articles:
            old_feed = db.query(Feed).filter(Feed.id == a.feed_id).first()
            if not old_feed:
                continue
            norm = normalize_url(old_feed.url)
            source_id = source_id_by_norm.get(norm)
            if not source_id:
                continue
            ckey = (
                a.guid
                or a.link
                or hashlib.sha256(f"{a.title}:{a.link}".encode()).hexdigest()
            )
            key = (source_id, ckey)
            if key in existing_articles:
                article_id_map[a.id] = existing_articles[key]
                continue
            sa = SharedArticle(
                feed_source_id=source_id,
                guid=a.guid or "",
                canonical_key=ckey,
                title=a.title,
                link=a.link,
                summary=a.summary,
                content=a.content,
                author=None,
                published_at=a.published_at,
                fetched_at=a.created_at,
                created_at=a.created_at,
            )
            db.add(sa)
            db.flush()
            article_id_map[a.id] = sa.id
            existing_articles[key] = sa.id

        db.commit()

        # Step 5: Backfill user_article_states
        read_states = db.query(ReadState).all()
        print(f"Collapsing {len(read_states)} v1 read_states...")
        # Deduplicate by keeping latest read_at per (user_id, article_id)
        from collections import defaultdict

        grouped = defaultdict(list)
        for rs in read_states:
            new_article_id = article_id_map.get(rs.article_id)
            if new_article_id:
                grouped[(rs.user_id, new_article_id)].append(rs)

        existing_states = {
            (s.user_id, s.article_id): s for s in db.query(UserArticleState).all()
        }
        for (user_id, new_article_id), rows in grouped.items():
            rows.sort(key=lambda r: r.read_at or r.id, reverse=True)
            best = rows[0]
            key = (user_id, new_article_id)
            if key in existing_states:
                s = existing_states[key]
                s.is_read = best.is_read
                s.read_at = best.read_at
            else:
                s = UserArticleState(
                    user_id=user_id,
                    article_id=new_article_id,
                    is_read=best.is_read,
                    read_at=best.read_at,
                )
                db.add(s)

        db.commit()
        print("Migration completed successfully.")
        print(f"  feed_sources: {db.query(FeedSource).count()}")
        print(f"  feed_subscriptions: {db.query(FeedSubscription).count()}")
        print(f"  shared_articles: {db.query(SharedArticle).count()}")
        print(f"  user_article_states: {db.query(UserArticleState).count()}")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Migrate feedr v1 to v2")
    parser.add_argument(
        "--dry-run", action="store_true", help="Analyze without writing"
    )
    args = parser.parse_args()
    run_migration(dry_run=args.dry_run)
