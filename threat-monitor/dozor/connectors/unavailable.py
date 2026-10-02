"""Платформы без разрешённого интерфейса для автоматического сбора.

Эти записи показываются в интерфейсе, чтобы ограничения охвата были
видны. Материалы с этих платформ можно добавить только через импорт
правомерно полученных материалов (с указанием основания).
"""

from __future__ import annotations

from .base import Connector, ConnectorStatus


class _Static(Connector):
    state = "unavailable"
    message = ""

    def status(self) -> ConnectorStatus:
        return ConnectorStatus(self.state, self.message)


class FacebookConnector(_Static):
    name = "facebook"
    platform = "Facebook"
    title = "Facebook — Meta Content Library"
    access = "Meta Content Library и Content Library API — только для одобренных исследователей (через ICPSR/SOMAR)"
    official = True
    state = "requires_approval"
    message = (
        "Публичный API поиска по публикациям Facebook отсутствует (CrowdTangle закрыт в 2024 г.). "
        "Работа с Meta Content Library ведётся в защищённой среде Meta; экспортированные результаты "
        "можно добавить через импорт с указанием основания."
    )
    capabilities = ["import_only"]
    limitations = ["Нет прямого автоматического сбора в этой системе"]
    docs_url = "https://transparency.meta.com/researchtools/meta-content-library"


class OdnoklassnikiConnector(_Static):
    name = "odnoklassniki"
    platform = "Одноклассники"
    title = "Одноклассники — OK API"
    access = "OK API требует регистрации приложения и выдачи прав администрацией платформы"
    state = "requires_approval"
    message = (
        "Публичного поиска по записям через OK API без согласования прав нет. После получения прав "
        "коннектор подключается по тому же шаблону, что и VK; до этого — импорт правомерно полученных материалов."
    )
    capabilities = ["import_only"]
    docs_url = "https://apiok.ru/"


class SignalConnector(_Static):
    name = "signal"
    platform = "Signal"
    title = "Signal"
    access = "Сквозное шифрование; публичных каналов и API для чтения чужого контента нет"
    message = (
        "Подключение невозможно и не предусматривается: доступ к чужой переписке исключён. "
        "Допускается только импорт материалов, правомерно предоставленных пользователем (например, жалоба с копией сообщения)."
    )
    capabilities = ["import_only"]
    docs_url = "https://signal.org/legal/"


class MaxConnector(_Static):
    name = "max"
    platform = "MAX"
    title = "MAX (мессенджер)"
    access = "Bot API работает только в чатах, куда бот добавлен администратором; публичного поиска нет"
    state = "requires_approval"
    message = (
        "Автоматический сбор возможен только ботом в каналах/чатах, где владелец добавил бота и дал согласие. "
        "Глобального поиска по публичному контенту через разрешённые интерфейсы нет."
    )
    capabilities = ["import_only"]
    docs_url = "https://dev.max.ru/"


class WhatsAppConnector(_Static):
    name = "whatsapp"
    platform = "WhatsApp"
    title = "WhatsApp (каналы и чаты)"
    access = "API для чтения чужих каналов и чатов нет; WhatsApp Business API — только собственные диалоги"
    message = "Подключение невозможно; только импорт правомерно предоставленных материалов."
    capabilities = ["import_only"]
    docs_url = "https://www.whatsapp.com/legal"


class ManualImportConnector(_Static):
    name = "manual_import"
    platform = "Импорт"
    title = "Импорт материалов, правомерно предоставленных пользователем"
    access = "JSON/CSV/текст через интерфейс или API; обязательно указывается основание получения"
    state = "connected"
    message = "Доступен аналитикам; каждое добавление фиксируется в журнале с основанием"
    capabilities = ["import"]
