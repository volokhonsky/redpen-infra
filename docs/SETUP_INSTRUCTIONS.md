# Инструкции по настройке проекта RedPen

ВНИМАНИЕ: Подмодули больше не используются. Проект состоит из трех независимых репозиториев, которые располагаются рядом друг с другом в одной папке рабочей станции/сервера. Никаких git submodule больше не требуется.

## Структура репозитория

Проект RedPen разделен на три независимых репозитория:

1. **redpen-infra**: Основной репозиторий, содержащий инфраструктурный код и скрипты
2. **redpen-content**: Репозиторий, содержащий файлы контента (изображения, текст, аннотации)
3. **redpen-publish**: Репозиторий для опубликованного статического веб-сайта

## Шаги настройки

### 1. Создание удаленных репозиториев

Сначала создайте три репозитория на GitHub или в предпочитаемом вами сервисе хостинга Git:

- `redpen-infra`
- `redpen-content`
- `redpen-publish`

### 2. Инициализация локальных репозиториев

#### Основной репозиторий (redpen-infra)

```bash
# Клонировать основной репозиторий
git clone git@github.com:volokhonsky/redpen-infra.git
cd redpen-infra

# Добавить и зафиксировать начальные файлы
git add .
git commit -m "Начальный коммит для redpen-infra"
git push origin main
```

#### Репозиторий контента (redpen-content)

```bash
# Инициализировать репозиторий контента
cd redpen-content
git init
git add .
git commit -m "Начальный коммит для redpen-content"

# Добавить удаленный репозиторий и отправить
git remote add origin git@github.com:volokhonsky/redpen-content.git
git push -u origin main
```

#### Репозиторий публикации (redpen-publish)

```bash
# Инициализировать репозиторий публикации
cd redpen-publish
git init
git add .
git commit -m "Начальный коммит для redpen-publish"

# Добавить удаленный репозиторий и отправить
git remote add origin git@github.com:volokhonsky/redpen-publish.git
git push -u origin main
```

### 3. Размещение репозиториев рядом

Теперь репозитории независимы. Рекомендуемая структура каталога на диске:

```text
workdir/
  redpen-infra/
  redpen-content/
  redpen-publish/
```

Клонирование и обновление:

```bash
# В одной папке (workdir) выполните:

git clone git@github.com:volokhonsky/redpen-infra.git
git clone git@github.com:volokhonsky/redpen-content.git
git clone git@github.com:volokhonsky/redpen-publish.git

# Обновление
(cd redpen-infra && git pull --rebase)
(cd redpen-content && git pull --rebase)
(cd redpen-publish && git pull --rebase)
```

Связь между репозиториями осуществляется на уровне путей: скрипты из redpen-infra читают/пишут в подкаталоги `../redpen-content` и `../redpen-publish` (или в одноимённые каталоги внутри `redpen-infra`, если вы храните всё в одном дереве). Git-подмодули не используются.

## Установка зависимостей Python

Для работы с PDF-файлами и обработки данных проект использует несколько Python-пакетов. Чтобы установить все необходимые зависимости:

```bash
# Перейдите в директорию scripts
cd scripts

# Установите зависимости из файла requirements.txt
pip install -r requirements.txt
```

Основные зависимости включают:
- PyPDF2 - для извлечения текста из PDF
- pdf2image - для конвертации PDF в изображения
- Pillow - для работы с изображениями

### Установка Poppler

Библиотека pdf2image требует установки Poppler - набора утилит для работы с PDF-файлами. Без Poppler вы можете получить ошибку `PDFInfoNotInstalledError: Unable to get page count. Is poppler installed and in PATH?`.

#### macOS

Установка через Homebrew:

```bash
brew install poppler
```

#### Linux (Ubuntu/Debian)

```bash
sudo apt-get update
sudo apt-get install poppler-utils
```

#### Windows

1. Скачайте бинарные файлы Poppler для Windows с [сайта](https://github.com/oschwartz10612/poppler-windows/releases/)
2. Распакуйте архив в удобное место (например, `C:\Program Files\poppler`)
3. Добавьте путь к бинарным файлам в переменную среды PATH:
   - Откройте "Система" -> "Дополнительные параметры системы" -> "Переменные среды"
   - Выберите переменную "Path" и нажмите "Изменить"
   - Добавьте путь к папке bin (например, `C:\Program Files\poppler\bin`)
   - Нажмите "ОК" для сохранения изменений

После установки Poppler убедитесь, что команда `pdfinfo -v` работает в терминале.

## Развертывание

Репозитории независимы. Размещайте `redpen-infra`, `redpen-content` и `redpen-publish` рядом на сервере и настраивайте ваши процессы так, чтобы сборка из `redpen-infra` читала/писала в соседние каталоги `../redpen-content` и `../redpen-publish`. Git submodule не требуется.

## Как убрать 404-ссылки на подмодули на GitHub

Если раньше в этом репозитории были подмодули, GitHub может показывать «подпапки» `redpen-content` и `redpen-publish` как ссылки на субмодули. После перевода на независимые репозитории эти ссылки должны исчезнуть, но только после того, как из истории состояния (индекса) будут убраны gitlinks. Сделайте в локальном клоне:

```bash
# 1) Убедитесь, что у вас нет незакоммиченных изменений
git status

# 2) Удалите gitlinks субмодулей из индекса (сами папки не трогаем)
git rm --cached redpen-content || true
git rm --cached redpen-publish || true

# 3) Удалите файл .gitmodules, если он есть, и проиндексируйте удаление
[ -f .gitmodules ] && git rm --cached .gitmodules || true

# 4) Добавьте .gitignore, чтобы эти каталоги не оказались снова закоммичены как gitlinks
echo -e "/redpen-content/\n/redpen-publish/" >> .gitignore
git add .gitignore

# 5) Зафиксируйте и отправьте изменения
git commit -m "chore: de-submodule redpen-content and redpen-publish; ignore nested repos"
git push
```

После пуша GitHub перестанет показывать кликабельные ссылки на подмодули (и, соответственно, 404 по ним). Это нормально, если каталоги остаются вложенными — Git позволяет хранить репозиторий внутри другого как обычную папку. Важно лишь, что главный репозиторий больше не отслеживает эти каталоги как подмодули или вложенные git-репозитории.


## Быстрый способ убрать 404-ссылки (скриптом)

Вместо ручных команд можно запустить готовый скрипт, который аккуратно удалит gitlinks из индекса и добавит нужные правила в .gitignore, не трогая содержимое каталогов и их собственные репозитории:

```bash
# Из корня репозитория
bash scripts/de_submodule_cleanup.sh
# Если в родительском репозитории есть незакоммиченные изменения и вы уверены, добавьте флаг:
# bash scripts/de_submodule_cleanup.sh --force

# После успешного коммита — просто пушим:
git push
```

После пуша GitHub перестанет показывать кликабельные ссылки на подмодули.
