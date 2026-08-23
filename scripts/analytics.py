"""
Разбор access-логов Caddy: подготовка к импорту в Matomo и счёт по контенту.

Модуль намеренно чистый: ни SQLite, ни сети, ни окружения — только функции над
строками. Всё, что ходит наружу, лежит в ``scripts/matomo_export.py`` и
``scripts/ops/redpen_stats.py``. Замысел целиком — ``docs/analytics-plan-2026-08.md``.

Разделение труда: посещаемость, источники, устройства, страны и визиты считает
Matomo (готовый продукт, импорт серверных логов — его штатная функция). Здесь
остаётся то, чего Matomo про нас не знает: какая страница какому параграфу
принадлежит, где разбора ещё нет и какие комментарии открывают по прямой ссылке.

Опознания посетителя тут нет: этим занимается Matomo, у него для этого своя
(настраиваемая) схема. Второй копии идентификаторов читателя мы не заводим.
"""

import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple
from urllib.parse import parse_qs, quote, urlsplit

# --------------------------------------------------------------------------
# Классификация пути
# --------------------------------------------------------------------------

_RE_PAGE = re.compile(r"^/(?P<doc>[A-Za-z0-9_-]+)/pages/(?P<label>[^/]+)/?$")
_RE_DOC = re.compile(r"^/(?P<doc>[A-Za-z0-9_-]+)/(index\.html)?$")
_RE_IMAGE = re.compile(r"^/(?P<doc>[A-Za-z0-9_-]+)/images/(?P<file>page_\d+)\.(png|jpe?g|webp)$")
_RE_ANNJSON = re.compile(r"^/(?P<doc>[A-Za-z0-9_-]+)/annotations/page_\d+\.json$")
_RE_TEXTJSON = re.compile(r"^/(?P<doc>[A-Za-z0-9_-]+)/text/page_\d+\.json$")
_RE_SPA = re.compile(r"^/(?P<doc>[A-Za-z0-9_-]+/)?document_index\.html$")
_RE_ASSET = re.compile(r"\.(js|css|svg|ico|woff2?|map)$", re.I)
_RE_META = re.compile(r"^/(robots\.txt|sitemap[^/]*\.xml|favicon\.[a-z]+)$")

#: Пути редактора и кабинета. Их запросы не попадают ни в Matomo, ни в наши
#: счётчики: там следы участников закрытого круга, а не читателей, и связывать
#: их с адресом прямо запрещает docs/anonymity-model.md.
_RE_PRIVATE = re.compile(r"^/(app|cabinet)(/|$)|^/\.hooks/|^/api/")

#: То же самое, но со стороны ссылающейся страницы.
_RE_PRIVATE_REFERER = re.compile(r"^/(app|cabinet)(/|$)")


def private_referer(referer: str, own_hosts: Tuple[str, ...] = ()) -> bool:
    """Запрос сделан со страницы редактора или кабинета?

    Проверка не косметическая. Предпросмотр в `/app/` грузит в iframe
    **настоящую страницу читателя**, да ещё и с `?only=<id>` (см.
    `templates/app/app.js`). По самому адресу такой запрос неотличим от чтения:
    без этой проверки собственная правка засчитывалась бы как визит, а разбор
    комментария — как пересылку, то есть портил бы единственную метрику,
    которая говорит, что читателям пересылают.

    Признак — заголовок `Referer`: у навигации в iframe там адрес объемлющей
    страницы, а `Referrer-Policy: no-referrer-when-downgrade` внутри https его
    сохраняет.
    """
    if not referer:
        return False
    split = urlsplit(referer)
    host = (split.hostname or "").lower()
    if own_hosts and host not in own_hosts:
        return False
    return bool(_RE_PRIVATE_REFERER.match(split.path or ""))


