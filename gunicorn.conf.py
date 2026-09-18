bind = "0.0.0.0:7191"
worker_class = "gthread"
workers = 1
threads = 8
timeout = 300
graceful_timeout = 30
keepalive = 5
preload_app = False
accesslog = None
errorlog = "-"
capture_output = True
worker_tmp_dir = "/tmp"
control_socket_disable = True


def on_starting(server):
    """Refuse unsafe configuration before Gunicorn creates its listener."""
    from ipaper.runtime.preflight import preflight_environment

    preflight_environment()


def post_worker_init(worker):
    """Render Gunicorn's own log lines in UTC+8 after handlers exist."""
    from ipaper.logging_setup import install_app_logging

    install_app_logging()
