"""Реестр коннекторов."""

from __future__ import annotations

from .base import Connector
from .demo import DemoConnector
from .platforms import (
    BlueskyConnector,
    DiscordConnector,
    InstagramConnector,
    MastodonConnector,
    ThreadsConnector,
    TikTokResearchConnector,
    TwitchConnector,
    VKConnector,
    XConnector,
    YouTubeConnector,
)
from .telegram import TelegramMTProtoConnector, TelegramWebConnector
from .unavailable import (
    FacebookConnector,
    ManualImportConnector,
    MaxConnector,
    OdnoklassnikiConnector,
    SignalConnector,
    WhatsAppConnector,
)
from .web import RSSConnector, WebPageConnector

CONNECTOR_CLASSES: list[type[Connector]] = [
    TelegramWebConnector,
    TelegramMTProtoConnector,
    InstagramConnector,
    DiscordConnector,
    TwitchConnector,
    TikTokResearchConnector,
    FacebookConnector,
    ThreadsConnector,
    OdnoklassnikiConnector,
    VKConnector,
    YouTubeConnector,
    XConnector,
    BlueskyConnector,
    MastodonConnector,
    RSSConnector,
    WebPageConnector,
    SignalConnector,
    MaxConnector,
    WhatsAppConnector,
    ManualImportConnector,
]


def build_connectors(settings) -> dict[str, Connector]:
    classes = list(CONNECTOR_CLASSES)
    if getattr(settings, "demo_enabled", False):
        classes.append(DemoConnector)  # учебный режим: вымышленные материалы
    return {cls.name: cls(settings) for cls in classes}
