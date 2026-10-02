"""Командная строка: python -m dozor <команда>."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import secrets
import sys

from . import audit, repository as repo, security
from .config import load_settings
from .db import Database


def _password() -> str:
    return secrets.token_urlsafe(12)


def cmd_init(args, settings, db: Database) -> None:
    users = security.list_users(db)
    created = []
    if not users:
        pw = args.admin_password or _password()
        security.create_user(db, "admin", pw, "admin")
        created.append(("admin", "admin", pw))
    if args.demo:
        existing = {u["username"] for u in security.list_users(db)}
        for name, role in (("analyst", "analyst"), ("supervisor", "supervisor"), ("viewer", "viewer")):
            if name not in existing:
                pw = _password()
                security.create_user(db, name, pw, role)
                created.append((name, role, pw))
        from .connectors.demo import demo_items
        from .connectors.registry import build_connectors
        from .pipeline import Pipeline

        system = security.User(0, "system", "admin")
        summary = Pipeline(db, settings, build_connectors(settings)).ingest(demo_items(), system)
        audit.log(db, "system", "admin", "demo_load", details=summary["counts"])
        print(f"Загружены демонстрационные данные: карточек {summary['counts']['stored']} "
              f"(потенциальных угроз: {summary['counts']['threats']}, связей: {summary['counts']['edges']})")
    print(f"База данных: {settings.db_path}")
    if created:
        print("\nСозданы учётные записи (сохраните пароли — они больше не будут показаны):")
        for name, role, pw in created:
            print(f"  {name:<11} роль: {role:<10} пароль: {pw}")
    else:
        print("Учётные записи уже существуют.")


def cmd_serve(args, settings, db) -> None:
    import uvicorn

    from .api import create_app

    app = create_app(settings, db)
    uvicorn.run(app, host=args.host or settings.host, port=args.port or settings.port, log_level="info")


def cmd_create_user(args, settings, db) -> None:
    pw = args.password or getpass.getpass("Пароль (не менее 10 символов): ")
    user = security.create_user(db, args.username, pw, args.role)
    audit.log(db, "cli", "admin", "user_create", "user", str(user.id), {"username": user.username, "role": user.role})
    print(f"Создан пользователь {user.username} ({user.role})")


def cmd_purge(args, settings, db) -> None:
    result = repo.purge_expired(db)
    audit.log(db, "cli", "admin", "purge", details=result)
    print(json.dumps(result, ensure_ascii=False))


def cmd_analyze(args, settings, db) -> None:
    from .analysis.classifier import assess

    a = assess(args.text)
    print(f"Категория: {a.category_label}\nРамка: {a.framing_label}\nПриоритет: {a.priority_label}\n"
          f"Тяжесть: {a.severity_label}\nУверенность: {a.confidence} ({a.confidence_label})\n"
          f"Язык: {a.language['label']} ({a.language['confidence']})\nПроверка человеком: {'да' if a.requires_human_review else 'нет'}")
    for e in a.explanation:
        print(f"  [{e['kind']}] {e['text']}")


def cmd_expand(args, settings, db) -> None:
    from .text.expansion import expand_query

    exp = expand_query(args.query)
    for t in exp.terms:
        print(f"«{t.query_term}» → {', '.join(t.concept_labels) or t.method}")
        for lang, words in t.by_lang.items():
            print(f"   {lang}: {', '.join(words)}")
        if t.literal_variants:
            print(f"   написания: {', '.join(t.literal_variants)}")
    for w in exp.warnings:
        print("! " + w)


def cmd_verify_audit(args, settings, db) -> None:
    print(json.dumps(audit.verify(db), ensure_ascii=False))


def cmd_telegram_login(args, settings, db) -> None:
    try:
        from telethon import TelegramClient  # type: ignore
    except ImportError:
        sys.exit("Установите telethon: pip install telethon")
    api_id, api_hash = settings.env("TELEGRAM_API_ID"), settings.env("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        sys.exit("Задайте TELEGRAM_API_ID и TELEGRAM_API_HASH")
    session = settings.env("TELEGRAM_SESSION") or str(settings.db_path.parent / "telegram.session")

    async def run():
        async with TelegramClient(session, int(api_id), api_hash) as client:
            me = await client.get_me()
            print(f"Сессия авторизована: {getattr(me, 'username', None) or me.id}")

    asyncio.run(run())


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="dozor", description="Дозор — мониторинг угроз в общедоступном контенте")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="создать БД и администратора")
    p.add_argument("--demo", action="store_true", help="добавить демо-пользователей и демонстрационные данные")
    p.add_argument("--admin-password")
    p = sub.add_parser("serve", help="запустить веб-интерфейс")
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p = sub.add_parser("create-user", help="создать пользователя")
    p.add_argument("username")
    p.add_argument("role", choices=list(security.ROLES))
    p.add_argument("--password")
    sub.add_parser("purge", help="удалить данные с истёкшим сроком хранения")
    p = sub.add_parser("analyze", help="оценить текст")
    p.add_argument("text")
    p = sub.add_parser("expand", help="показать многоязычное расширение запроса")
    p.add_argument("query")
    sub.add_parser("verify-audit", help="проверить целостность журнала")
    sub.add_parser("telegram-login", help="авторизовать сессию Telegram API (MTProto)")
    args = parser.parse_args(argv)
    settings = load_settings()
    db = Database(settings.db_path)
    {
        "init": cmd_init, "serve": cmd_serve, "create-user": cmd_create_user, "purge": cmd_purge,
        "analyze": cmd_analyze, "expand": cmd_expand, "verify-audit": cmd_verify_audit,
        "telegram-login": cmd_telegram_login,
    }[args.cmd](args, settings, db)


if __name__ == "__main__":
    main()
