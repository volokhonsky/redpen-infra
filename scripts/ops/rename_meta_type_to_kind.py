#!/usr/bin/env python3
"""
Разовая правка черновиков: `type: main|comment` -> `kind: major|minor`.

Написан под переименование сущности «аннотация» -> «замечание» (2026-08-29).
После прогона по обоим контентным репозиториям не нужен; оставлен в
``scripts/ops/`` как запись о том, что именно было сделано с 1564 строками.

Почему не sed: ключ `type:` меняется **только внутри блока ``~~~meta``**. Тело
замечания вполне может начинаться со строки вида ``type: ...`` — в цитате из
учебника или в примере кода, — и построчная замена испортила бы текст, чего
никто не заметил бы до следующей публикации.

    python3 scripts/ops/rename_meta_type_to_kind.py <dir> [--apply]

По умолчанию — отчёт без записи (как у scripts/api/backfill_tags.py).
"""

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import remark_kinds  # noqa: E402

LEGACY_KINDS = remark_kinds.LEGACY_KINDS

#: Разделитель мета-блока: тот же набор, что понимает remark_converter
#: (включая хвостовые пробелы, встречающиеся в черновиках).
_FENCE = re.compile(r"^[ \t]*(?:~~~meta|~~~|---)[ \t]*$")
_TYPE_LINE = re.compile(r"^([ \t]*)type:[ \t]*(\S+)[ \t]*$")


def convert(text):
    """(новый текст, число заменённых строк, список неожиданных значений)."""
    lines = text.split("\n")
    out = []
    in_meta = False
    changed = 0
    unexpected = []
    for line in lines:
        if _FENCE.match(line):
            in_meta = not in_meta
            out.append(line)
            continue
        match = _TYPE_LINE.match(line) if in_meta else None
        if match:
            indent, value = match.group(1), match.group(2)
            if value in LEGACY_KINDS:
                out.append("%skind: %s" % (indent, LEGACY_KINDS[value]))
                changed += 1
                continue
            # `general` и всё прочее оставляем как есть: за упразднённый тип
            # отвечает docs/general-migration-map.json, а не этот скрипт.
            unexpected.append(value)
        out.append(line)
    return "\n".join(out), changed, unexpected


def run(root, apply_changes):
    files = 0
    lines = 0
    unexpected = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in sorted(filenames):
            if not name.endswith(".md"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, encoding="utf-8") as handle:
                original = handle.read()
            converted, changed, odd = convert(original)
            for value in odd:
                unexpected.setdefault(value, []).append(path)
            if not changed:
                continue
            files += 1
            lines += changed
            if apply_changes:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(converted)

    print("%s: файлов %d, строк %d" % (
        "изменено" if apply_changes else "будет изменено", files, lines))
    for value, paths in sorted(unexpected.items()):
        print("  оставлено как есть: type: %s (%d шт., напр. %s)"
              % (value, len(paths), paths[0]))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory")
    parser.add_argument("--apply", action="store_true",
                        help="записать изменения (без флага — только отчёт)")
    args = parser.parse_args(argv)
    if not os.path.isdir(args.directory):
        print("нет такого каталога: %s" % args.directory, file=sys.stderr)
        return 2
    return run(args.directory, args.apply)


if __name__ == "__main__":
    raise SystemExit(main())
