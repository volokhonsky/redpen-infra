"""
CLI: сменить статус у многих замечаний разом (и, по желанию, пометить их тегом).

Зачем: у API есть только поштучная смена статуса (`PATCH .../status`), а для
подготовки сайта к открытию нужно было, например, перевести в черновики всё
опубликованное за пределами принятых параграфов — полторы сотни замечаний.
Каждое изменение проходит через те же функции, что и ручки API
(`db.set_status_db`, `db.archive_remark`, `db.set_tags_db`), поэтому у каждого
замечания в журнале появляется обычная ревизия с резюме `--summary`.

Выбор замечаний — одним из способов (их можно сочетать, условия складываются
через «и»):
    --pages 038-999     диапазон ключей страниц, включительно, по числу;
    --from published    только с этим статусом;
    --tag was-published только с этим тегом;
    --ids <файл>        строки «<страница> <remarkId>», `#` — комментарий.

По умолчанию НИЧЕГО не пишет — печатает отчёт. Запись — с `--apply`; после неё
вызывается publisher.publish_all() (перерисует JSON, страницы и оглавление),
если не указан `--no-publish`.

Примеры (в контейнере API, где лежит БД):

    # Снять с сайта всё опубликованное после §3, запомнив, что оно было опубликовано
    python3 bulk_set_status.py --doc medinsky11klass --pages 038-999 \\
        --from published --to draft --add-tag was-published \\
        --summary "Подготовка к открытию: снято до приёмки" --apply

    # Вернуть снятое
    python3 bulk_set_status.py --doc medinsky11klass --tag was-published \\
        --from draft --to published --remove-tag was-published --apply

    # Принять список
    python3 bulk_set_status.py --doc medinsky11klass --ids accepted.txt \\
        --to published --summary "Приёмка §1—§3" --apply
"""

import argparse
import re
import sys
from typing import Dict, List, Optional, Set, Tuple

import db

TARGET_STATUSES = ("published", "draft", "archived")
SOURCE_STATUSES = TARGET_STATUSES

_RANGE_RE = re.compile(r"^(-?\d+)-(-?\d+)$")


class UsageError(ValueError):
    pass


def parse_range(text: str) -> Tuple[int, int]:
    m = _RANGE_RE.match(text.strip())
    if not m:
        raise UsageError(f"--pages ждёт диапазон вида 038-999, получено {text!r}")
    lo, hi = int(m.group(1)), int(m.group(2))
    if hi < lo:
        raise UsageError(f"--pages: конец диапазона {hi} меньше начала {lo}")
    return lo, hi


def parse_ids(lines: List[str]) -> Set[Tuple[str, str]]:
    """Строки «<страница> <remarkId>» -> множество пар. Страница — ключ файла
    (`038`), как в БД; короткая запись `38` дополняется нулями до трёх знаков."""
    result: Set[Tuple[str, str]] = set()
    for lineno, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 2:
            raise UsageError(f"--ids, строка {lineno}: ждём «<страница> <remarkId>», получено {raw.strip()!r}")
        page, remark_id = parts
        if page.isdigit():
            page = page.zfill(3)
        result.add((page, remark_id))
    return result


def _page_int(page_num: str) -> Optional[int]:
    try:
        return int(page_num)
    except ValueError:
        return None


def select(doc_id: str, pages: Optional[Tuple[int, int]] = None,
           from_status: Optional[str] = None, tag: Optional[str] = None,
           ids: Optional[Set[Tuple[str, str]]] = None) -> List[Dict]:
    """Замечания книги, подходящие под все заданные условия, по порядку страниц."""
    rows = db.list_remarks(doc_id=doc_id, status=from_status, tag=tag,
                           include_archived=True, limit=1_000_000)
    picked = []
    for r in rows:
        if pages is not None:
            n = _page_int(r["pageNum"])
            if n is None or not pages[0] <= n <= pages[1]:
                continue
        if ids is not None and (r["pageNum"], r["remarkId"]) not in ids:
            continue
        picked.append(r)
    picked.sort(key=lambda r: (_page_int(r["pageNum"]) or 0, r["pageNum"], r["remarkId"]))
    return picked


