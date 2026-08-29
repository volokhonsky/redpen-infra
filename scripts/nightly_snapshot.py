#!/usr/bin/env python3
"""Ночной снапшот: выгрузить аннотации из БД в git-клон redpen-publish.

Канон аннотаций — SQLite. Гит держит переносимую копию сайта: офлайн-артефакт,
резервную копию и вход для сборки. Между выкладками снапшот отстаёт от базы, и
это создаёт ловушку: если собрать сайт из устаревшего клона и запушить,
content-sync раскатает старые страницы поверх правильных. Ночной прогон
убирает ловушку — к утру клон всегда совпадает с базой.

Что делает:
  1. пишет `<doc>/remarks/page_NNN.json` из БД (тот же рендер, что у
     живого публикатора — `publisher.render_page_static`);
  2. перерисовывает `<doc>/pages/<label>/index.html` и оглавление, потому что
     просмотрщик читает не JSON, а инлайновый блок внутри HTML;
  3. коммитит и пушит, если что-то изменилось.

Почему раз в сутки, а не на каждую правку: коммит на каждое сохранение — это
публичная хронология работы редакторов с точностью до минуты (часы суток
выдают часовой пояс, а при нескольких участниках — и людей). Суточная пачка
огрубляет её до дня, как и всё остальное в docs/anonymity-model.md.

По умолчанию НИЧЕГО не пишет — печатает отчёт. `--apply` пишет файлы и
коммитит, `--push` дополнительно пушит.

Пример (в контейнере API, где лежит БД):
    python3 scripts/nightly_snapshot.py --repo /var/redpen-data --push
"""

import argparse
import datetime
import os
import subprocess
import sys
from typing import List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "api"))

# Модули проекта импортируются лениво, внутри функций. Скрипт запускается в двух
# очень разных местах: в контейнере API (там есть БД и весь код) и на хосте, где
# из проекта может не быть ничего. В режиме `--from --no-pages` хосту не нужен
# ни один модуль проекта — только git.

COMMIT_AUTHOR_NAME = os.environ.get("SNAPSHOT_AUTHOR_NAME", "redpen-bot")
COMMIT_AUTHOR_EMAIL = os.environ.get("SNAPSHOT_AUTHOR_EMAIL", "bot@medinsky.net")


def _run(args: List[str], cwd: str) -> Tuple[int, str]:
    proc = subprocess.run(args, cwd=cwd, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, text=True)
    return proc.returncode, proc.stdout.strip()


def export_remarks(repo: str, doc_id: Optional[str]) -> List[str]:
    """Записать JSON страниц из БД. Возвращает список изменившихся файлов."""
    import db
    import publisher

    changed = []
    for doc, page_num in db.list_pages():
        if doc_id and doc != doc_id:
            continue
        rendered = publisher.render_page_static(doc, page_num)
        out_dir = os.path.join(repo, doc, "remarks")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"page_{page_num}.json")
        # Байт в байт как у живого публикатора и export_remarks.py:
        # иначе файлы дёргались бы туда-обратно в зависимости от того, кто
        # писал последним.
        payload = publisher.json.dumps(rendered, ensure_ascii=False, indent=2)
        try:
            with open(path, "r", encoding="utf-8") as f:
                if f.read() == payload:
                    continue
        except OSError:
            pass
        with open(path, "w", encoding="utf-8") as f:
            f.write(payload)
        changed.append(os.path.relpath(path, repo))
    return changed


def copy_tree(src_dir: str, out_dir: str, repo: str, suffix: str = "") -> List[str]:
    """Скопировать файлы каталога, пропуская совпадающие. Изменившиеся — в ответ."""
    changed = []
    for root, _dirs, files in os.walk(src_dir):
        rel_root = os.path.relpath(root, src_dir)
        target_root = out_dir if rel_root == "." else os.path.join(out_dir, rel_root)
        os.makedirs(target_root, exist_ok=True)
        for name in sorted(files):
            if suffix and not name.endswith(suffix):
                continue
            src_path = os.path.join(root, name)
            dst_path = os.path.join(target_root, name)
            with open(src_path, "rb") as f:
                payload = f.read()
            try:
                with open(dst_path, "rb") as f:
                    if f.read() == payload:
                        continue
            except OSError:
                pass
            with open(dst_path, "wb") as f:
                f.write(payload)
            changed.append(os.path.relpath(dst_path, repo))
    return changed


def copy_remarks(src: str, repo: str, doc_id: Optional[str]) -> List[str]:
    """Взять готовые JSON из каталога вместо БД. Возвращает изменившиеся файлы.

    Нужно там, где база и git-клон не видны одному процессу — а на проде это
    именно так: база смонтирована только в контейнер API (uid 10001), клон
    лежит на хосте и принадлежит root. Контейнер выгружает JSON туда, куда уже
    имеет право писать (`export_remarks.py --to /var/redpen-data`), а хост
    забирает их отсюда. Ни новых монтирований, ни смены владельца, ни ключа с
    правом записи внутри контейнера, который смотрит в интернет."""
    changed = []
    for doc in sorted(os.listdir(src)):
        if doc_id and doc != doc_id:
            continue
        src_dir = os.path.join(src, doc, "remarks")
        if not os.path.isdir(src_dir):
            continue
        changed += copy_tree(src_dir, os.path.join(repo, doc, "remarks"), repo,
                             suffix=".json")
    return changed


