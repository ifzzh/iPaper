from __future__ import annotations

import os

from werkzeug.serving import make_server

from ..logging_setup import install_app_logging
from .app import create_worker_app


def main() -> None:
    install_app_logging()
    app = create_worker_app()
    server = make_server("0.0.0.0", 7192, app, threaded=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