def classify_path(uri: str) -> Dict[str, Any]:
    """Разложить URI на вид запроса, книгу, страницу и интересные параметры."""
    split = urlsplit(uri)
    path = split.path or "/"
    query = parse_qs(split.query, keep_blank_values=True)

    def q(name: str) -> Optional[str]:
        values = query.get(name)
        return values[0] if values else None

    out: Dict[str, Any] = {
        "path": path,
        "kind": "other",
        "doc_id": None,
        "page_label": None,
        "ann_id": q("only"),
        "private": False,
        # ?editor=1 на странице читателя — это загрузка редактора поверх
        # статики, то есть снова не читатель.
        "editor_flag": q("editor") == "1",
        "legacy_param": q("page") or q("p"),
        "tag_filter": bool(q("tags") or q("notags") or q("showDrafts")),
    }

    if _RE_PRIVATE.search(path) or out["editor_flag"]:
        out["private"] = True
        out["kind"] = "private"
        return out

    m = _RE_PAGE.match(path)
    if m:
        out.update(kind="page", doc_id=m.group("doc"), page_label=m.group("label"))
        return out
    m = _RE_IMAGE.match(path)
    if m:
        out.update(kind="image", doc_id=m.group("doc"))
        return out
    m = _RE_ANNJSON.match(path) or _RE_TEXTJSON.match(path)
    if m:
        out.update(kind="data", doc_id=m.group("doc"))
        return out
    m = _RE_SPA.match(path)
    if m:
        out.update(kind="spa", doc_id=(m.group("doc") or "").rstrip("/") or None)
        return out
    if path in ("/", "/index.html"):
        out["kind"] = "home"
        return out
    if path.startswith("/blog"):
        out["kind"] = "blog"
        return out
    if path.endswith(".zip"):
        out["kind"] = "download"
        return out
    if _RE_META.match(path):
        out["kind"] = "meta"
        return out
    if _RE_ASSET.search(path):
        out["kind"] = "asset"
        return out
    m = _RE_DOC.match(path)
    if m:
        out.update(kind="doc", doc_id=m.group("doc"))
        return out
    return out


# --------------------------------------------------------------------------
# Источник перехода и класс клиента
# --------------------------------------------------------------------------

_SEARCH_HOSTS = (
    "google", "yandex", "bing", "duckduckgo", "yahoo", "mail.ru", "rambler",
    "ecosia", "startpage", "seznam", "baidu",
)
_SOCIAL_HOSTS = {
    "t.me": "telegram", "telegram.me": "telegram", "web.telegram.org": "telegram",
    "vk.com": "vk", "m.vk.com": "vk", "ok.ru": "ok",
    "facebook.com": "facebook", "l.facebook.com": "facebook",
    "twitter.com": "twitter", "x.com": "twitter", "t.co": "twitter",
    "reddit.com": "reddit", "out.reddit.com": "reddit",
    "instagram.com": "instagram", "l.instagram.com": "instagram",
    "livejournal.com": "livejournal", "dzen.ru": "dzen",
    "news.ycombinator.com": "hn",
}


def referer_source(referer: str, own_hosts: Tuple[str, ...] = ()) -> Tuple[str, str]:
    """(источник, хост) для заголовка Referer.

    Нужен нашим счётчикам только чтобы отличать внутренние переходы от внешних;
    полноценный отчёт по источникам строит Matomo.
    """
    if not referer:
        return "direct", ""
    host = (urlsplit(referer).hostname or "").lower()
    if not host:
        return "direct", ""
    if host in own_hosts:
        return "internal", host
    bare = host[4:] if host.startswith("www.") else host
    if bare in _SOCIAL_HOSTS:
        return "social", _SOCIAL_HOSTS[bare]
    if any(s in host for s in _SEARCH_HOSTS):
        return "search", bare
    return "link", bare


_RE_BOT = re.compile(
    r"bot|crawl|spider|slurp|scrap|preview|monitor|fetcher|feed|"
    r"headlesschrome|phantomjs|python-requests|python-urllib|curl/|wget|"
    r"go-http-client|libwww|java/|okhttp|axios|node-fetch|httpx|"
    r"ahrefs|semrush|mj12|dotbot|petalbot|bytespider|dataforseo|"
    r"gptbot|claudebot|ccbot|perplexity|anthropic|openai",
    re.I,
)
_RE_MOBILE = re.compile(r"mobile|android|iphone|ipad|ipod|opera mini", re.I)


def ua_class(user_agent: str) -> str:
    """``bot`` | ``mobile`` | ``desktop`` | ``unknown``.

    Нужен, чтобы наши счётчики контента не считали обходы роботов за спрос.
    Своё, более точное опознание есть и у Matomo — оно применяется на импорте.
    """
    if not user_agent:
        return "unknown"
    if _RE_BOT.search(user_agent):
        return "bot"
    if _RE_MOBILE.search(user_agent):
        return "mobile"
    if "mozilla" in user_agent.lower():
        return "desktop"
    return "unknown"


# --------------------------------------------------------------------------
# Разбор строки лога
# --------------------------------------------------------------------------

def _header(headers: Dict[str, Any], name: str) -> str:
    """Заголовок из лога Caddy: значения там списками."""
    for key, value in (headers or {}).items():
        if key.lower() == name.lower():
            if isinstance(value, list):
                return value[0] if value else ""
            return str(value)
    return ""


