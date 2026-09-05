"""Read-only, task-independent run observability."""

__all__ = ["RunMonitor"]


def __getattr__(name):
    # Keep `python -m ...run.server` from importing its entry point twice.
    if name == "RunMonitor":
        from .server import RunMonitor
        return RunMonitor
    raise AttributeError(name)
