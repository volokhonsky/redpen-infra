# Инструкции по настройке проекта RedPen

Этот документ предоставляет инструкции по настройке проекта RedPen с его структурой подмодулей Git.

## Структура репозитория

Проект RedPen разделен на три репозитория:

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

### 3. Настройка подмодулей

Из основного репозитория:

```bash
# Удалить существующие директории
rm -rf redpen-content redpen-publish

# Добавить репозитории как подмодули
git submodule add git@github.com:volokhonsky/redpen-content.git redpen-content
git submodule add git@github.com:volokhonsky/redpen-publish.git redpen-publish

# Зафиксировать изменения
git commit -am "Добавить redpen-content и redpen-publish как подмодули"
git push origin main
```

### 4. Клонирование проекта с подмодулями

Для клонирования проекта со всеми подмодулями:

```bash
git clone --recurse-submodules git@github.com:volokhonsky/redpen-infra.git
cd redpen-infra
```

Или если вы уже клонировали репозиторий без подмодулей:

```bash
git submodule init
git submodule update
```

## Работа с подмодулями

### Обновление подмодулей

Для обновления всех подмодулей до их последних коммитов:

```bash
git submodule update --remote
```

### Внесение изменений в подмодули

```bash
# Перейти в подмодуль
cd redpen-content

# Внести изменения, зафиксировать и отправить
git add .
git commit -m "Обновить контент"
git push origin main

# Вернуться в основной репозиторий и обновить ссылку на подмодуль
cd ..
git add redpen-content
git commit -m "Обновить подмодуль redpen-content"
git push origin main
```

## Развертывание

Для развертывания вам нужно убедиться, что подмодули правильно клонированы и обновлены на вашем сервере развертывания. Большинство систем CI/CD имеют опции для работы с подмодулями.