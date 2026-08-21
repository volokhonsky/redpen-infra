#!/usr/bin/env python3
"""
Браузерная приёмка офлайн-архива (playwright, не собирается pytest'ом — как и
annotation_position_tests.py / editor_mode_tests.py).

Проверяет распакованный архив по адресам file://, то есть ровно тот сценарий,
который обещан читателю: скачал, распаковал, открыл двойным щелчком, интернета
нет. Главные утверждения — страница действительно отрисовалась (данные пришли
через js/redpen-offline.js, а не fetch'ем) и за пределы file:// не ушло ни
одного запроса.

    python3 scripts/make_offline_bundle.py --doc medinsky11klass \\
        --site-dir redpen-publish --out tmp/bundle.zip
    unzip -q tmp/bundle.zip -d tmp/unzipped
    .venv/bin/python tests/offline_bundle_browser_test.py \\
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

        print("2. Страница разбора (?p=%s)" % label)
        page.goto(base + "/%s/index.html?p=%s" % (doc, label), wait_until="networkidle")
        page.wait_for_timeout(2500)
        check(page.evaluate("() => !!window.REDPEN_OFFLINE_ACTIVE"), "шим офлайн-данных активен")
        img = page.eval_on_selector("#page-image", "e => ({src: e.getAttribute('src'), w: e.naturalWidth})")
        check(img["w"] > 0, "картинка страницы загрузилась", img)
        circles = page.eval_on_selector_all("[class*=circle]", "els => els.length")
        check(circles > 0, "маркеры расставлены (значит text/*.json прочитан)", circles)
        глобальный = page.eval_on_selector("#global-comment", "e => e.textContent.trim()")
        check(len(глобальный) > 40 and "Загрузка" not in глобальный,
              "общий комментарий отрисован", глобальный[:60])

        print("3. Взаимодействие")
        page.query_selector("[class*=circle]").click()
        page.wait_for_timeout(900)
        popup = page.evaluate("""() => {
            const els = [...document.querySelectorAll('[class*=popup], #mobile-comment-content')];
            const v = els.filter(e => e.offsetParent !== null && e.textContent.trim());
            return v.length ? v[0].textContent.trim() : '';
        }""")
        check(len(popup) > 20, "попап аннотации открывается", popup[:60])
        if screenshot_dir:
            page.screenshot(path=os.path.join(screenshot_dir, "offline-page.png"))
        page.click("#next-page")
        page.wait_for_timeout(1500)
        check("?p=" in page.url, "листание сохраняет адресацию ?p=", page.url.split("/")[-1])

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
    parser.add_argument("--page", default="7", help="метка страницы для проверки (?p=)")
    parser.add_argument("--screenshots", help="куда положить скриншот")
    args = parser.parse_args()
    return run(args.bundle_root, args.doc, args.page, args.screenshots)


if __name__ == "__main__":
    sys.exit(main())
