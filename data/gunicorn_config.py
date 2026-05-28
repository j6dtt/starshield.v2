from applog import emit, install_crash_handler, Heartbeat

_heartbeat = None


def on_starting(server):
    emit("STARTUP")


def when_ready(server):
    global _heartbeat
    emit("READY")
    _heartbeat = Heartbeat(interval_seconds=30)
    _heartbeat.start()


def on_exit(server):
    emit("SHUTDOWN")


def post_fork(server, worker):
    install_crash_handler()