def copy_pages(src: str, repo: str, doc_id: Optional[str]) -> List[str]:
    """Забрать и HTML страниц читателя.

    Обязательно с тех пор, как API перерисовывает страницу на каждую правку:
    content-sync раскатывает `pages/` из git-снапшота, и если снапшот отстал,
    свежие страницы будут затёрты старыми при первой же синхронизации. Именно
    это и случилось при выкладке 2026-08-21."""
    changed = []
    for doc in sorted(os.listdir(src)):
        if doc_id and doc != doc_id:
            continue
        src_dir = os.path.join(src, doc, "pages")
        if not os.path.isdir(src_dir):
            continue
        changed += copy_tree(src_dir, os.path.join(repo, doc, "pages"), repo)
    return changed


def rebuild_pages(repo: str, doc_id: Optional[str]) -> int:
    """Перерисовать страницы читателя. Возвращает число обработанных документов.

    Метка «последнее обновление» сохраняется у страниц, чьё содержимое не
    изменилось (`page_html._write_page_preserving_stamp`), иначе каждую ночь
    менялись бы все 448 файлов разом и в коммите не было бы видно сути.
    """
    import page_html

    docs = 0
    for name in sorted(os.listdir(repo)):
        if doc_id and name != doc_id:
            continue
        doc_dir = os.path.join(repo, name)
        if not os.path.isdir(doc_dir) or name.startswith("."):
            continue
        if not os.path.exists(os.path.join(doc_dir, "metadata.json")):
            continue
        page_html.build_pages(doc_dir, page_html.day_stamp())
        docs += 1
    return docs


def git_status(repo: str) -> List[str]:
    code, out = _run(["git", "status", "--porcelain"], repo)
    if code != 0:
        raise SystemExit(f"git status failed: {out}")
    return [line for line in out.splitlines() if line.strip()]


def commit_and_push(repo: str, message: str, push: bool) -> bool:
    _run(["git", "add", "-A"], repo)
    code, out = _run([
        "git",
        "-c", f"user.name={COMMIT_AUTHOR_NAME}",
        "-c", f"user.email={COMMIT_AUTHOR_EMAIL}",
        "commit", "-m", message,
    ], repo)
    if code != 0:
        print(f"[!] commit не удался: {out}", file=sys.stderr)
        return False
    print(f"[+] коммит: {message}")
    if not push:
        print("[i] не запушено (--push не задан)")
        return True
    code, out = _run(["git", "push"], repo)
    if code != 0:
        # Read-only deploy key — известная ситуация на сервере
        # (deployment-log, 2026-07-10). Коммит остаётся локальным.
        print(f"[!] push не удался: {out}", file=sys.stderr)
        return False
    print("[+] запушено")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", required=True, help="git-клон redpen-publish")
    parser.add_argument("--doc", help="ограничиться одним docId")
    parser.add_argument("--from", dest="source",
                        help="брать готовые JSON из этого каталога, а не из БД "
                             "(база и клон могут быть не видны одному процессу)")
    parser.add_argument("--no-pages", action="store_true",
                        help="не перерисовывать HTML страниц. Нужно, пока на проде "
                             "старый код: рендер новым page_html выложил бы заодно "
                             "и незапланированные изменения вёрстки")
    parser.add_argument("--apply", action="store_true", help="писать файлы и коммитить")
    parser.add_argument("--push", action="store_true", help="плюс git push (влечёт --apply)")
    parser.add_argument("--write-only", action="store_true",
                        help="только записать файлы, не звать git (влечёт --apply). "
                             "Для запуска в контейнере API, где git может быть не "
                             "установлен, а ключ для push держать не хочется: "
                             "коммит и push делаются снаружи, в том же клоне")
    args = parser.parse_args()
    apply_changes = args.apply or args.push or args.write_only

    if not args.write_only and not os.path.isdir(os.path.join(args.repo, ".git")):
        print(f"[!] {args.repo} не git-репозиторий", file=sys.stderr)
        return 2

    # БД трогаем только когда она и есть источник: в режиме --from скрипт
    # работает на хосте, и открывать там боевую базу (а значит и её WAL-файлы,
    # от имени root) нельзя — API работает под uid 10001 и потеряет к ним доступ.
    if not args.source:
        import db
        db.init_db()

    if not apply_changes:
        if args.source:
            print(f"источник JSON: {args.source}")
        else:
            import db
            pages = [p for p in db.list_pages() if not args.doc or p[0] == args.doc]
            print(f"страниц в БД: {len(pages)}")
        print("[dry-run] ничего не записано; повторите с --apply или --push")
        return 0

    if args.source:
        changed_json = copy_remarks(args.source, args.repo, args.doc)
        # Источник уже содержит отрисованные страницы — пересобирать нечем и
        # незачем, достаточно забрать их как есть.
        changed_pages = [] if args.no_pages else copy_pages(args.source, args.repo, args.doc)
        print(f"[+] обновлено JSON: {len(changed_json)}; страниц: {len(changed_pages)}")
    else:
        changed_json = export_remarks(args.repo, args.doc)
        docs = 0 if args.no_pages else rebuild_pages(args.repo, args.doc)
        print(f"[+] обновлено JSON: {len(changed_json)}; документов пересобрано: {docs}")

    if args.write_only:
        print("[i] git не вызывался (--write-only): коммит и push — снаружи")
        return 0

    dirty = git_status(args.repo)
    if not dirty:
        print("[=] изменений нет, коммит не нужен")
        return 0
    print(f"[+] файлов к коммиту: {len(dirty)}")

    stamp = datetime.date.today().isoformat()
    message = f"chore(snapshot): аннотации из БД на {stamp} ({len(dirty)} файлов)"
    return 0 if commit_and_push(args.repo, message, args.push) else 1


if __name__ == "__main__":
    raise SystemExit(main())
