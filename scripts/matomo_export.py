#!/usr/bin/env python3
"""
Экспорт access-лога Caddy в формат, который штатно читает Matomo.

Зачем преобразование: Caddy пишет структурированный JSON, а `import_logs.py`
из коробки знает `ncsa_extended` (он же combined) — привести лог к нему дешевле,
чем описывать регуляркой чужую структуру. Заодно на этом шаге делается то, чего
готовый инструмент про нас не знает:

  * выбрасываются запросы редактора и кабинета (`/app/`, `/cabinet/`, `/api/`,
    любой адрес с `?editor=1`) — в аналитику читателей им нельзя;
  * в адрес страницы вставляется параграф, поэтому отчёт Matomo по адресам
    складывается в иерархию «книга → § → страница», то есть в посещаемость
    по разделам. Настоящий адрес при этом теряется: см. `analytics.section_uri`.

Смещение в каждом файле лога запоминается, поэтому повторный запуск не отдаёт
Matomo одни и те же строки дважды (сам импортёр дублей не отсекает).

Пример:
    python3 scripts/matomo_export.py --log-dir /var/log/caddy \\
        --out /var/redpen-stats/matomo-import.log
"""

import argparse
import gzip
import hashlib
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analytics  # noqa: E402
import page_sections  # noqa: E402

DEFAULT_LOG_DIR = os.getenv("ANALYTICS_LOG_DIR", "/var/log/caddy")
DEFAULT_SITE_DIR = os.getenv("PUBLISH_DIR", "/srv/public")
DEFAULT_STATE = os.getenv("MATOMO_EXPORT_STATE", "/var/redpen-stats/matomo_export.json")


def open_log(path: str):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def signature(path: str) -> str:
    """Отпечаток первой строки: ротация или обрезка меняют его."""
    # Именно первая строка, а не первые N байт: дописанная в конец строка
    # меняла бы «первые 512 байт» у короткого файла, и весь лог читался бы
    # заново — то есть уезжал бы в Matomo дважды.
    try:
        with open_log(path) as f:
            head = f.readline()
    except OSError:
        return ""
    return hashlib.sha256(head.encode("utf-8", "replace")).hexdigest()[:16]


def log_files(log_dir: str) -> List[str]:
    if not os.path.isdir(log_dir):
        return []
    names = [n for n in os.listdir(log_dir)
             if n.startswith("access") and (n.endswith(".log") or n.endswith(".gz"))]
    paths = [os.path.join(log_dir, n) for n in names]
    return sorted(paths, key=lambda p: (os.path.getmtime(p), p))


def load_state(path: str) -> Dict[str, Dict[str, object]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: Dict[str, Dict[str, object]]) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def commit_state(state_path: str) -> bool:
    """Признать отданные строки импортированными.

    Экспорт двигает позицию не сразу: сначала пишет её рядом, в `.pending`, и
    только этот вызов делает её действующей. Порядок важен — если импорт упал,
    позиция не должна уехать, иначе строки пропадут навсегда: `import_logs.py`
    не возобновляемый, и второй попытки для них не будет.
    """
    pending = f"{state_path}.pending"
    if not os.path.exists(pending):
        return False
    os.replace(pending, state_path)
    return True


def export(log_dir: str, out_path: str, state_path: str,
           site_dir: Optional[str] = None, dry_run: bool = False,
           section_prefix: str = "§", own_hosts: Tuple[str, ...] = ()) -> int:
    """Дописать новые строки лога в файл для импорта. Возвращает их число."""
    state = load_state(state_path)
    manifests = page_sections.ManifestCache(site_dir) if site_dir else None
    written = 0

    directory = os.path.dirname(out_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    out = None if dry_run else open(out_path, "w", encoding="utf-8")
    try:
        for path in log_files(log_dir):
            sig = signature(path)
            if not sig:
                continue
            entry = state.get(path) or {}
            offset = int(entry.get("offset", 0)) if entry.get("signature") == sig else 0
            consumed = offset

            with open_log(path) as f:
                for lineno, line in enumerate(f):
                    if lineno < offset:
                        continue
                    consumed = lineno + 1
                    parsed = analytics.parse_line(line)
                    if not parsed:
                        continue
                    info = analytics.classify_path(parsed["uri"])
                    if info["private"]:
                        continue
                    # Предпросмотр редактора грузит настоящую страницу читателя
                    # в iframe — по адресу он неотличим от чтения, отличается
                    # только ссылающейся страницей.
                    if analytics.private_referer(parsed["referer"], own_hosts):
                        continue
                    uri = None
                    if manifests and info["kind"] == "page" and info["doc_id"]:
                        section = manifests.section_of(info["doc_id"], info["page_label"])
                        uri = analytics.section_uri(
                            info["doc_id"], info["page_label"],
                            section["id"] if section else None, section_prefix)
                    if out:
                        out.write(analytics.combined_line(parsed, uri) + "\n")
                    written += 1
            state[path] = {"signature": sig, "offset": consumed}
    finally:
        if out:
            out.close()

    if not dry_run:
        save_state(f"{state_path}.pending", state)
    return written


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--log-dir", default=DEFAULT_LOG_DIR)
    parser.add_argument("--out", help="файл для import_logs.py (кроме --commit)")
    parser.add_argument("--state", default=DEFAULT_STATE)
    parser.add_argument("--site-dir", default=DEFAULT_SITE_DIR,
                        help="каталог сайта: нужен, чтобы подставить параграф в адрес")
    parser.add_argument("--no-sections", action="store_true",
                        help="оставить настоящие адреса страниц")
    parser.add_argument("--section-prefix", default="§",
                        help="как назвать сегмент параграфа в адресе (по умолчанию §)")
    parser.add_argument("--host", action="append", default=[],
                        help="собственный хост: ссылки с него из /app/ и /cabinet/ отбрасываются")
    parser.add_argument("--dry-run", action="store_true",
                        help="посчитать строки, ничего не писать и не двигать смещение")
    parser.add_argument("--commit", action="store_true",
                        help="признать прошлый экспорт импортированным и сдвинуть позицию")
    args = parser.parse_args(argv)

    if args.commit:
        moved = commit_state(args.state)
        print("matomo-export: позиция сдвинута" if moved
              else "matomo-export: двигать нечего")
        return 0

    hosts = tuple(h.lower() for h in
                  (args.host or [os.getenv("DOMAIN", "medinsky.net")]))
    count = export(args.log_dir, args.out, args.state,
                   None if args.no_sections else args.site_dir, args.dry_run,
                   args.section_prefix, hosts)
    print(f"matomo-export: строк {count} → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
