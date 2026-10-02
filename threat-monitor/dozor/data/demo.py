"""ДЕМОНСТРАЦИОННЫЕ ДАННЫЕ.

Все материалы вымышлены: каналы, аккаунты, люди, учреждения и события не
существуют. Ссылки ведут на зарезервированный домен ``demo.invalid``.
В интерфейсе каждая такая карточка помечена плашкой «ДЕМО».

Поле ``expected`` используется только в автотестах классификатора и не
показывается как «истина».
"""

from __future__ import annotations

DEMO_NOTICE = (
    "Демонстрационные данные: материалы, каналы, люди и события вымышлены и служат "
    "для показа возможностей интерфейса и проверки классификатора."
)


def _u(platform: str, path: str) -> str:
    return f"https://demo.invalid/{platform}/{path}"


DEMO_MATERIALS: list[dict] = [
    {
        "key": "d01", "platform": "Telegram", "source_name": "Публичный чат «Город N — обсуждение» (демо)",
        "source_kind": "comment", "url": _u("telegram", "gorod_n_chat/1101"), "published_at": "2026-09-30T19:42:00+05:00",
        "context": "Обсуждение: «Что думаете о новых правилах в школах?»",
        "text": "Завтра в 8:30 приду к школе №0 на ул. Вымышленной с ружьём. Уже купил патроны, план готов. Пусть все узнают.",
        "expected": {"category": "direct_threat", "priority": "very_critical"},
    },
    {
        "key": "d02", "platform": "Telegram", "source_name": "Канал «Новости Города N 24» (демо)",
        "source_kind": "post", "url": _u("telegram", "gorod_n_news24/5512"), "published_at": "2026-09-30T20:05:00+05:00",
        "text": "Завтра в 8:30 приду к школе №0 на ул. Вымышленной с ружьём. Уже купил патроны, план готов. Пусть все узнают.",
        "forwarded_from": "d01",
        "expected": {"category": "direct_threat", "priority": "very_critical"},
    },
    {
        "key": "d03", "platform": "Telegram", "source_name": "Канал «Вестник региона» (демо)",
        "source_kind": "post", "url": _u("telegram", "vestnik_regiona/882"), "published_at": "2026-10-01T09:15:00+05:00",
        "text": "МВД сообщает: задержан подросток, который в соцсетях угрожал напасть на школу. Возбуждено уголовное дело. "
        "Ссылка на исходное сообщение: https://demo.invalid/telegram/gorod_n_chat/1101",
        "links_to": ["d01"],
        "expected": {"category": "journalism"},
    },
    {
        "key": "d04", "platform": "Instagram", "source_name": "@demo_jamiyat (демо)",
        "source_kind": "post", "url": _u("instagram", "p/DEMO004"), "published_at": "2026-10-01T11:00:00+05:00",
        "text": "Zo'ravonlikni qoralaymiz! Terrorizmning dini yo'q. Qurbonlar oilalariga hamdardlik bildiramiz.",
        "expected": {"category": "condemnation"},
    },
    {
        "key": "d05", "platform": "Telegram", "source_name": "Канал «Ҳақиқат йўли» (демо)",
        "source_kind": "post", "url": _u("telegram", "haqiqat_yoli_demo/77"), "published_at": "2026-09-27T21:10:00+05:00",
        "text": "Бу хоинларни ўлдириш керак! Жума куни ҳаммамиз йиғиламиз.",
        "expected": {"category": "call_to_violence", "priority": "critical"},
    },
    {
        "key": "d06", "platform": "Telegram", "source_name": "Канал «Путь братьев» (демо)",
        "source_kind": "post", "url": _u("telegram", "put_bratiev_demo/301"), "published_at": "2026-09-25T14:30:00+05:00",
        "text": "Братья, хватит сидеть. Хиджра — обязанность. Кто готов присоединиться к муджахидам — пишите в бот @demo_hijra_bot, дорогу оплатим.",
        "expected": {"category": "recruitment", "priority": "critical"},
    },
    {
        "key": "d07", "platform": "VK", "source_name": "Сообщество «Подслушано Город N» (демо)",
        "source_kind": "comment", "url": _u("vk", "wall-000_123?reply=456"), "published_at": "2026-09-29T16:20:00+05:00",
        "context": "Пост: «Блогер Иван Тестов раскритиковал местную администрацию»",
        "text": "Иван Тестов, ты конченый. Все пишем ему в личку, слейте адрес, пусть знает.",
        "expected": {"category": "harassment", "priority": "high"},
    },
    {
        "key": "d08", "platform": "Odnoklassniki", "source_name": "Группа «Наш район» (демо)",
        "source_kind": "post", "url": _u("ok", "group/0000/topic/888"), "published_at": "2026-09-28T10:00:00+05:00",
        "text": "Пора выгнать всех приезжих из нашего района, это не люди, а тараканы. Кто с нами — в субботу на площади.",
        "expected": {"category": "incitement", "priority": "critical"},
    },
    {
        "key": "d09", "platform": "Threads", "source_name": "@demo_movie_fan (демо)",
        "source_kind": "post", "url": _u("threads", "post/DEMO009"), "published_at": "2026-09-26T18:00:00+05:00",
        "text": "\"I will blow up the bridge,\" the villain says in the new trailer. Can't wait for the movie!",
        "expected": {"category": "quotation"},
    },
    {
        "key": "d10", "platform": "Facebook", "source_name": "Страница «Гражданская инициатива» (демо)",
        "source_kind": "post", "url": _u("facebook", "page/DEMO010"), "published_at": "2026-09-24T12:00:00+05:00",
        "text": "Требуем отставки хокима! Коррупция душит город. Выходим на мирный митинг в воскресенье, без насилия.",
        "expected": {"category": "protected_speech"},
    },
    {
        "key": "d11", "platform": "Telegram", "source_name": "Канал «Нур» (демо)",
        "source_kind": "post", "url": _u("telegram", "nur_demo/12"), "published_at": "2026-09-20T06:00:00+05:00",
        "text": "رمضان كريم، تقبل الله منا ومنكم الصيام والقيام",
        "expected": {"category": "religious_expression"},
    },
    {
        "key": "d12", "platform": "Discord", "source_name": "Сервер «Demo Gamers» / #general (демо)",
        "source_kind": "message", "url": _u("discord", "channels/000/111/222"), "published_at": "2026-09-30T22:10:00+05:00",
        "text": "I'm going to shoot everyone at the LAN party on Friday. Already have the rifle.",
        "expected": {"category": "direct_threat", "priority": "very_critical"},
    },
    {
        "key": "d13", "platform": "Twitch", "source_name": "Чат канала demo_streamer (демо)",
        "source_kind": "chat_message", "url": _u("twitch", "demo_streamer/chat"), "published_at": "2026-09-30T23:01:00+05:00",
        "text": "lol we're gonna kill them in the next round, gg ez",
        "expected": {"category": "fiction"},
    },
    {
        "key": "d14", "platform": "TikTok", "source_name": "@demo_student_kz (демо)",
        "source_kind": "video_description", "url": _u("tiktok", "video/DEMO014"), "published_at": "2026-09-29T08:00:00+05:00",
        "text": "Ертең емтиханды жарып жіберемін 😂",
        "expected": {"category": "idiom"},
    },
    {
        "key": "d15", "platform": "Telegram", "source_name": "Канал «Работа сегодня» (демо)",
        "source_kind": "post", "url": _u("telegram", "rabota_segodnya_demo/45"), "published_at": "2026-09-23T13:00:00+05:00",
        "text": "Нужны ребята 16+, лёгкий заработок: поджечь машину или релейный шкаф, оплата сразу в крипте. Пиши в лс.",
        "expected": {"category": "dangerous_involvement", "priority": "critical"},
    },
    {
        "key": "d16", "platform": "VK", "source_name": "Сообщество «Кабарлар» (демо)",
        "source_kind": "post", "url": _u("vk", "wall-000_777"), "published_at": "2026-09-22T09:00:00+06:00",
        "text": "Бишкекте террордук чабуул даярдаган деп шектелген адам кармалды, деп билдирди УКМК.",
        "expected": {"category": "journalism"},
    },
    {
        "key": "d17", "platform": "Telegram", "source_name": "Публичный чат «Маҳалла» (демо)",
        "source_kind": "comment", "url": _u("telegram", "mahalla_demo/909"), "published_at": "2026-09-30T17:30:00+05:00",
        "text": "Ман туро мекушам, Алӣ! Фардо назди хонаат меоям.",
        "expected": {"category": "direct_threat", "priority": "critical"},
    },
    {
        "key": "d18", "platform": "Instagram", "source_name": "@demo_news_fa / комментарии (демо)",
        "source_kind": "comment", "url": _u("instagram", "p/DEMO018/c/1"), "published_at": "2026-09-21T15:00:00+04:30",
        "text": "مرگ بر همه‌شان، باید همه را کشت",
        "expected": {"category": "call_to_violence", "priority": "high"},
    },
    {
        "key": "d19", "platform": "Facebook", "source_name": "Seite «Demo Nachrichten» (демо)",
        "source_kind": "post", "url": _u("facebook", "page/DEMO019"), "published_at": "2026-09-19T10:00:00+02:00",
        "text": "Die Polizei hat einen Mann festgenommen, der im Internet mit einem Anschlag auf eine Synagoge gedroht hatte.",
        "expected": {"category": "journalism"},
    },
    {
        "key": "d20", "platform": "YouTube", "source_name": "Комментарии к видео «Demo Info» (демо)",
        "source_kind": "comment", "url": _u("youtube", "watch?v=DEMO020&lc=1"), "published_at": "2026-09-19T21:00:00+02:00",
        "text": "Je condamne fermement cet attentat. La violence n'est jamais une solution.",
        "expected": {"category": "condemnation"},
    },
    {
        "key": "d21", "platform": "X", "source_name": "@demo_estudiante (демо)",
        "source_kind": "post", "url": _u("x", "status/DEMO021"), "published_at": "2026-09-18T20:00:00-05:00",
        "text": "Me voy a matar estudiando para el examen jaja",
        "expected": {"category": "idiom"},
    },
    {
        "key": "d22", "platform": "Telegram", "source_name": "Публичная группа «Demo 校园» (демо)",
        "source_kind": "comment", "url": _u("telegram", "demo_campus_cn/55"), "published_at": "2026-09-30T12:00:00+08:00",
        "text": "我要杀了他，明天在学校门口等他。",
        "expected": {"category": "direct_threat", "priority": "critical"},
    },
    {
        "key": "d23", "platform": "X", "source_name": "@demo_tokyo_anon (демо)",
        "source_kind": "post", "url": _u("x", "status/DEMO023"), "published_at": "2026-09-30T23:30:00+09:00",
        "text": "明日、駅で全員殺す。もう準備はできている。",
        "expected": {"category": "direct_threat", "priority": "very_critical"},
    },
    {
        "key": "d24", "platform": "YouTube", "source_name": "Канал «Demo 뉴스» (демо)",
        "source_kind": "video_description", "url": _u("youtube", "watch?v=DEMO024"), "published_at": "2026-09-20T07:00:00+09:00",
        "text": "경찰은 온라인에서 테러를 예고한 남성을 체포했다고 밝혔다.",
        "expected": {"category": "journalism"},
    },
    {
        "key": "d25", "platform": "Facebook", "source_name": "Pagina «Demo Cronaca» (демо)",
        "source_kind": "post", "url": _u("facebook", "page/DEMO025"), "published_at": "2026-09-21T08:00:00+02:00",
        "text": "Il giornale riporta la frase \"vi ammazzo tutti\" scritta dall'aggressore prima dell'arresto.",
        "expected": {"category": "journalism"},
    },
    {
        "key": "d26", "platform": "Telegram", "source_name": "Публичный чат «Talabalar» (демо)",
        "source_kind": "comment", "url": _u("telegram", "talabalar_demo/314"), "published_at": "2026-09-28T19:00:00+05:00",
        "text": "Hammamiz unga yozamiz, manzilini toping! U o'zini o'ldirsin. @demo_target_user",
        "expected": {"category": "harassment", "priority": "high"},
    },
    {
        "key": "d27", "platform": "TikTok", "source_name": "@demo_ashgabat_life (демо)",
        "source_kind": "video_description", "url": _u("tiktok", "video/DEMO027"), "published_at": "2026-09-18T09:00:00+05:00",
        "text": "Oraza baýramyňyz gutly bolsun! Hemmäňize parahatçylyk arzuw edýärin.",
        "expected": {"category": "religious_expression"},
    },
    {
        "key": "d28", "platform": "VK", "source_name": "Сообщество «Спорт Города N» (демо)",
        "source_kind": "comment", "url": _u("vk", "wall-000_999?reply=1"), "published_at": "2026-09-27T22:00:00+05:00",
        "text": "ну всё, ему конец",
        "expected": {"category": "ambiguous", "priority": "insufficient_data"},
    },
    {
        "key": "d29", "platform": "Telegram", "source_name": "Канал «Demo Brothers EN» (демо)",
        "source_kind": "post", "url": _u("telegram", "demo_brothers_en/19"), "published_at": "2026-09-26T16:00:00+05:00",
        "text": "Brothers, join the ranks of the mujahideen. Hijrah is obligatory. DM for travel details.",
        "expected": {"category": "recruitment", "priority": "critical"},
    },
    {
        "key": "d30", "platform": "Odnoklassniki", "source_name": "Группа «Жители N» (демо)",
        "source_kind": "post", "url": _u("ok", "group/1111/topic/42"), "published_at": "2026-09-29T11:00:00+05:00",
        "text": "Пора выгнать всех приезжих из нашего района, они не люди, а тараканы. Все, кто с нами, — в субботу на площадь!",
        "similar_to": "d08",
        "expected": {"category": "incitement", "priority": "critical"},
    },
    {
        "key": "d31", "platform": "VK", "source_name": "Сообщество «Город N без цензуры» (демо)",
        "source_kind": "post", "url": _u("vk", "wall-000_555"), "published_at": "2026-09-28T12:30:00+05:00",
        "text": "Пора выгнать всех приезжих из нашего района, это не люди, а тараканы. Кто с нами — в субботу на площади.",
        "reposted_from": "d08",
        "expected": {"category": "incitement", "priority": "critical"},
    },
    {
        "key": "d32", "platform": "Telegram", "source_name": "Канал «Голос пути» (демо)",
        "source_kind": "post", "url": _u("telegram", "golos_puti_demo/88"), "published_at": "2026-09-25T18:00:00+05:00",
        "text": "Братья, хватит сидеть. Хиджра — обязанность. Кто готов присоединиться к муджахидам — пишите в бот @demo_hijra_bot, дорогу оплатим.",
        "forwarded_from": "d06",
        "expected": {"category": "recruitment", "priority": "critical"},
    },
    {
        "key": "d33", "platform": "Telegram", "source_name": "Публичный чат «Город N — обсуждение» (демо)",
        "source_kind": "comment", "url": _u("telegram", "gorod_n_chat/1102"), "published_at": "2026-09-30T19:50:00+05:00",
        "text": "y6ью тебя, если ещё раз появишься тут",
        "expected": {"category": "direct_threat"},
    },
    {
        "key": "d34", "platform": "YouTube", "source_name": "Комментарии к видео «Demo Talk» (демо)",
        "source_kind": "comment", "url": _u("youtube", "watch?v=DEMO034&lc=2"), "published_at": "2026-09-24T19:00:00+05:00",
        "text": "Я никогда не стану убивать людей, насилие — это не выход.",
        "expected": {"category": "no_threat"},
    },
]


def expected_for(key: str) -> dict:
    for m in DEMO_MATERIALS:
        if m["key"] == key:
            return m.get("expected", {})
    return {}
