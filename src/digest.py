import argparse
import datetime as dt
import difflib
import json
import logging
import os
import re
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote_plus

import feedparser
import requests
from dateutil import parser as dt_parser


TIMEOUT = 20
MAX_TELEGRAM_MESSAGE = 4000


@dataclass
class DigestItem:
    title: str
    category: str
    source: str
    date: dt.datetime
    summary: str
    plain_description: str
    importance_reason: str
    keywords: List[str]
    link: str
    doi: Optional[str] = None
    pmid: Optional[str] = None
    score: float = 0.0
    raw_type: str = ""
    extra: Dict[str, str] = field(default_factory=dict)


def load_topics(path: str) -> Tuple[List[str], List[str]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("topics", []), data.get("priority_keywords", [])


def parse_date(value: str) -> dt.datetime:
    if not value:
        return dt.datetime.now(dt.timezone.utc)
    try:
        parsed = dt_parser.parse(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except Exception:
        return dt.datetime.now(dt.timezone.utc)


def within_lookback(item_date: dt.datetime, lookback_hours: int) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    return item_date >= now - dt.timedelta(hours=lookback_hours)


def fetch_crossref(topics: List[str], lookback_hours: int, mailto: str) -> List[DigestItem]:
    items = []
    base = "https://api.crossref.org/works"
    for topic in topics[:10]:
        params = {
            "query": topic,
            "rows": 5,
            "sort": "published",
            "order": "desc",
            "mailto": mailto,
        }
        try:
            r = requests.get(base, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            records = r.json().get("message", {}).get("items", [])
        except Exception as e:
            logging.warning("Crossref fetch failed for '%s': %s", topic, e)
            continue

        for rec in records:
            title = (rec.get("title") or [""])[0]
            journal = (rec.get("container-title") or ["Unknown Journal"])[0]
            doi = rec.get("DOI")
            date_parts = rec.get("created", {}).get("date-time") or ""
            pub_date = parse_date(date_parts)
            if not within_lookback(pub_date, lookback_hours):
                continue
            link = f"https://doi.org/{doi}" if doi else rec.get("URL", "")
            item_type = rec.get("type", "paper")
            category = "리뷰논문" if "review" in item_type.lower() else "논문"
            items.append(DigestItem(
                title=title,
                category=category,
                source=journal,
                date=pub_date,
                summary="",
                plain_description=rec.get("abstract", "") or title,
                importance_reason="최신 학술 출처에서 확인된 연구입니다.",
                keywords=[],
                link=link,
                doi=doi,
                raw_type=item_type,
            ))
    return items


def fetch_pubmed(topics: List[str], lookback_hours: int, email: str) -> List[DigestItem]:
    items = []
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    for topic in topics[:10]:
        try:
            search = requests.get(
                f"{base}/esearch.fcgi",
                params={"db": "pubmed", "retmode": "json", "term": topic, "retmax": 5, "sort": "pub date", "email": email},
                timeout=TIMEOUT,
            )
            search.raise_for_status()
            ids = search.json().get("esearchresult", {}).get("idlist", [])
            if not ids:
                continue
            summary = requests.get(
                f"{base}/esummary.fcgi",
                params={"db": "pubmed", "retmode": "json", "id": ",".join(ids), "email": email},
                timeout=TIMEOUT,
            )
            summary.raise_for_status()
            data = summary.json().get("result", {})
        except Exception as e:
            logging.warning("PubMed fetch failed for '%s': %s", topic, e)
            continue

        for pmid in ids:
            rec = data.get(pmid, {})
            title = rec.get("title", "")
            journal = rec.get("fulljournalname", "PubMed")
            pub_date = parse_date(rec.get("pubdate", ""))
            if not within_lookback(pub_date, lookback_hours):
                continue
            link = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
            ptypelist = [x.lower() for x in rec.get("pubtype", [])]
            category = "리뷰논문" if any("review" in x for x in ptypelist) else "논문"
            items.append(DigestItem(title=title, category=category, source=journal, date=pub_date, summary="", plain_description=title,
                                    importance_reason="PubMed에서 최근 등록된 문헌입니다.", keywords=[], link=link, pmid=pmid, raw_type=",".join(ptypelist)))
    return items


def fetch_europe_pmc(topics: List[str], lookback_hours: int) -> List[DigestItem]:
    items = []
    base = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
    for topic in topics[:8]:
        try:
            r = requests.get(base, params={"query": topic, "format": "json", "pageSize": 5, "sort": "FIRST_PDATE_D desc"}, timeout=TIMEOUT)
            r.raise_for_status()
            records = r.json().get("resultList", {}).get("result", [])
        except Exception as e:
            logging.warning("Europe PMC fetch failed for '%s': %s", topic, e)
            continue

        for rec in records:
            title = rec.get("title", "")
            journal = rec.get("journalTitle", "Europe PMC")
            pub_date = parse_date(rec.get("firstPublicationDate", ""))
            if not within_lookback(pub_date, lookback_hours):
                continue
            src = rec.get("source", "")
            ext_id = rec.get("id", "")
            link = f"https://europepmc.org/article/{src}/{ext_id}" if src and ext_id else "https://europepmc.org"
            ptype = rec.get("pubType", "")
            category = "프리프린트" if "preprint" in ptype.lower() else "논문"
            items.append(DigestItem(title=title, category=category, source=journal, date=pub_date, summary="", plain_description=title,
                                    importance_reason="Europe PMC에서 수집된 최신 문헌입니다.", keywords=[], link=link,
                                    doi=rec.get("doi"), raw_type=ptype))
    return items


def fetch_google_news(topics: List[str], lookback_hours: int) -> List[DigestItem]:
    items = []
    for topic in topics[:12]:
        q = quote_plus(topic)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            logging.warning("Google News RSS failed for '%s': %s", topic, e)
            continue

        for entry in feed.entries[:4]:
            pub_date = parse_date(getattr(entry, "published", ""))
            if not within_lookback(pub_date, lookback_hours):
                continue
            source = getattr(entry, "source", {}).get("title", "Google News") if hasattr(entry, "source") else "Google News"
            desc = re.sub("<[^<]+?>", "", getattr(entry, "summary", ""))
            items.append(DigestItem(title=getattr(entry, "title", ""), category="기사", source=source, date=pub_date,
                                    summary="", plain_description=desc, importance_reason="산업/시장 동향 파악용 뉴스입니다.",
                                    keywords=[], link=getattr(entry, "link", "")))
    return items


def deduplicate(items: List[DigestItem]) -> List[DigestItem]:
    seen = set()
    unique = []
    for it in items:
        keys = [f"doi:{it.doi}" if it.doi else "", f"pmid:{it.pmid}" if it.pmid else "", f"url:{it.link}" if it.link else ""]
        if any(k and k in seen for k in keys):
            continue
        duplicate_title = False
        for u in unique:
            if difflib.SequenceMatcher(None, it.title.lower(), u.title.lower()).ratio() >= 0.92:
                duplicate_title = True
                break
        if duplicate_title:
            continue
        for k in keys:
            if k:
                seen.add(k)
        unique.append(it)
    return unique


def score_item(item: DigestItem, topics: List[str], priority_keywords: List[str]) -> float:
    s = 0.0
    text = f"{item.title} {item.plain_description}".lower()
    for kw in priority_keywords:
        if kw.lower() in text:
            s += 3.0
            item.keywords.append(kw)
    for kw in topics:
        if kw.lower() in item.title.lower():
            s += 1.3
    age_hours = (dt.datetime.now(dt.timezone.utc) - item.date).total_seconds() / 3600
    s += max(0, 2.0 - age_hours / 12)
    if item.category in ["논문", "리뷰논문", "프리프린트"]:
        s += 1.5
    if item.category == "리뷰논문":
        s += 1.0
    if item.category == "기사" and len(item.keywords) == 0:
        s -= 2.0
    return s


def fallback_summary(item: DigestItem) -> Tuple[str, str, str]:
    one_line = (item.plain_description or item.title).strip().replace("\n", " ")[:140]
    easy = f"이 소식은 '{item.title[:50]}' 주제의 최신 동향을 빠르게 보여줍니다."
    why = item.importance_reason
    return one_line, easy, why


def item_type_label(category: str) -> str:
    if category == "기사":
        return "기사"
    if category == "프리프린트":
        return "프리프린트"
    if category == "리뷰논문":
        return "리뷰논문"
    return "논문"


def item_icon(category: str) -> str:
    if category == "기사":
        return "📰"
    if category == "프리프린트":
        return "📄"
    return "🧪"


def score_to_stars(score: float) -> str:
    if score >= 8:
        return "★★★★★"
    if score >= 6:
        return "★★★★☆"
    if score >= 4:
        return "★★★☆☆"
    if score >= 2:
        return "★★☆☆☆"
    return "★☆☆☆☆"


def make_three_line_summary(item: DigestItem) -> List[str]:
    kw_text = ", ".join(item.keywords[:2]) if item.keywords else "식품 유화·유지가공"
    source_text = item.source if item.source else "주요 학술/산업 출처"
    title_short = re.sub(r"\s+", " ", item.title).strip()[:58]
    line1 = f"이 [{item_type_label(item.category)}] 자료는 {kw_text} 관련 최근 동향을 다룹니다."
    line2 = f"핵심 포인트: {title_short}"
    line3 = f"{source_text} 기반 정보로 현장 적용 아이디어를 빠르게 확인할 수 있습니다."
    return [line1, line2, line3]


def make_100_char_summary(item: DigestItem) -> str:
    kw_text = ", ".join(item.keywords[:2]) if item.keywords else "식품 유화와 유지가공"
    text = f"{item_type_label(item.category)} 한 건으로 {kw_text}의 최신 흐름을 쉽게 파악하고 제품 안정화·품질 개선 방향을 잡는 데 도움됩니다."
    clean = re.sub(r"\s+", " ", text).strip()
    if len(clean) <= 110:
        return clean
    return clean[:107].rstrip() + "..."


def make_study_topics(item: DigestItem) -> List[str]:
    text = f"{item.title} {item.plain_description}".lower()
    pool: List[str] = []
    if "pickering" in text:
        pool.append("Pickering emulsion의 안정화 원리")
    if "oleogel" in text:
        pool.append("oleogel과 saturated fat replacement의 관계")
    if "oxid" in text:
        pool.append("lipid oxidation 억제 전략(항산화제/포장/공정)")
    if "protein" in text or "plant" in text:
        pool.append("식물성 단백질 기반 유화 안정화 설계")
    if "interesterification" in text:
        pool.append("효소적 인터에스테르화 반응 조건 최적화")
    if "crystall" in text:
        pool.append("지방 결정화 거동과 제품 텍스처의 관계")
    if "refin" in text or "deodorization" in text or "degumming" in text:
        pool.append("식용유 정제 공정(탈검·탈취·품질지표) 이해")
    if not pool:
        pool.extend([
            "식품 유화 시스템의 기본 원리",
            "O/W emulsion과 W/O emulsion 차이",
            "계면 안정성과 유화 안정성",
            "유지 산화와 산패 관리",
            "식용유 정제 공정의 이해",
        ])
    return pool[:3]


def generate_openai_summary(item: DigestItem, model: str, api_key: str) -> Tuple[str, str, str]:
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        prompt = (
            "다음 항목을 비전공자도 이해할 수 있도록 한국어로 요약하세요."
            "출력은 JSON 형태로 one_line, easy, why 키를 포함하세요.\n\n"
            f"제목: {item.title}\n"
            f"설명: {item.plain_description[:1500]}\n"
            f"구분: {item.category}\n"
            f"출처: {item.source}\n"
        )
        response = client.responses.create(model=model, input=prompt, temperature=0.2)
        text = response.output_text.strip()
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            parsed = json.loads(text[start:end + 1])
            return parsed.get("one_line", ""), parsed.get("easy", ""), parsed.get("why", "")
    except Exception as e:
        logging.warning("OpenAI summary failed: %s", e)
    return fallback_summary(item)


def format_item(idx: int, item: DigestItem) -> str:
    date_str = item.date.astimezone(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d %H:%M KST")
    icon = item_icon(item.category)
    tlabel = item_type_label(item.category)
    stars = score_to_stars(item.score)
    bullets = make_three_line_summary(item)
    one_line_100 = make_100_char_summary(item)
    study_topics = make_study_topics(item)
    return (
        f"━━━━━━━━━━━━━━━━━━\n"
        f"[{idx}] [{tlabel}]\n\n"
        f"0. 중요도\n"
        f"{stars}\n\n"
        f"1. 논문명 또는 기사 제목\n"
        f"{icon} {item.title}\n\n"
        f"2. 해당 학회 및 기사 낸 곳\n"
        f"🏛 {item.source}\n"
        f"📅 {date_str}\n\n"
        f"3. 3줄 요약\n"
        f"• {bullets[0]}\n"
        f"• {bullets[1]}\n"
        f"• {bullets[2]}\n\n"
        f"4. 100자 요약\n"
        f"{one_line_100}\n\n"
        f"5. 관련 내용 엮어서 추가로 공부하면 좋을 것\n"
        + "".join(f"• {t}\n" for t in study_topics)
        + f"\n🔗 링크\n{item.link}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
    )


def render_digest_message(items: List[DigestItem], lookback_hours: int, priority_keywords: List[str]) -> str:
    today_kst = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).strftime("%Y-%m-%d")
    top_keywords = []
    for it in items:
        for kw in it.keywords:
            if kw not in top_keywords:
                top_keywords.append(kw)
    if not top_keywords:
        top_keywords = priority_keywords[:3]
    keyline = ", ".join(top_keywords[:5])
    header = (
        "🧪 Food Emulsion & Lipid Processing Daily Digest\n"
        f"📅 {today_kst} | Morning Brief\n"
        f"📦 오늘 브리핑: {len(items)}건\n"
        f"🔎 주요 키워드: {keyline}\n\n"
        f"(수집 기준: 최근 {lookback_hours}시간)\n\n"
    )
    if not items:
        return header + "수집된 항목이 없습니다."
    body = "\n".join(format_item(i + 1, it) for i, it in enumerate(items))
    return header + body


def chunk_text(text: str, max_len: int = MAX_TELEGRAM_MESSAGE) -> List[str]:
    chunks = []
    cur = ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > max_len:
            chunks.append(cur)
            cur = line
        else:
            cur += line
    if cur:
        chunks.append(cur)
    return chunks


def send_telegram(messages: List[str], token: str, chat_id: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for m in messages:
        try:
            r = requests.post(url, json={"chat_id": chat_id, "text": m}, timeout=TIMEOUT)
            r.raise_for_status()
            logging.info("Telegram message sent (%d chars)", len(m))
        except Exception as e:
            logging.error("Telegram send failed: %s", e)


def run(dry_run: bool) -> int:
    topics, priority = load_topics("config/topics.json")
    lookback_hours = int(os.getenv("LOOKBACK_HOURS", "24"))
    max_items = int(os.getenv("MAX_ITEMS", "12"))
    openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_key = os.getenv("OPENAI_API_KEY", "")

    logging.info("Collecting items for lookback=%sh", lookback_hours)
    all_items = []
    all_items.extend(fetch_crossref(topics, lookback_hours, os.getenv("CROSSREF_MAILTO", "")))
    all_items.extend(fetch_pubmed(topics, lookback_hours, os.getenv("NCBI_EMAIL", "")))
    all_items.extend(fetch_europe_pmc(topics, lookback_hours))
    all_items.extend(fetch_google_news(topics, lookback_hours))

    logging.info("Collected raw items: %d", len(all_items))
    unique = deduplicate(all_items)
    logging.info("After deduplication: %d", len(unique))

    for it in unique:
        it.score = score_item(it, topics, priority)

    ranked = sorted(unique, key=lambda x: x.score, reverse=True)[:max_items]

    for it in ranked:
        if openai_key:
            one_line, easy, why = generate_openai_summary(it, openai_model, openai_key)
        else:
            one_line, easy, why = fallback_summary(it)
        it.summary = one_line
        it.extra["easy"] = easy
        it.extra["why"] = why

    final_text = render_digest_message(ranked, lookback_hours, priority)
    messages = chunk_text(final_text)

    if dry_run:
        logging.info("DRY RUN MODE: Telegram not sent.")
        for i, m in enumerate(messages, 1):
            print(f"\n--- message chunk {i}/{len(messages)} ---\n{m}")
    else:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if not token or not chat_id:
            logging.error("Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID")
            return 1
        send_telegram(messages, token, chat_id)
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Food Emulsion & Lipid Processing Daily Digest")
    p.add_argument("--dry-run", default="true", help="true/false")
    return p.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = parse_args()
    dry = str(args.dry_run).lower() == "true"
    sys.exit(run(dry))
