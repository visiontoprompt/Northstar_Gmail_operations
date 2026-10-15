"""Organization-scoped Google Alerts topic catalog and selection helpers."""

import random

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import GoogleAlertTopic


DEFAULT_ALERT_TOPICS: tuple[tuple[str, str], ...] = (
    ("Climate Change", "Global News & Politics"), ("Geopolitics", "Global News & Politics"),
    ("Elections", "Global News & Politics"), ("Supreme Court", "Global News & Politics"),
    ("United Nations", "Global News & Politics"), ("Breaking News", "Global News & Politics"),
    ("Artificial Intelligence", "Technology & Innovation"), ("Cybersecurity", "Technology & Innovation"),
    ("Cryptocurrency", "Technology & Innovation"), ("Quantum Computing", "Technology & Innovation"),
    ("Space Exploration", "Technology & Innovation"), ("Renewable Energy", "Technology & Innovation"),
    ("Electric Vehicles", "Technology & Innovation"), ("Biotechnology", "Technology & Innovation"),
    ("Federal Reserve", "Business & Finance"), ("Stock Market", "Business & Finance"),
    ("Real Estate Market", "Business & Finance"), ("Inflation", "Business & Finance"),
    ("Mergers and Acquisitions", "Business & Finance"), ("Venture Capital", "Business & Finance"),
    ("Mental Health", "Health & Wellness"), ("Longevity Medicine", "Health & Wellness"),
    ("Nutrition Science", "Health & Wellness"), ("Public Health", "Health & Wellness"),
    ("Fitness Trends", "Health & Wellness"),
    ("Streaming Services", "Entertainment & Pop Culture"), ("Box Office", "Entertainment & Pop Culture"),
    ("Celebrity News", "Entertainment & Pop Culture"), ("Award Shows", "Entertainment & Pop Culture"),
    ("Gaming Industry", "Entertainment & Pop Culture"), ("Fashion Week", "Entertainment & Pop Culture"),
    ("Olympics", "Sports & Athletics"), ("World Cup", "Sports & Athletics"),
    ("Super Bowl", "Sports & Athletics"), ("Formula 1", "Sports & Athletics"),
    ("Premier League", "Sports & Athletics"), ("NBA Finals", "Sports & Athletics"),
    ("Remote Work", "Education & Career"), ("Higher Education", "Education & Career"),
    ("Online Learning", "Education & Career"), ("Labor Market", "Education & Career"),
    ("AI in Education", "Education & Career"),
    ("Sustainable Fashion", "Lifestyle & Sustainability"), ("Electric Aviation", "Lifestyle & Sustainability"),
    ("Vertical Farming", "Lifestyle & Sustainability"), ("Circular Economy", "Lifestyle & Sustainability"),
    ("Ecotourism", "Lifestyle & Sustainability"), ("Smart Homes", "Lifestyle & Sustainability"),
    ("Minimalism", "Lifestyle & Sustainability"), ("Plant-Based Diets", "Lifestyle & Sustainability"),
)


def seed_default_alert_topics(db: Session, organization_id: str) -> None:
    rows = db.scalars(select(GoogleAlertTopic).where(GoogleAlertTopic.organization_id == organization_id)).all()
    default_topics = {topic for topic, _category in DEFAULT_ALERT_TOPICS}
    # Keep older catalog rows for audit/history, but prevent them from being
    # selected after the workspace catalog is replaced by the approved list.
    for row in rows:
        if row.topic not in default_topics:
            row.active = False
    existing = {row.topic for row in rows}
    missing = [item for item in DEFAULT_ALERT_TOPICS if item[0] not in existing]
    if not missing:
        db.commit()
        return
    for index, (topic, category) in enumerate(missing, start=1):
        db.add(GoogleAlertTopic(id=f"alert_topic_{organization_id}_{abs(hash(topic)) % 10**12:012d}_{index}", organization_id=organization_id, topic=topic, category=category, active=True))
    db.commit()


def list_alert_topics(db: Session, organization_id: str) -> list[dict]:
    rows = db.scalars(select(GoogleAlertTopic).where(GoogleAlertTopic.organization_id == organization_id, GoogleAlertTopic.active.is_(True)).order_by(GoogleAlertTopic.category, GoogleAlertTopic.topic)).all()
    return [{"id": row.id, "topic": row.topic, "category": row.category, "active": row.active, "created_at": row.created_at, "updated_at": row.updated_at} for row in rows]


def choose_alert_topics(db: Session, organization_id: str, count: int, requested: list[str] | None = None) -> list[str]:
    seed_default_alert_topics(db, organization_id)
    rows = db.scalars(select(GoogleAlertTopic).where(GoogleAlertTopic.organization_id == organization_id, GoogleAlertTopic.active.is_(True))).all()
    if not rows:
        raise ValueError("No active Google Alerts topics are available")
    if requested is not None:
        active_topics = {row.topic for row in rows}
        topics = list(dict.fromkeys(topic.strip() for topic in requested if topic and topic.strip()))
        if len(topics) != count:
            raise ValueError(f"Choose exactly {count} unique Google Alerts topics")
        if any(topic not in active_topics for topic in topics):
            raise ValueError("One or more selected Google Alerts topics are not active in the workspace catalog")
        return topics
    return [row.topic for row in random.SystemRandom().sample(rows, min(count, len(rows)))]
