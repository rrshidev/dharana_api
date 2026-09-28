import csv
import os
import sys

BASE = "/opt/dharana/bot_data"
CATALOG = os.path.join(BASE, "catalog")
MEDIA_CATALOG = "/opt/dharana/media/catalog"

CAT_DISPLAY = {
    "sit_lie+": "Асаны сидя и лёжа",
    "stay+": "Асаны стоя",
    "hand+": "Балансы на руках",
    "coup+": "Перевёрнутые",
    "sag+": "Прогибы",
    "power+": "Силовые",
}

PHOTO_EXTS = (".jpg", ".jpeg", ".png")
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


def asana_names_by_cat():
    names = {}
    for cat in os.listdir(CATALOG):
        cat_dir = os.path.join(CATALOG, cat)
        if not os.path.isdir(cat_dir):
            continue
        for f in os.listdir(cat_dir):
            low = f.lower()
            if not low.endswith(".txt"):
                continue
            base = f[:-4]
            if low.endswith(".en.txt"):
                base = base[:-3]
            names.setdefault(base, (cat, cat_dir))
    return names


def video_stems():
    files = {}
    for f in os.listdir(MEDIA_CATALOG):
        stem, ext = os.path.splitext(f)
        if ext.lower() in VIDEO_EXTS:
            files.setdefault(stem, f)
    return files


def check_name_mismatch(asanas, videos):
    """Видео без точного совпадения имени с текстом каталога (регистр/пробел)."""
    problems = []
    lower = {}
    for name, (cat, _) in asanas.items():
        lower.setdefault(name.lower(), (name, cat))
    for stem in sorted(videos):
        canonical = lower.get(stem.lower())
        if canonical is None:
            problems.append(("НЕТ_АСАНЫ", stem, "-"))
        elif canonical[0] != stem:
            problems.append(("РЕГИСТР", stem, canonical[0]))
    return problems


def check_duplicates(asanas):
    """Одно и то же имя текста в разных категориях (кандидаты на путаницу фото/видео)."""
    by_lower = {}
    for name, (cat, _) in asanas.items():
        by_lower.setdefault(name.lower(), []).append(cat)
    return {name: cats for name, cats in by_lower.items() if len(cats) > 1}


def run_missing_media(asanas, videos):
    rows = []
    for name, (cat, cat_dir) in sorted(asanas.items()):
        has_jpg = any(os.path.exists(os.path.join(cat_dir, name + e)) for e in (".jpg", ".jpeg"))
        has_png = os.path.exists(os.path.join(cat_dir, name + ".png"))
        has_video = name in videos
        photo = "jpg" if has_jpg else ("png" if has_png else "none")
        problems = []
        if not has_jpg:
            problems.append("нет_фото_jpg")
        if not has_video:
            problems.append("нет_видео")
        if problems:
            rows.append([name, cat, CAT_DISPLAY.get(cat, cat), photo,
                         "+" if has_video else "-", "; ".join(problems)])
    rows.sort(key=lambda r: (r[1], r[0]))

    with open("/tmp/missing_media.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Асана", "Категория_id", "Категория", "Фото(файл)", "Видео", "Чего нет"])
        w.writerows(rows)

    with open("/tmp/missing_media.md", "w", encoding="utf-8") as f:
        f.write("| Асана | Категория_id | Категория | Фото | Видео | Чего нет |\n")
        f.write("|---|---|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} | {r[5]} |\n")

    return len(rows)


def main():
    asanas = asana_names_by_cat()
    videos = video_stems()

    problems = check_name_mismatch(asanas, videos)
    dups = check_duplicates(asanas)
    missing = run_missing_media(asanas, videos)

    print(f"asanas={len(asanas)} videos={len(videos)} missing_rows={missing}")

    ok = True
    if problems:
        ok = False
        print("NAME MISMATCH (регистр/пробел видео vs каталог):")
        for kind, stem, canon in problems:
            print(f"  {kind}: {stem!r} -> {canon!r}")
    if dups:
        # Дубли сообщаем, но на пайплайн не вешаем: бывают легитимные одноимённые в разных разделах.
        print("DUPLICATE NAMES (в разных категориях):")
        for name, cats in sorted(dups.items()):
            print(f"  {name!r}: {cats}")

    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()