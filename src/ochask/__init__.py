from .models import MainImage, ReportAttachment, ReportLanding


def __getattr__(name: str):
    if name in {"get_landing_page", "parse_landing_page"}:
        from importlib import import_module

        return getattr(import_module(".scraper", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "MainImage",
    "ReportAttachment",
    "ReportLanding",
    "get_landing_page",
    "parse_landing_page",
]
