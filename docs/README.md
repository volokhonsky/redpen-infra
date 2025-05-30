# Репозиторий инфраструктуры RedPen

Это основной репозиторий проекта RedPen, который обрабатывает PDF-учебники и отображает их с аннотациями.

## Структура репозитория

- `scripts/`: Python-скрипты для обработки PDF-файлов
  - `extract_images.py`: Извлекает изображения из PDF-файлов
  - `extract_text.py`: Извлекает текст из PDF-файлов
  - `generate_annotations.py`: Генерирует шаблоны аннотаций
  - `process_pdf.py`: Основной скрипт, который организует весь рабочий процесс
  - `publish_data.py`: Публикует данные в репозиторий контента
  - `requirements.txt`: Зависимости Python
- `templates/`: Шаблоны для статического веб-сайта
  - `css/`: Таблицы стилей для веб-сайта
  - `js/`: JavaScript-файлы для функциональности
  - `index.html`: Основной HTML-файл для статического веб-сайта
  - `favicon.svg`: Иконка для веб-сайта
- `tests/`: Автоматизированные тесты для приложения
  - `annotation_position_tests.py`: Тесты для позиционирования аннотаций при различной ширине экрана
  - `run_annotation_tests.sh`: Скрипт оболочки для запуска тестов аннотаций
  - `requirements.txt`: Зависимости для инструментов тестирования
  - `baseline_positions.json`: Базовые позиции для тестов аннотаций
  - `README.md`: Документация для тестов аннотаций
- Подмодули:
  - `redpen-content/`: Репозиторий, содержащий файлы контента (изображения, текст, аннотации)
  - `redpen-publish/`: Репозиторий для опубликованного статического веб-сайта

## Функции

- Извлечение изображений и текста из PDF-файлов
- Генерация шаблонов аннотаций
- Отображение страниц учебника с аннотациями
- Адаптивный дизайн для просмотра как на настольных компьютерах, так и на мобильных устройствах
- Диагностические инструменты для устранения неполадок с позиционированием элементов

## Настройка

1. Клонировать этот репозиторий с подмодулями:
   ```bash
   git clone --recurse-submodules git@github.com:volokhonsky/redpen-infra.git
   cd redpen-infra
   ```

2. Установить зависимости Python:
   ```bash
   pip install -r scripts/requirements.txt
   ```

## Использование

### Обработка PDF

```bash
python scripts/process_pdf.py path/to/textbook.pdf
```

### Просмотр веб-сайта

Откройте `templates/index.html` в веб-браузере для просмотра обработанного контента или используйте полностью функциональный веб-сайт в директории `redpen-publish`, открыв `redpen-publish/index.html`.

### Запуск тестов аннотаций

Для проверки позиционирования кружков аннотаций при различной ширине экрана:

```bash
./tests/run_annotation_tests.sh
```

Это запустит тесты позиционирования аннотаций, которые измеряют позиции кружков при ширине рабочего стола (1280px), мобильной ширине (800px) и во время изменения размера окна. Подробнее см. в `tests/README.md`.

Для обновления базовых позиций для тестов:

```bash
./tests/run_annotation_tests.sh --update-baseline
```

## Рабочий процесс

1. PDF-файлы обрабатываются с использованием скриптов в этом репозитории
2. Сгенерированный контент хранится в репозитории контента RedPen
3. Статический веб-сайт в репозитории публикации RedPen отображает контент

## Структура проекта

Проект RedPen разделен на три репозитория:

1. **redpen-infra** (этот репозиторий): Основной репозиторий, содержащий инфраструктурный код и скрипты
2. **redpen-content**: Репозиторий, содержащий файлы контента (изображения, текст, аннотации)
3. **redpen-publish**: Репозиторий для опубликованного статического веб-сайта

Эти репозитории связаны с использованием подмодулей Git, где этот репозиторий является основным, а остальные - подмодулями.