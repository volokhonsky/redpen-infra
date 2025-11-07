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

Для развертывания вам нужно убедиться, что подмодули правильно клонированы и обновлены на вашем сервере развертывания. Большинство систем CI/CD имеют опции для работы с подмодулями.
