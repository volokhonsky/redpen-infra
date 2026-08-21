FROM nginx:alpine

# Frontend serves static files from a shared Docker volume (content_data)
# No files are copied into the image; no runtime mutation is performed here.
# content-sync populates /usr/share/nginx/html via the shared volume.
#
# Единственное, что образ добавляет от себя, — правила кэширования: у nginx по
# умолчанию нет Cache-Control, и после выкладки читатели оставались на старом
# JS (см. deployment-log, 2026-08-21).
COPY frontend/default.conf /etc/nginx/conf.d/default.conf
