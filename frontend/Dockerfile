FROM nginx:alpine

# Frontend serves static files from a shared Docker volume (content_data)
# No files are copied into the image; no runtime mutation is performed here.
# content-sync populates /usr/share/nginx/html via the shared volume.
