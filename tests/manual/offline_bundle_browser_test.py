#!/usr/bin/env python3
"""
Браузерная приёмка офлайн-архива (playwright, pytest'ом не собирается).

Проверяет распакованный архив по адресам file://, то есть ровно тот сценарий,
который обещан читателю: скачал, распаковал, открыл двойным щелчком, интернета
нет. Главные утверждения — страница действительно отрисовалась и за пределы
file:// не ушло ни одного запроса.

Переписан 2026-08-30 под текущую конструкцию бандла. Раньше он проверял, что
активен шим `redpen-offline.js`, подменявший fetch: данные книги лежали
отдельным файлом offline-data.js, потому что под file:// fetch к соседним
файлам запрещён. Всё это было нужно старому SPA; постраничный просмотрщик не
делает ни одного запроса вовсе — замечания приезжают инлайновым блоком
redpen-page-data внутри самой страницы, — и бандл стал прямой копией статики.

    python3 scripts/make_offline_bundle.py --doc medinsky11klass \\
        --site-dir redpen-publish --out tmp/bundle.zip
    unzip -q tmp/bundle.zip -d tmp/unzipped
    .venv/bin/python tests/manual/offline_bundle_browser_test.py \\
        tmp/unzipped/redpen-medinsky11klass-offline --page 7
"""

import argparse
import os
import sys

from playwright.sync_api import sync_playwright


def run(root, doc, label, screenshot_dir=None):
    root = os.path.abspath(root)
    base = "file://" + root
    errors, requests = [], []
    failures = []

    def check(ok, what, detail=""):
        print(("  [ok] " if ok else "  [!!] ") + what + (" — " + str(detail) if detail else ""))
        if not ok:
            failures.append(what)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append("pageerror: %s" % e))
        page.on("request", lambda r: requests.append(r.url))

        print("1. Точка входа")
        page.goto(base + "/ЧИТАТЬ.html", wait_until="networkidle")
        page.wait_for_timeout(800)
        check(page.url.endswith("index.html"), "ЧИТАТЬ.html ведёт на корневой экран", page.url.split("/")[-1])
        page.click("text=Открыть разбор")
        page.wait_for_timeout(2500)
        check("/%s/" % doc in page.url, "кнопка открывает разбор", page.url.split("/")[-2:])

        print("2. Страница разбора (стр. %s)" % label)
        page.goto(base + "/%s/pages/%s/index.html" % (doc, label), wait_until="networkidle")
        page.wait_for_timeout(2000)
        img = page.eval_on_selector("#page-image", "e => ({src: e.getAttribute('src'), w: e.naturalWidth})")
        check(img["w"] > 0, "картинка страницы загрузилась", img)
        blob = page.eval_on_selector(
            "#redpen-page-data",
            "e => JSON.parse(e.textContent).filter(a => !a.draft).length")
        check(blob > 0, "опубликованные замечания приехали в самой странице", blob)
        circles = page.eval_on_selector_all(".circle", "els => els.length")
        check(circles == blob, "маркер на каждое замечание", (circles, blob))
        items = page.eval_on_selector_all(".panel-item", "els => els.length")
        check(items == blob, "панель замечаний отрисована сборкой", (items, blob))

        print("3. Взаимодействие")
        first = page.query_selector(".circle")
        if first is None:
            check(False, "нечего открывать: на странице нет маркеров — "
                         "выберите --page с опубликованными замечаниями")
            browser.close()
            return 1
        first.click()
        page.wait_for_timeout(900)
        popup = page.evaluate("""() => {
            const els = [...document.querySelectorAll('[id^=popup-], #mobile-remark-body')];
            const v = els.filter(e => e.offsetParent !== null && e.textContent.trim());
            return v.length ? v[0].textContent.trim() : '';
        }""")
        check(len(popup) > 20, "попап замечания открывается", popup[:60])
        if screenshot_dir:
            page.screenshot(path=os.path.join(screenshot_dir, "offline-page.png"))
        nav = page.query_selector(".page-nav a")
        check(nav is not None, "на странице есть навигация к соседней")
        if nav:
            nav.click()
            page.wait_for_timeout(1200)
            check("/pages/" in page.url, "листание остаётся внутри архива",
                  page.url.split("/")[-3:])

        browser.close()

    print("4. Сеть и консоль")
    external = [u for u in requests if not u.startswith("file://")]
    check(not external, "ни одного запроса за пределы file://", external[:5])
    check(not errors, "консоль без ошибок", errors[:5])

    if failures:
        print("\nПРОВАЛЕНО: " + "; ".join(failures))
        return 1
    print("\nВсё сошлось: копия читается офлайн и никуда не обращается.")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bundle_root", help="каталог распакованного архива")
    parser.add_argument("--doc", default="medinsky11klass")
    parser.add_argument("--page", default="7",
                        help="метка страницы для проверки; нужна страница с "
                             "опубликованными замечаниями")
    parser.add_argument("--screenshots", help="куда положить скриншот")
    args = parser.parse_args()
    return run(args.bundle_root, args.doc, args.page, args.screenshots)


if __name__ == "__main__":
    sys.exit(main())
