from werkzeug.serving import make_server

from ..logging_setup import install_app_logging
from .app import create_worker_app


def main() -> None:
    install_app_logging()
    make_server("0.0.0.0", 7193, create_worker_app(), threaded=True).serve_forever()


if __name__ == "__main__":
    main()
