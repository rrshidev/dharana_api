# -*- coding: utf-8 -*-
"""Живой смоук CatalogClient против api.dharana.ru (без MAX)."""

import asyncio

from maxbot.catalog import CatalogClient


async def main() -> None:
    catalog = CatalogClient("https://api.dharana.ru")
    try:
        cats = await catalog.categories("ru")
        print("categories:", len(cats or []), [c["id"] for c in cats or []][:3])

        items = await catalog.category_asanas("sit_lie+", "ru")
        print("sit_lie+ items:", len(items or []))
        name = items[0]["name"]
        print("first asana:", name)

        detail = await catalog.asana(name, "ru")
        print("detail ok:", bool(detail), "desc chars:", len((detail or {}).get("description") or ""))

        found = await catalog.search("Тада", "ru", limit=5)
        print("search total:", (found or {}).get("total"), "items:", len((found or {}).get("items") or []))

        daily = await catalog.daily("ru")
        print("daily:", (daily or {}).get("name"))

        random_asana = await catalog.random_asana("en")
        print("random en:", (random_asana or {}).get("name"))

        basics = await catalog.basics("ru")
        steps = await catalog.steps("ru")
        print("basics:", len(basics or []), "steps:", len(steps or []))

        photo = await catalog.photo((detail or {}).get("image_url") or "")
        print("photo bytes:", len(photo or b""), "magic:", (photo or b"")[:2])

        # Второй вызов — из кеша (сеть не дёргаем).
        again = await catalog.categories("ru")
        print("cache same object:", again is cats)
    finally:
        await catalog.close()


if __name__ == "__main__":
    asyncio.run(main())
