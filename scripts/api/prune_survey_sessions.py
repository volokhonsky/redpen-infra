"""
CLI: убрать заходы опроса, не оставившие ни одного ответа.

Использование:
    python prune_survey_sessions.py [--days 30] [--apply]

Зачем. `POST /api/survey/session` — единственный маршрут в системе, где строки
в канонической базе создаёт посторонний человек: назвался псевдонимом и получил
токен захода. Ни срока жизни, ни уборки у этих строк не было, поэтому
`survey_respondents` и `survey_sessions` росли ровно столько, сколько
кто-нибудь стучится в опрос — а стучаться может кто угодно.

Что убирается: заходы старше указанного срока, у которых нет ни одного ответа,
и псевдонимы, у которых после этого не осталось ни заходов, ни ответов.

Что НЕ убирается никогда: псевдонимы и заходы, оставившие хоть один ответ. Это
данные опроса; их удаление — отдельное осознанное действие админа в кабинете
(`DELETE /api/survey/sessions/{id}` и `.../respondents/{id}`).

По умолчанию только считает; запись — по `--apply`. Тот же порядок, что у
scripts/api/backfill_tags.py.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db  # noqa: E402

DEFAULT_DAYS = 30


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"возраст захода в сутках (по умолчанию {DEFAULT_DAYS})")
    parser.add_argument("--apply", action="store_true",
                        help="действительно удалить; без флага только отчёт")
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days должен быть не меньше 1")

    db.init_db()
    result = db.prune_empty_survey_sessions(args.days, apply=args.apply)
    verb = "удалено" if args.apply else "нашлось (записи не было)"
    print(f"prune-survey-sessions: {verb} — "
          f"заходов без ответов: {result['sessions']}, "
          f"псевдонимов без следа: {result['respondents']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
