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
    DemoConnector,
]


def build_connectors(settings) -> dict[str, Connector]:
    return {cls.name: cls(settings) for cls in CONNECTOR_CLASSES}
