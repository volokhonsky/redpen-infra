import os
import re
import glob
import argparse
from pathlib import Path


def get_files_to_rename(folder, pattern='page_[0-9][0-9][0-9].*'):
    """Получает список всех файлов по маске в указанной папке"""
    return glob.glob(os.path.join(folder, pattern))


def check_conflicts(files):
    """Проверяет, не приведет ли переименование к конфликтам или потере файлов"""
    planned_names = set()
    for file in files:
        # Разбираем путь к файлу
        path = Path(file)
        match = re.match(r'page_(\d{3})(.*)', path.name)
        if match:
            current_num = int(match.group(1))
            extension = match.group(2)  # включает точку и расширение
            new_num = current_num - 1
            new_name = path.parent / f'page_{new_num:03d}{extension}'

            # Проверяем, не собираемся ли мы использовать это имя дважды
            if new_name in planned_names:
                return True, f"Конфликт: несколько файлов будут переименованы в {new_name}"
            planned_names.add(new_name)

            # Проверяем, не существует ли уже файл с таким именем
            if str(new_name) not in files and os.path.exists(new_name):
                return True, f"Конфликт: файл {new_name} уже существует"

    return False, ""


def rename_files(folder):
    """Выполняет переименование файлов"""
    # Получаем список всех файлов по маске
    files = get_files_to_rename(folder)
    if not files:
        print(f"В папке {folder} не найдено файлов для переименования")
        return

    files.sort()

    # Проверяем на конфликты перед началом переименования
    has_conflicts, error_message = check_conflicts(files)

    if has_conflicts:
        print(f"ОШИБКА: {error_message}")
        print("Операция отменена для предотвращения потери данных.")
        return

    # Создаём временные имена для всех файлов
    print("Этап 1: Создание временных имен...")
    for file in files:
        path = Path(file)
        temp_name = path.parent / f"temp_{path.name}"
        os.rename(file, temp_name)
        print(f"Временное переименование: {path.name} -> {temp_name.name}")

    # Переименовываем из временных в целевые имена
    print("\nЭтап 2: Создание финальных имен...")
    for file in files:
        path = Path(file)
        temp_name = path.parent / f"temp_{path.name}"
        match = re.match(r'page_(\d{3})(.*)', path.name)
        if match:
            current_num = int(match.group(1))
            extension = match.group(2)  # включает точку и расширение
            new_num = current_num - 1
            new_name = path.parent / f'page_{new_num:03d}{extension}'
            os.rename(temp_name, new_name)
            print(f"Финальное переименование: {path.name} -> {new_name.name}")

    print("\nПереименование успешно завершено!")


def main():
    parser = argparse.ArgumentParser(description='Переименование файлов page_XXX.* с уменьшением номера на 1')
    parser.add_argument('folder', nargs='?', default='.',
                        help='Папка с файлами (по умолчанию - текущая папка)')

    args = parser.parse_args()

    # Преобразуем путь в абсолютный для более понятного вывода
    folder = os.path.abspath(args.folder)

    if not os.path.exists(folder):
        print(f"ОШИБКА: Папка {folder} не существует")
        return

    if not os.path.isdir(folder):
        print(f"ОШИБКА: {folder} не является папкой")
        return

    print(f"Работаем с папкой: {folder}")
    rename_files(folder)


if __name__ == "__main__":
    main()