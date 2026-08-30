"""Общий локальный HTTP-сервер для браузерных сценариев.

Эти две функции были скопированы в четырёх файлах (позиционные тесты, тесты
режима редактора и два отладочных скрипта в tests/manual/) — одинаково, вплоть
до комментариев. Здесь они в одном экземпляре.

ВНИМАНИЕ: start_http_server() делает os.chdir() в раздаваемый каталог. Это
поведение исходных копий, на него опираются вызывающие; отдельный процесс для
сервера тут не заводится, поэтому рабочий каталог меняется у всего теста.
"""

import http.server
import os
import socket
import socketserver
import threading


def find_free_port():
    """Свободный порт на localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


def start_http_server(directory, port):
    """Поднять раздачу `directory` на `port` в фоновом потоке."""
    handler = http.server.SimpleHTTPRequestHandler
    httpd = socketserver.TCPServer(("", port), handler)

    os.chdir(directory)

    server_thread = threading.Thread(target=httpd.serve_forever)
    server_thread.daemon = True
    server_thread.start()

    return httpd