def parse_line(line: str) -> Optional[Dict[str, Any]]:
    """Строка JSON-лога Caddy → словарь запроса; None, если это не access-лог.

    В том же файле оказываются служебные записи (перезагрузка конфигурации,
    ошибки TLS) — у них нет ``request``, и они молча пропускаются.
    """
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        rec = json.loads(line)
    except ValueError:
        return None
    req = rec.get("request")
    if not isinstance(req, dict) or "uri" not in req:
        return None
    ts = rec.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    headers = req.get("headers") or {}
    return {
        "ts": datetime.fromtimestamp(float(ts), tz=timezone.utc),
        "ip": req.get("remote_ip") or req.get("client_ip") or "",
        "method": req.get("method") or "",
        "host": (req.get("host") or "").lower(),
        "uri": req.get("uri") or "",
        "proto": req.get("proto") or "",
        "status": int(rec.get("status") or 0),
        "size": int(rec.get("size") or 0),
        "duration_ms": int(round(float(rec.get("duration") or 0) * 1000)),
        "user_agent": _header(headers, "User-Agent"),
        "referer": _header(headers, "Referer"),
    }


# --------------------------------------------------------------------------
# Экспорт в Matomo: NCSA extended (он же combined)
# --------------------------------------------------------------------------

def section_uri(doc_id: str, label: str, section_id: Optional[str],
                prefix: str = "§") -> str:
    """Путь, в который вставлен параграф: книга → § → страница.

    Matomo строит отчёт по адресам страниц иерархически, по сегментам пути.
    Готовый инструмент про наши параграфы ничего не знает, но если параграф
    оказался сегментом адреса, «посещаемость по разделам» получается сама.

    Цена приёма: адрес в отчёте не совпадает с настоящим, и переход по строке
    отчёта ведёт в никуда. Настоящий адрес — `/<doc>/pages/<label>/`.

    ``prefix`` вынесен в параметр на случай, если Matomo покажет `§` в
    процентной кодировке: тогда сгодится ASCII-вариант вроде ``para-``.
    """
    section = f"{prefix}{section_id}" if section_id else "вне-параграфов"
    # Без завершающего слэша: адрес, кончающийся на «/», Matomo считает
    # каталогом и подвешивает под него лишний уровень «/index».
    return f"/{doc_id}/{quote(section)}/{quote(str(label))}"


def _quoted(value: str) -> str:
    return (value or "").replace("\\", "\\\\").replace('"', '\\"')


def combined_line(parsed: Dict[str, Any], uri: Optional[str] = None) -> str:
    """Строка в формате ncsa_extended — его import_logs.py читает штатно.

    Caddy пишет JSON, Matomo из коробки такой JSON не разбирает; преобразование
    в общепринятый формат дешевле, чем регулярка на структурированный лог.
    """
    stamp = parsed["ts"].astimezone(timezone.utc).strftime("%d/%b/%Y:%H:%M:%S +0000")
    request = f"{parsed['method']} {uri or parsed['uri']} {parsed['proto'] or 'HTTP/1.1'}"
    return (f'{parsed["ip"] or "-"} - - [{stamp}] "{_quoted(request)}" '
            f'{parsed["status"]} {parsed["size"]} '
            f'"{_quoted(parsed["referer"]) or "-"}" "{_quoted(parsed["user_agent"])}"')


# --------------------------------------------------------------------------
# Счётчики контента
# --------------------------------------------------------------------------

def build_hit(parsed: Dict[str, Any],
              own_hosts: Tuple[str, ...] = ()) -> Optional[Dict[str, Any]]:
    """Разобранная строка → строка таблицы ``hits``. None — запрос не наш.

    Ни адреса, ни производной от него: посещаемостью занимается Matomo, а здесь
    считается контент — сколько раз открыли страницу, параграф, комментарий.
    """
    info = classify_path(parsed["uri"])
    if info["private"] or private_referer(parsed["referer"], own_hosts):
        return None
    if parsed["method"] not in ("GET", "HEAD"):
        return None
    source, ref_host = referer_source(parsed["referer"], own_hosts)
    return {
        "ts": parsed["ts"].isoformat(),
        "day": parsed["ts"].date().isoformat(),
        "kind": info["kind"],
        "doc_id": info["doc_id"],
        "page_label": info["page_label"],
        "ann_id": info["ann_id"],
        "legacy_param": info["legacy_param"],
        "tag_filter": 1 if info["tag_filter"] else 0,
        "path": info["path"],
        "status": parsed["status"],
        "referer_source": source,
        "ua_class": ua_class(parsed["user_agent"]),
    }
