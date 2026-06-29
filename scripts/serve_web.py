#!/usr/bin/env python3
"""Servidor estático mínimo para o Flutter Web (SaaS) com fallback de SPA.

Serve os arquivos do build do Flutter Web e, para rotas que não existem como
arquivo (client-side routing do go_router), devolve /index.html. Lê a porta de
$PORT (Render injeta) e o diretório de $WEB_ROOT.
"""
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WEB_ROOT = os.environ.get("WEB_ROOT", "/web")
PORT = int(os.environ.get("PORT", "8000"))


class SPAHandler(SimpleHTTPRequestHandler):
    def send_head(self):
        path = self.translate_path(self.path)
        # Diretórios e arquivos existentes seguem o fluxo padrão (index.html /
        # main.dart.js / assets). Rotas inexistentes caem no index.html (SPA).
        if not os.path.isdir(path) and not os.path.exists(path):
            self.path = "/index.html"
        return super().send_head()

    def end_headers(self):
        # index.html não deve ser cacheado (garante pegar novos deploys).
        if self.path in ("/", "/index.html"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()


def main():
    handler = partial(SPAHandler, directory=WEB_ROOT)
    with ThreadingHTTPServer(("0.0.0.0", PORT), handler) as httpd:
        print(f"[serve_web] servindo {WEB_ROOT} em 0.0.0.0:{PORT}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