def apply_one(r: Dict, to_status: str, add_tag: Optional[str],
              remove_tag: Optional[str], summary: str) -> None:
    doc_id, page_num, remark_id = r["docId"], r["pageNum"], r["remarkId"]
    tags = list(r.get("tags") or [])
    new_tags = [t for t in tags if t != remove_tag] if remove_tag else list(tags)
    if add_tag and add_tag not in new_tags:
        new_tags.append(add_tag)
    if new_tags != tags:
        db.set_tags_db(doc_id, page_num, remark_id, new_tags, summary=summary)
    if r["status"] != to_status:
        if to_status == "archived":
            db.archive_remark(doc_id, page_num, remark_id, summary=summary)
        else:
            db.set_status_db(doc_id, page_num, remark_id, to_status, summary=summary)


def run(args: argparse.Namespace, out=sys.stdout) -> Dict[str, int]:
    if not (args.pages or args.from_status or args.tag or args.ids):
        raise UsageError("нужно хотя бы одно условие выбора: --pages, --from, --tag или --ids")
    pages = parse_range(args.pages) if args.pages else None
    ids = None
    if args.ids:
        with open(args.ids, "r", encoding="utf-8") as f:
            ids = parse_ids(f.readlines())

    picked = select(args.doc, pages, args.from_status, args.tag, ids)

    if ids is not None:
        found = {(r["pageNum"], r["remarkId"]) for r in picked}
        for page, remark_id in sorted(ids - found):
            print(f"  нет такого замечания (или не прошло фильтры): {page} {remark_id}", file=out)

    by_status: Dict[str, int] = {}
    for r in picked:
        by_status[r["status"]] = by_status.get(r["status"], 0) + 1
    changing = [r for r in picked if r["status"] != args.to]

    mode = "ЗАПИСЬ" if args.apply else "отчёт, без записи (--apply для записи)"
    print(f"[bulk_set_status] {args.doc}: выбрано {len(picked)} "
          f"({', '.join(f'{k}: {v}' for k, v in sorted(by_status.items())) or '—'}); "
          f"сменят статус на {args.to}: {len(changing)}. Режим: {mode}", file=out)
    pages_touched = sorted({r["pageNum"] for r in picked}, key=lambda p: (_page_int(p) or 0, p))
    if pages_touched:
        print(f"  страницы: {', '.join(pages_touched)}", file=out)
    if args.verbose:
        for r in picked:
            print(f"  {r['pageNum']} {r['remarkId']} {r['status']} -> {args.to}", file=out)

    if args.apply:
        for r in picked:
            apply_one(r, args.to, args.add_tag, args.remove_tag, args.summary)
        if not args.no_publish:
            import publisher
            result = publisher.publish_all()
            print(f"  publish_all: {result}", file=out)

    return {"selected": len(picked), "changing": len(changing)}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--doc", required=True, help="docId книги")
    p.add_argument("--pages", help="диапазон ключей страниц, например 038-999")
    p.add_argument("--from", dest="from_status", choices=SOURCE_STATUSES,
                   help="брать только замечания с этим статусом")
    p.add_argument("--tag", help="брать только замечания с этим тегом")
    p.add_argument("--ids", help="файл со строками «<страница> <remarkId>»")
    p.add_argument("--to", required=True, choices=TARGET_STATUSES, help="новый статус")
    p.add_argument("--add-tag", help="добавить тег выбранным замечаниям")
    p.add_argument("--remove-tag", help="снять тег с выбранных замечаний")
    p.add_argument("--summary", default="Массовая смена статуса",
                   help="резюме ревизии (до 200 символов)")
    p.add_argument("--apply", action="store_true", help="записать (по умолчанию — только отчёт)")
    p.add_argument("--no-publish", action="store_true", help="не вызывать publish_all после записи")
    p.add_argument("-v", "--verbose", action="store_true", help="перечислить каждое замечание")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    for name in ("add_tag", "remove_tag"):
        value = getattr(args, name)
        if value:
            setattr(args, name, db.normalize_tag(value))
    db.init_db()
    try:
        run(args)
    except UsageError as e:
        print(f"[bulk_set_status] ошибка: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
