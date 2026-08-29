#!/usr/bin/env python3
"""Собрать задание для агента-аннотатора по номеру параграфа.

Задание генерируется из шаблона docs/remark-agent-run-prompt.template.md:
номер, название и диапазон страниц берутся из paragraphs_list.txt, пути
подставляются контейнерные (/work). Правила живут в
docs/remark-agent-prompt.md и в задание не копируются — рукописные задания
расходятся с документом и теряют правила (см. §20, 2026-08-08).

Примеры:
    python3 scripts/make_agent_prompt.py 20
    python3 scripts/make_agent_prompt.py 32-33 -o ~/.claude-agent-docker/para32-33-prompt.txt
"""

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = REPO_ROOT / "docs" / "remark-agent-run-prompt.template.md"


def parse_paragraphs_list(path):
    """id -> (title, from, to). Название может содержать запятые, id и границы — нет."""
    entries = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            continue
        para_id, page_from, page_to = parts[0], parts[-2], parts[-1]
        if not (page_from.isdigit() and page_to.isdigit()):
            continue
        entries[para_id] = (", ".join(parts[1:-2]), int(page_from), int(page_to))
    return entries


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paragraph", help="номер параграфа как в paragraphs_list.txt (20, 32-33)")
    ap.add_argument("--doc-id", default="medinsky11klass")
    ap.add_argument("--content-root", default=None,
                    help="каталог redpen-content (по умолчанию <repo>/redpen-content)")
    ap.add_argument("-o", "--output", help="куда записать задание (по умолчанию — stdout)")
    ap.add_argument("--skip-image-check", action="store_true",
                    help="не проверять наличие картинок с сеткой")
    args = ap.parse_args()

    content_root = Path(args.content_root) if args.content_root else REPO_ROOT / "redpen-content"
    doc_dir = content_root / args.doc_id
    list_path = doc_dir / "paragraphs_list.txt"
    if not list_path.is_file():
        sys.exit(f"нет {list_path}")

    entries = parse_paragraphs_list(list_path)
    if args.paragraph not in entries:
        known = ", ".join(k for k in entries if re.fullmatch(r"[\d-]+", k))
        sys.exit(f"параграф {args.paragraph!r} не найден в {list_path}\nизвестные: {known}")
    title, page_from, page_to = entries[args.paragraph]

    if not args.skip_image_check:
        grid_dir = doc_dir / "images_with_grid"
        missing = [n for n in range(page_from, page_to + 1)
                   if not (grid_dir / f"page_{n:03d}.png").is_file()]
        if missing:
            rng = f"{missing[0]} {missing[-1]}"
            sys.exit(
                f"нет картинок с сеткой для страниц: {', '.join(str(n) for n in missing)}\n"
                f"сгенерировать: python3 scripts/make_grid_images.py "
                f"redpen-content/{args.doc_id} {rng}"
            )

    # В именах служебных файлов дефис из "32-33" не мешает, но пробелов быть не должно.
    para_slug = args.paragraph.replace(" ", "")
    prompt = TEMPLATE.read_text(encoding="utf-8")
    for key, value in {
        "DOC_ID": args.doc_id,
        "PARA": args.paragraph,
        "PARA_SLUG": para_slug,
        "TITLE": title,
        "FROM": f"{page_from:03d}",
        "TO": f"{page_to:03d}",
        "NPAGES": str(page_to - page_from + 1),
    }.items():
        prompt = prompt.replace("{{" + key + "}}", value)

    leftover = re.findall(r"\{\{(\w+)\}\}", prompt)
    if leftover:
        sys.exit(f"в шаблоне остались незаполненные плейсхолдеры: {', '.join(sorted(set(leftover)))}")

    if args.output:
        out = Path(args.output).expanduser()
        out.write_text(prompt, encoding="utf-8")
        print(f"[+] задание для §{args.paragraph} «{title}» "
              f"(стр. {page_from:03d}—{page_to:03d}) → {out}")
    else:
        sys.stdout.write(prompt)


if __name__ == "__main__":
    main()
