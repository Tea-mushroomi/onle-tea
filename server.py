# -*- coding: utf-8 -*-
"""🏛️ Реставратор фасадов — Python 3.13/3.14 (Flask).
Ключи в keys.json. Лог каждого действия. Чайник/пакетик/звук из static/easter/.
Pollinations — генерация, Horde — реставрация. Только здания. Обучение на лайках.
"""

import base64
import difflib
import hashlib
import io
import json
import logging
import os
import random
import re
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import requests
from flask import Flask, render_template, request, send_from_directory, redirect, url_for
from PIL import Image

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
GALLERY_DIR = BASE_DIR / "gallery"
DATASET_DIR = BASE_DIR / "dataset"
HISTORY_FILE = BASE_DIR / "history.json"
LEARNING_FILE = BASE_DIR / "learning.json"
LOG_FILE = BASE_DIR / "server.log"
KEYS_FILE = BASE_DIR / "keys.json"
GALLERY_DIR.mkdir(exist_ok=True)
DATASET_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%d.%m.%Y %H:%M:%S",
    handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()],
)
log = logging.getLogger("restaurator")


# ===== БЕЗОПАСНОСТЬ: ключи из keys.json =====
def load_keys():
    data = {}
    if KEYS_FILE.exists():
        try:
            data = json.loads(KEYS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            log.error(f"keys.json повреждён: {e}")
    return {
        "POLLINATIONS_TOKEN": data.get("pollinations_token") or os.getenv("POLLINATIONS_TOKEN", ""),
        "AIHORDE_API_KEY": data.get("aihorde_api_key") or os.getenv("AIHORDE_API_KEY", "0000000000"),
    }

KEYS = load_keys()
POLLINATIONS_TOKEN = KEYS["POLLINATIONS_TOKEN"]
AIHORDE_API_KEY = KEYS["AIHORDE_API_KEY"]


# ===== 🫖 ПАСХАЛКИ: чайник / пакетик / звук из static/easter/ =====
def get_teapot_image():
    d = BASE_DIR / "static" / "easter"
    for name in ("teapot.png", "teapot.jpg", "teapot.jpeg", "teapot.gif", "teapot.webp"):
        if (d / name).exists():
            return f"/static/easter/{name}"
    return None

def get_teabag_image():
    d = BASE_DIR / "static" / "easter"
    for name in ("teabag.png", "teabag.jpg", "teabag.jpeg", "teabag.gif", "teabag.webp"):
        if (d / name).exists():
            return f"/static/easter/{name}"
    return None

def get_teapot_sound():
    d = BASE_DIR / "static" / "easter"
    for name in ("teapot.mp3", "teapot.wav", "teapot.ogg"):
        if (d / name).exists():
            return f"/static/easter/{name}"
    return None


POLLINATIONS_URL = "https://image.pollinations.ai/prompt/"
POLLINATIONS_TEXT = "https://text.pollinations.ai/"
WIKI_API = "https://ru.wikipedia.org/api/rest_v1/page/summary/"
AIHORDE_URL = "https://aihorde.net/api/v2"

CONNECT_TIMEOUT = 15
READ_TIMEOUT = 120
REQUEST_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)
MAX_RETRIES = 3
RETRY_BACKOFF = 2
POLLINATIONS_MAX_TIME = 90
POLLINATIONS_INTERVAL = 5 if POLLINATIONS_TOKEN else 15

HTTP_PROXY = os.getenv("HTTP_PROXY", "")
HTTPS_PROXY = os.getenv("HTTPS_PROXY", "")

_rate_lock = threading.Lock()
_last_poll_ts = 0.0
_translation_cache = {}
_wiki_cache = {}
_image_hashes = None
_horde_models_cache = None
_horde_models_cache_time = 0
_poll_models_cache = None
_poll_models_cache_time = 0

_session = requests.Session()
_session.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"),
    "Accept": "*/*", "Connection": "keep-alive",
})
if HTTP_PROXY or HTTPS_PROXY:
    _session.proxies = {"http": HTTP_PROXY or HTTPS_PROXY, "https": HTTPS_PROXY or HTTP_PROXY}

HORDE_NEGATIVE = ("people, humans, person, crowd, faces, portrait, animals, "
                  "blurry, low quality, deformed, watermark, text")
RESTORE_NEGATIVE = ("cropped, fragment, close-up, partial building, cut off, "
                    "zoomed, half building, missing roof, missing walls")
BUILDING_ONLY = ("architecture only, building exterior only, facade, "
                 "no people, no humans, no animals, no cars, no food, "
                 "no interior, no objects, no text, no watermark")
STOPWORDS = {"the", "a", "an", "of", "and", "with", "building", "only",
             "no", "view", "photo", "photograph", "architectural", "facade"}

STYLES = {
    "Без стиля": "",
    "Сталинка / неоклассика 1950-х": "Stalin-era neoclassical architecture, ornate cornice, arched windows, pastel colors",
    "Сталинский ампир / высотка": "Stalinist empire skyscraper, tiered tower with spire, monumental",
    "Хрущёвка": "Soviet khrushchyovka panel block, 5 stories, plain concrete panels",
    "Брежневка": "Soviet brezhnevka brick apartment block, repetitive balconies",
    "Советский модернизм / брутализм": "Soviet brutalist architecture, raw concrete, monumental forms",
    "Конструктивизм 1920-х": "1920s constructivist architecture, ribbon windows, avant-garde",
    "Русский классицизм / усадьба": "Russian classicism manor, portico with columns, yellow facade",
    "Доходный дом XIX века": "19th century Russian apartment building, red brick, ornate windows",
    "Православный храм": "Russian orthodox church, golden onion domes, white walls",
    "Деревянное зодчество": "traditional Russian wooden architecture, log house, carved frames",
    "Неоготика": "gothic revival, pointed arches, rose window, grey stone, spires",
    "Готика": "gothic cathedral, pointed arches, flying buttresses, tall spires",
    "Ренессанс": "Italian renaissance palazzo, rusticated stone, arched windows",
    "Барокко": "baroque palace facade, rich stucco, pilasters, pastel colors",
    "Рококо": "rococo palace facade, pastel colors, delicate stucco",
    "Ар-деко": "art deco facade, geometric ornament, limestone, stepped silhouette",
    "Баухаус": "bauhaus architecture, white cubic volumes, flat roof, glass",
    "Интернациональный стиль": "international style skyscraper, glass and steel tower",
    "Хай-тек": "high-tech architecture, exposed steel, glass curtain walls",
    "Деконструктивизм": "deconstructivist architecture, twisted forms, sharp angles",
    "Постмодернизм": "postmodern architecture, playful classical references, bright colors",
    "Параметризм / биотек": "parametric architecture, flowing curved surfaces, Zaha Hadid style",
    "Минимализм": "minimalist architecture, clean white volumes, frameless glazing",
    "Скандинавский дом": "Scandinavian minimalist house, light wood, panoramic windows",
    "Альпийское шале": "alpine chalet, wide sloping roof, wooden balconies, stone base",
    "Средиземноморский стиль": "mediterranean villa, white stucco, terracotta roof",
    "Фахверк": "half-timbered fachwerk house, dark wooden beams, light plaster",
    "Тюдор": "tudor style house, black and white timbering, steep gables",
    "Викторианский стиль": "victorian architecture, polychrome brickwork, bay windows",
    "Георгианский стиль": "georgian townhouse, red brick, white sash windows, symmetry",
    "Колониальный стиль": "colonial architecture, verandas with columns, symmetrical facade",
    "Промышленное здание": "industrial factory, red brick, sawtooth roof, large windows",
    "Промышленный лофт": "industrial loft, old factory brick, huge steel windows",
    "Современный ЖК": "contemporary residential complex, ventilated facade, large glazing",
    "Эко-архитектура": "eco architecture, green facade, vertical gardens, wooden structure",
    "Древнекитайская архитектура": "ancient Chinese architecture, pagoda, curved eaves, red columns, glazed tile roof",
    "Древнеяпонская архитектура": "ancient Japanese architecture, wooden temple, curved roof, shoji screens",
    "Античная архитектура": "ancient Greek Roman architecture, marble columns, pediment, temple",
    "Исламская архитектура": "islamic architecture, geometric patterns, horseshoe arches, minaret, dome",
}

WIKI_TITLES = {
    "Сталинка / неоклассика 1950-х": "Сталинский ампир",
    "Хрущёвка": "Хрущёвка", "Брежневка": "Брежневка",
    "Советский модернизм / брутализм": "Брутализм (архитектура)",
    "Конструктивизм 1920-х": "Конструктивизм",
    "Русский классицизм / усадьба": "Классицизм",
    "Доходный дом XIX века": "Доходный дом",
    "Неоготика": "Неоготика", "Готика": "Готическая архитектура",
    "Ренессанс": "Архитектура Возрождения", "Барокко": "Барокко",
    "Рококо": "Рококо", "Ар-деко": "Ар-деко", "Баухаус": "Баухаус",
    "Хай-тек": "Хай-тек (архитектура)", "Минимализм": "Минимализм",
    "Древнекитайская архитектура": "Китайская архитектура",
    "Древнеяпонская архитектура": "Японская архитектура",
    "Античная архитектура": "Античная архитектура",
    "Исламская архитектура": "Исламская архитектура",
}

ARCH_TERMS = {
    "древнекитайское": "ancient Chinese", "древнекитайская": "ancient Chinese",
    "древнеяпонское": "ancient Japanese", "древнеяпонская": "ancient Japanese",
    "здание": "building", "дом": "house", "фасад": "facade",
    "окно": "window", "окна": "windows", "крыша": "roof",
    "колонна": "column", "колонны": "columns", "арка": "arch", "арки": "arches",
    "купол": "dome", "башня": "tower", "стена": "wall", "стены": "walls",
    "этаж": "floor", "балкон": "balcony", "дверь": "door",
    "храм": "temple", "церковь": "church", "дворец": "palace", "замок": "castle",
    "собор": "cathedral", "современный": "modern", "кирпичный": "brick",
    "деревянный": "wooden", "каменный": "stone", "мраморный": "marble",
    "белый": "white", "красный": "red", "желтый": "yellow", "жёлтый": "yellow",
    "серый": "grey", "готический": "gothic", "барокко": "baroque",
    "классицизм": "classicism", "пятиэтажный": "five-storey", "лепнина": "stucco",
}


def has_cyrillic(text):
    return any('\u0400' <= c <= '\u04FF' for c in text)


def safe_folder_name(style):
    name = re.sub(r'[^\w\s-]', '', style)
    name = name.strip().replace(' ', '_')
    return re.sub(r'_+', '_', name)[:60]


# ===== ДАТАСЕТ =====
def scan_dataset():
    dataset = {}
    if not DATASET_DIR.exists():
        return dataset
    style_map = {safe_folder_name(s): s for s in STYLES if s != "Без стиля"}
    sorted_safe = sorted(style_map.keys(), key=len, reverse=True)
    for f in sorted(DATASET_DIR.iterdir()):
        if f.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
            continue
        stem = f.stem
        matched = None
        for safe_name in sorted_safe:
            if stem == safe_name or stem.startswith(safe_name + "_") or stem.startswith(safe_name + "-"):
                matched = style_map[safe_name]; break
        if matched is None:
            for safe_name in sorted_safe:
                if stem.startswith(safe_name):
                    matched = style_map[safe_name]; break
        if matched is None:
            matched = "Прочее"
        dataset.setdefault(matched, {"images": [], "count": 0})
        dataset[matched]["images"].append(f.name)
        dataset[matched]["count"] += 1
    return dataset


def get_dataset_context(style):
    info_file = DATASET_DIR / f"{safe_folder_name(style)}_info.txt"
    if info_file.exists():
        try:
            return info_file.read_text(encoding='utf-8').strip()[:200]
        except Exception:
            pass
    return ""


def dataset_stats():
    ds = scan_dataset()
    return {"styles": len(ds), "images": sum(v["count"] for v in ds.values()),
            "filled": sum(1 for v in ds.values() if v["count"] > 0)}


def get_offline_image(style):
    ds = scan_dataset()
    candidates = []
    if style in ds and ds[style]["images"]:
        candidates += [DATASET_DIR / i for i in ds[style]["images"]]
    if not candidates:
        for data in ds.values():
            candidates += [DATASET_DIR / i for i in data["images"]]
    if not candidates:
        return None
    try:
        return random.choice(candidates).read_bytes()
    except Exception:
        return None


# ===== ИСТОРИЯ / ГАЛЕРЕЯ =====
def load_history():
    if HISTORY_FILE.exists():
        try:
            return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_history(history):
    try:
        HISTORY_FILE.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"history: {e}")


def get_image_hashes():
    global _image_hashes
    if _image_hashes is None:
        _image_hashes = set()
        for f in GALLERY_DIR.glob("*.png"):
            try:
                _image_hashes.add(hashlib.md5(f.read_bytes()).hexdigest())
            except Exception:
                pass
    return _image_hashes


def save_to_gallery(img_bytes, meta):
    img_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    path = GALLERY_DIR / f"{img_id}.png"
    path.write_bytes(img_bytes)
    get_image_hashes().add(hashlib.md5(img_bytes).hexdigest())
    history = load_history()
    entry = dict(meta)
    entry.update({"id": img_id, "time": datetime.now().strftime("%d.%m.%Y %H:%M:%S"), "file": path.name})
    history.insert(0, entry)
    save_history(history)
    log.info(f"💾 Сохранено: {path.name} ({meta.get('engine')})")
    return img_id, path.name


def clear_gallery():
    count = 0
    for f in GALLERY_DIR.glob("*.png"):
        try:
            f.unlink(); count += 1
        except Exception as e:
            log.error(f"Не удалось удалить {f.name}: {e}")
    save_history([])
    global _image_hashes
    _image_hashes = set()
    return count


# ===== ОБУЧЕНИЕ =====
def load_learning():
    if LEARNING_FILE.exists():
        try:
            return json.loads(LEARNING_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"total_generations": 0, "total_likes": 0, "total_dislikes": 0,
            "accuracy": 0.0, "style_weights": {}, "style_tokens": {}}


def save_learning(data):
    try:
        LEARNING_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        log.error(f"learning: {e}")


def _prompt_tokens(prompt):
    out = []
    for seg in (prompt or "").split(","):
        seg = seg.strip().lower()
        if len(seg) > 3 and seg not in STOPWORDS and not seg.isdigit():
            out.append(seg)
    return out


def record_feedback(image_id, rating):
    try:
        history = load_history()
        entry = next((h for h in history if h.get("id") == image_id), None)
        if not entry:
            return False
        learning = load_learning()
        learning["total_generations"] = learning.get("total_generations", 0) + 1
        style = entry.get("style", "")
        tokens = _prompt_tokens(entry.get("prompt", ""))
        st = learning.setdefault("style_tokens", {}).setdefault(style, {"good": {}, "bad": {}})
        if rating == "like":
            learning["total_likes"] = learning.get("total_likes", 0) + 1
            if style:
                learning.setdefault("successful_patterns", {})[style] = \
                    learning.get("successful_patterns", {}).get(style, 0) + 1
            for t in tokens:
                st["good"][t] = st["good"].get(t, 0) + 1
                st["bad"].pop(t, None)
        else:
            learning["total_dislikes"] = learning.get("total_dislikes", 0) + 1
            if style:
                learning.setdefault("failed_patterns", {})[style] = \
                    learning.get("failed_patterns", {}).get(style, 0) + 1
            for t in tokens:
                st["bad"][t] = st["bad"].get(t, 0) + 1
        sw = learning.setdefault("style_weights", {})
        for s in learning.get("successful_patterns", {}):
            succ = learning["successful_patterns"].get(s, 0)
            fail = learning.get("failed_patterns", {}).get(s, 0)
            if succ + fail > 0:
                sw[s] = succ / (succ + fail)
        likes = learning.get("total_likes", 0)
        total_fb = likes + learning.get("total_dislikes", 0)
        learning["accuracy"] = (likes / total_fb) if total_fb > 0 else 0.0
        save_learning(learning)
        log.info(f"🧠 Feedback {rating}: {image_id} (стиль={style})")
        return True
    except Exception as e:
        log.error(f"feedback: {e}")
        return False


def get_learning_adjustments(style):
    learning = load_learning()
    st = learning.get("style_tokens", {}).get(style, {})
    good = [k for k, _ in sorted(st.get("good", {}).items(), key=lambda x: -x[1])[:5]]
    bad = [k for k, _ in sorted(st.get("bad", {}).items(), key=lambda x: -x[1])[:5]]
    return good, bad


# ===== ПЕРЕВОД / ПРОМПТ =====
def local_translate(text):
    if not text:
        return ""
    result = " " + text.lower() + " "
    for ru, en in sorted(ARCH_TERMS.items(), key=lambda x: -len(x[0])):
        result = result.replace(" " + ru + " ", " " + en + " ")
    keys = [k for k in ARCH_TERMS if " " not in k]
    vals = set(ARCH_TERMS.values())
    out = []
    for tok in result.split():
        if tok in vals:
            out.append(tok); continue
        m = difflib.get_close_matches(tok, keys, n=1, cutoff=0.75)
        out.append(ARCH_TERMS[m[0]] if m else tok)
    return " ".join(out).strip()


def ensure_building_prompt(desc):
    desc = (desc or "").strip()
    if len(desc) < 3:
        desc = "classic multi-storey residential building"
    if len(desc) < 25:
        desc += ", detailed facade, windows, roof, entrance"
    return (f"architectural photograph of a building, {desc}, "
            f"building only, facade view, {BUILDING_ONLY}")


def describe_photo_local(img):
    try:
        small = img.resize((32, 32)).convert("RGB")
        px = list(small.getdata()); n = len(px)
        r = sum(p[0] for p in px) / n; g = sum(p[1] for p in px) / n; b = sum(p[2] for p in px) / n
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        tone = "dark moody" if lum < 90 else ("bright daylight" if lum > 170 else "soft daylight")
        if r > g + 20 and r > b + 20: hue = "reddish brick"
        elif g > r + 10 and g > b + 10: hue = "greenish overgrown"
        elif b > r + 10 and b > g + 10: hue = "bluish stone"
        else: hue = "gray weathered stone"
        return f"{tone}, {hue}, ruined texture"
    except Exception:
        return ""


def fetch_wikipedia_reference(style):
    title = WIKI_TITLES.get(style)
    if not title:
        return ""
    if title in _wiki_cache:
        return _wiki_cache[title]
    try:
        resp = _session.get(WIKI_API + quote(title), timeout=10)
        if resp.status_code == 200:
            result = resp.json().get("extract", "")[:300]
            _wiki_cache[title] = result
            return result
    except Exception:
        pass
    _wiki_cache[title] = ""
    return ""


def translate_to_english(ru_text):
    ru_text = ru_text.strip()
    if not ru_text:
        return ""
    if not has_cyrillic(ru_text):
        return ru_text
    if ru_text in _translation_cache:
        return _translation_cache[ru_text]
    for _ in range(2):
        try:
            prompt = f"Translate to English, short and precise: {ru_text}"
            resp = _session.get(POLLINATIONS_TEXT + quote(prompt), timeout=(10, 45))
            if resp.status_code == 200:
                tr = resp.text.strip().strip('"').strip("'")[:120]
                if tr and not has_cyrillic(tr):
                    _translation_cache[ru_text] = tr
                    return tr
        except Exception:
            pass
        time.sleep(2)
    return local_translate(ru_text) or ru_text


def extract_keywords(description, style, wiki_text):
    try:
        prompt = ("You are an expert architect. Return ONLY comma-separated English visual "
                  "keywords (style, era, materials, colors, windows, roof). No sentences.\n"
                  f"Style: {style}\nReference: {wiki_text}\nDescription (Russian): {description}")
        resp = _session.get(POLLINATIONS_TEXT + quote(prompt), timeout=(10, 45))
        if resp.status_code == 200:
            kw = " ".join(resp.text.strip().strip('"').strip("'").split())[:400]
            if kw and not has_cyrillic(kw):
                return kw
    except Exception:
        pass
    return translate_to_english(description)


def prepare_prompt(description, style, task, internet_search=False):
    style_en = STYLES.get(style, "")
    dataset_ctx = get_dataset_context(style)
    good, bad = get_learning_adjustments(style)
    reference = ""
    keywords = ""
    if internet_search and description.strip():
        reference = fetch_wikipedia_reference(style)
        keywords = extract_keywords(description, style, reference)
        desc = keywords
    else:
        desc = local_translate(description)
    core = ", ".join(p for p in [desc, dataset_ctx, style_en] if p)
    if good:
        core += ", " + ", ".join(good)
    if task == "plan":
        prompt = (f"architectural floor plan, top view, blueprint, black lines on white, "
                  f"{core}, building only, {BUILDING_ONLY}")
    else:
        prompt = ensure_building_prompt(core)
    return {"prompt": prompt, "keywords": keywords, "reference": reference,
            "dataset_ctx": dataset_ctx, "avoid": bad}


# ===== МОДЕЛИ =====
def get_pollinations_models():
    global _poll_models_cache, _poll_models_cache_time
    now = time.time()
    if _poll_models_cache is not None and (now - _poll_models_cache_time) < 60:
        return _poll_models_cache
    try:
        r = _session.get("https://image.pollinations.ai/models", timeout=10)
        if r.status_code == 200:
            models = r.json()
            if isinstance(models, list) and models:
                _poll_models_cache = models; _poll_models_cache_time = now
                return models
    except Exception:
        pass
    return _poll_models_cache or ["sana"]


def pick_pollinations_model(preferred="turbo"):
    available = get_pollinations_models()
    for m in (preferred, "turbo", "flux", "sana"):
        if m in available:
            return m
    return available[0] if available else "sana"


def get_horde_models_detailed():
    global _horde_models_cache, _horde_models_cache_time
    now = time.time()
    if _horde_models_cache is not None and (now - _horde_models_cache_time) < 60:
        return _horde_models_cache
    try:
        r = _session.get(f"{AIHORDE_URL}/status/models", timeout=15)
        if r.status_code == 200:
            live = [m for m in r.json() if isinstance(m, dict) and m.get("count", 0) > 0]
            if live:
                _horde_models_cache = live; _horde_models_cache_time = now
                return live
    except Exception:
        pass
    return _horde_models_cache or []


def pick_horde_models(limit=5):
    models = get_horde_models_detailed()
    if not models:
        return ["stable_diffusion"]
    blocked = ("hentai", "pony", "furry", "nsfw", "waifu", "anime", "illustrious",
               "yiffy", "babes", "pixel", "comic", "cartoon", "ghibli",
               "fantasy card", "rpg", "dan mumford", "mtg", "illuminati",
               "sci-fi", "vector", "app icon")
    safe = [m for m in models if not any(x in m.get("name", "").lower() for x in blocked)]
    if not safe:
        safe = models
    free = [m for m in safe if (m.get("queued") or 0) == 0 and (m.get("count") or 0) > 0]
    pool = free if free else safe
    pool.sort(key=lambda m: (m.get("eta") or 0, m.get("queued") or 0, -(m.get("count") or 0)))
    return [m["name"] for m in pool[:limit]]


def is_censored_image(img_bytes):
    try:
        img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
        small = img.resize((50, 50)); px = list(small.getdata()); n = len(px)
        r = sum(p[0] for p in px) / n; g = sum(p[1] for p in px) / n; b = sum(p[2] for p in px) / n
        lum = 0.299 * r + 0.587 * g + 0.114 * b
        sat = max(r, g, b) - min(r, g, b)
        return lum < 60 and sat < 20
    except Exception:
        return False


# ===== ГЕНЕРАТОРЫ =====
def generate_pollinations(prompt, width, height, seed, model="turbo", avoid=None):
    global _last_poll_ts
    with _rate_lock:
        wait = max(0.0, POLLINATIONS_INTERVAL - (time.time() - _last_poll_ts))
    if wait > 0:
        time.sleep(wait)
    with _rate_lock:
        _last_poll_ts = time.time()
    if avoid:
        prompt += ", no " + ", no ".join(avoid)
    model = pick_pollinations_model(model)
    start_time = time.time()
    last_error = "неизвестная ошибка"
    for attempt in range(1, MAX_RETRIES + 1):
        if time.time() - start_time > POLLINATIONS_MAX_TIME:
            break
        try:
            url = POLLINATIONS_URL + quote(prompt)
            params = {"width": width, "height": height, "model": model,
                      "seed": seed, "nologo": "true", "enhance": "false"}
            if POLLINATIONS_TOKEN:
                params["token"] = POLLINATIONS_TOKEN
            resp = _session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 429:
                last_error = "Rate limit (429)"; time.sleep(15); continue
            if resp.status_code == 402:
                last_error = "Требуется токен (402)"; break
            if resp.status_code in (502, 503, 504):
                last_error = "Перегружен"; time.sleep(RETRY_BACKOFF * attempt); continue
            if resp.status_code != 200:
                last_error = f"HTTP {resp.status_code}"; time.sleep(RETRY_BACKOFF * attempt); continue
            if "image" not in resp.headers.get("Content-Type", ""):
                last_error = "Не изображение"; time.sleep(RETRY_BACKOFF * attempt); continue
            data = resp.content
            if len(data) < 1000:
                last_error = "Маленький ответ"; time.sleep(RETRY_BACKOFF * attempt); continue
            return data
        except Exception as e:
            last_error = str(e); time.sleep(RETRY_BACKOFF * attempt)
    raise RuntimeError(f"Pollinations не ответил: {last_error}")


def generate_horde(prompt, width, height, fragment_bytes=None, mode="img2img",
                   retry_on_censor=True, avoid=None, restore=False):
    headers = {"apikey": AIHORDE_API_KEY, "Client-Agent": "chai-restoration:1.0:anonymous"}
    negative = HORDE_NEGATIVE
    if avoid:
        negative += ", " + ", ".join(avoid)
    if restore:
        negative += ", " + RESTORE_NEGATIVE
    params = {"width": 512, "height": 512, "cfg_scale": 7}
    if restore:
        params.update({"steps": 24, "cfg_scale": 8,
                       "denoising_strength": 0.7 if fragment_bytes else 1.0})
    else:
        params.update({"steps": 10})
    payload = {"prompt": prompt + " ### " + negative,
               "params": params, "models": pick_horde_models(limit=5),
               "nsfw": False, "r2": False}
    if fragment_bytes and mode in ("img2img", "outpainting"):
        payload["source_image"] = base64.b64encode(fragment_bytes).decode("ascii")
        payload["source_processing"] = mode

    job_id = None
    last_err = ""
    for attempt in range(5):
        try:
            resp = _session.post(f"{AIHORDE_URL}/generate/async", json=payload,
                                 headers=headers, timeout=(60, 180))
            if resp.status_code not in (200, 202):
                last_err = f"HTTP {resp.status_code}: {resp.text[:200]}"
                time.sleep(3); continue
            job_id = resp.json().get("id")
            if job_id:
                break
        except Exception as e:
            last_err = str(e); time.sleep(4)
    if job_id is None:
        raise RuntimeError(f"Horde: не удалось отправить ({last_err})")

    done = False
    for i in range(90):
        time.sleep(5)
        try:
            check = _session.get(f"{AIHORDE_URL}/generate/check/{job_id}",
                                 headers=headers, timeout=(CONNECT_TIMEOUT, 30)).json()
        except Exception:
            continue
        if check.get("done"):
            done = True; break
        if check.get("faulted"):
            raise RuntimeError("Horde: задача сломалась")
    if not done:
        raise RuntimeError("Horde: таймаут ожидания")

    result = None
    for _ in range(3):
        try:
            result = _session.get(f"{AIHORDE_URL}/generate/status/{job_id}",
                                  headers=headers, timeout=(CONNECT_TIMEOUT, 60)).json()
            break
        except Exception:
            time.sleep(3)
    if result is None:
        raise RuntimeError("Horde: не удалось получить результат")
    gens = result.get("generations", [])
    if not gens:
        raise RuntimeError("Horde: пустой результат")
    img_field = gens[0].get("img", "")
    if img_field.startswith("http"):
        raw = None
        for _ in range(3):
            try:
                raw = _session.get(img_field, timeout=REQUEST_TIMEOUT).content; break
            except Exception:
                time.sleep(3)
        if raw is None:
            raise RuntimeError("Horde: не удалось скачать")
    else:
        raw = base64.b64decode(img_field)

    if retry_on_censor and is_censored_image(raw):
        log.warning("🚫 Заглушка цензуры — переключаюсь на txt2img...")
        return generate_horde(prompt, width, height, fragment_bytes=None,
                              mode="txt2img", retry_on_censor=False,
                              avoid=avoid, restore=restore)
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img = img.resize((width, height), Image.LANCZOS)
        buf = io.BytesIO(); img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return raw


def generate_with_fallback(prompt, width, height, seed, model="turbo", style="", avoid=None):
    try:
        return generate_pollinations(prompt, width, height, seed, model, avoid), "pollinations"
    except Exception as e:
        log.warning(f"Pollinations упал: {e}")
    try:
        return generate_horde(prompt, width, height, avoid=avoid), "aihorde"
    except Exception as e:
        log.warning(f"Horde упал: {e}")
    offline = get_offline_image(style)
    if offline:
        return offline, "offline-dataset"
    raise RuntimeError("Все сервисы недоступны, а датасет пуст")


# ===== РЕНДЕР =====
def render_page(template, page, **kwargs):
    return render_template(template, page=page, styles=list(STYLES.keys()),
                           dataset_stats=dataset_stats(), learning=load_learning(),
                           gallery=load_history(), year=datetime.now().year,
                           teapot_img=get_teapot_image(), teabag_img=get_teabag_image(),
                           teapot_snd=get_teapot_sound(), **kwargs)


# ===== ЛОГ КАЖДОГО ЗАПРОСА =====
@app.before_request
def log_request():
    if request.path.startswith("/static"):
        return
    log.info(f"🖱️ {request.method} {request.path}")


# ===== СТРАНИЦЫ =====
@app.route("/")
def page_index():
    return render_page("index.html", "index")


@app.route("/gallery")
def page_gallery():
    return render_page("gallery.html", "gallery",
                       notice=request.args.get("notice"), error=request.args.get("error"))


@app.route("/learning")
def page_learning():
    return render_page("learning.html", "learning")


@app.route("/about")
def page_about():
    return render_page("about.html", "about")


@app.route("/teapot")
def teapot():
    log.info("🫖 Кто-то нашёл чайник!")
    img = get_teapot_image()
    snd = get_teapot_sound()
    picture = (f'<img src="{img}" style="max-width:220px;border-radius:20px;'
               f'margin:20px auto;box-shadow:0 0 40px rgba(201,177,255,.5)">'
               if img else '<div style="font-size:120px">🫖</div>')
    audio_tag = f'<audio id="teapot-sound" src="{snd}" preload="auto"></audio>' if snd else ''
    body = ("<html><body style='background:#1a1625;color:#c9b1ff;text-align:center;"
            "font-family:Georgia;padding-top:8%;'>" + picture + audio_tag +
            "<h1>418 — Я чайник!</h1>"
            "<p>Короткая передышка: даже реставраторам фасадов нужен чай.</p>"
            "<p style='opacity:.7'>🔊 кликни в любом месте, если тишина</p>"
            "<a href='/' style='color:#e8b4c8'>← Вернуться к зданиям</a>"
            "<script>"
            "function whistle(){try{var ctx=new (window.AudioContext||window.webkitAudioContext)();"
            "var o=ctx.createOscillator(),g=ctx.createGain();o.type='sine';"
            "o.frequency.setValueAtTime(880,ctx.currentTime);"
            "o.frequency.exponentialRampToValueAtTime(1760,ctx.currentTime+0.4);"
            "g.gain.setValueAtTime(0.0001,ctx.currentTime);"
            "g.gain.exponentialRampToValueAtTime(0.3,ctx.currentTime+0.05);"
            "g.gain.exponentialRampToValueAtTime(0.0001,ctx.currentTime+1.2);"
            "o.connect(g);g.connect(ctx.destination);o.start();o.stop(ctx.currentTime+1.3);}catch(e){}}"
            "function playSound(){var a=document.getElementById('teapot-sound');"
            "if(a){a.currentTime=0;a.play().catch(function(){});}else{whistle();}}"
            "playSound();document.addEventListener('click',playSound,{once:true});"
            "</script></body></html>")
    return body, 418


@app.route("/health")
def health():
    return {"pollinations": "OK", "aihorde": "OK",
            "aihorde_key": "задан" if AIHORDE_API_KEY != "0000000000" else "анонимный",
            "time": datetime.now().strftime("%H:%M:%S")}


@app.route("/log")
def view_log():
    try:
        tail = "\n".join(LOG_FILE.read_text(encoding="utf-8").splitlines()[-200:])
    except Exception as e:
        tail = f"Не удалось прочитать лог: {e}"
    return f"<pre style='font:12px monospace;white-space:pre-wrap'>{tail}</pre>"


@app.route("/log_action", methods=["POST"])
def log_action():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", ""))[:120]
    if action:
        log.info(f"🖱️ Действие: {action}")
    return {"ok": True}


# ===== ДЕЙСТВИЯ =====
@app.route("/generate", methods=["POST"])
def generate():
    try:
        task = request.form.get("task", "facade")
        description = request.form.get("description", "")
        style = request.form.get("style", "Без стиля")
        model = request.form.get("model", "turbo")
        internet_search = request.form.get("internet_search") == "on"
        seed = int(request.form.get("seed") or 0) or random.randint(1, 999999)
        if not description.strip():
            return render_page("index.html", "index", error="Введите описание здания")
        w, h = (768, 1024) if task == "facade" else (896, 896)
        prep = prepare_prompt(description, style, task, internet_search)
        img_bytes, engine = generate_with_fallback(
            prep["prompt"], w, h, seed, model, style, avoid=prep["avoid"])
        img_id, filename = save_to_gallery(img_bytes, {
            "mode": "text", "task": task, "prompt": prep["prompt"], "style": style,
            "engine": engine, "seed": seed, "description_ru": description, "model": model})
        result = {"image": f"/gallery/{filename}", "prompt": prep["prompt"],
                  "engine": engine, "width": w, "height": h, "id": img_id,
                  "keywords": prep["keywords"], "reference": prep["reference"],
                  "dataset_ctx": prep["dataset_ctx"]}
        return render_page("index.html", "index", result=result)
    except Exception as e:
        log.exception(f"❌ ОШИБКА генерации: {e}")
        return render_page("index.html", "index", error=f"Ошибка генерации: {e}")


@app.route("/restore", methods=["GET", "POST"])
def restore():
    if request.method == "GET":
        return render_page("restore.html", "restore")
    try:
        file = request.files.get("file")
        if not file:
            return render_page("restore.html", "restore", error="Загрузите фото здания")
        style = request.form.get("style", "Без стиля")
        mode = request.form.get("mode", "img2img")
        hint = request.form.get("description", "")
        seed = int(request.form.get("seed") or 0) or random.randint(1, 999999)
        img = Image.open(file.stream).convert("RGB")
        photo_desc = describe_photo_local(img)
        img.thumbnail((384, 384))
        buf = io.BytesIO(); img.save(buf, format="JPEG", quality=70)
        fragment_bytes = buf.getvalue()
        base_hint = ", ".join(x for x in [
            hint.strip(), photo_desc,
            "entire complete building from ground to roof, fully reconstructed, "
            "whole facade visible, restore missing parts, rebuild ruins into intact building"
        ] if x)
        prep = prepare_prompt(base_hint, style, "restore", internet_search=False)
        notice = None
        try:
            img_bytes = generate_horde(prep["prompt"], 768, 768,
                                       fragment_bytes=fragment_bytes, mode=mode,
                                       avoid=prep["avoid"], restore=True)
            engine = "aihorde"
        except Exception as horde_err:
            log.warning(f"Horde-реставрация не удалась: {horde_err}")
            img_bytes, engine = generate_with_fallback(
                prep["prompt"], 768, 768, seed, "turbo", style, avoid=prep["avoid"])
            notice = "⚠️ Фото-реставрация недоступна — сгенерировано по описанию."
        img_id, filename = save_to_gallery(img_bytes, {
            "mode": "restore", "task": "restore", "prompt": prep["prompt"],
            "style": style, "engine": engine, "seed": seed,
            "source": file.filename, "restore_mode": mode})
        result = {"image": f"/gallery/{filename}", "prompt": prep["prompt"],
                  "engine": engine, "width": 768, "height": 768, "id": img_id,
                  "keywords": prep["keywords"], "reference": prep["reference"],
                  "dataset_ctx": prep["dataset_ctx"]}
        return render_page("restore.html", "restore", result=result, notice=notice)
    except Exception as e:
        log.exception(f"❌ ОШИБКА реставрации: {e}")
        return render_page("restore.html", "restore", error=f"Ошибка реставрации: {e}")


@app.route("/feedback", methods=["POST"])
def feedback():
    image_id = request.form.get("image_id", "")
    rating = request.form.get("rating", "")
    if image_id and rating in ("like", "dislike"):
        if record_feedback(image_id, rating):
            emoji = "👍" if rating == "like" else "👎"
            return redirect(url_for("page_gallery", notice=f"Спасибо за оценку {emoji}! Нейросеть обучается."))
        return redirect(url_for("page_gallery", error="Не найдено изображение"))
    return redirect(url_for("page_gallery", error="Ошибка обработки оценки"))


@app.route("/delete", methods=["POST"])
def delete():
    filename = request.form.get("file", "")
    if filename and ".." not in filename:
        p = GALLERY_DIR / filename
        if p.exists():
            p.unlink(missing_ok=True)
        save_history([h for h in load_history() if h.get("file") != filename])
    return redirect(url_for("page_gallery", notice="Изображение удалено"))


@app.route("/delete_all", methods=["POST"])
def delete_all():
    count = clear_gallery()
    return redirect(url_for("page_gallery", notice=f"Удалено генераций: {count}"))


@app.route("/dataset/img/<path:filename>")
def serve_dataset(filename):
    if ".." in filename:
        return "Bad", 400
    return send_from_directory(DATASET_DIR, filename)


@app.route("/gallery/<path:filename>")
def serve_gallery(filename):
    return send_from_directory(GALLERY_DIR, filename)


@app.errorhandler(404)
def not_found(e):
    return render_page("index.html", "index", error="Страница не найдена"), 404


@app.errorhandler(500)
def server_error(e):
    log.exception(f"500: {e}")
    return render_page("index.html", "index", error="Внутренняя ошибка сервера"), 500


if __name__ == "__main__":
    log.info("🏛️ Реставратор фасадов запущен (Python 3.13/3.14)")
    log.info(f"📍 http://localhost:5000 · 🫖 пасхалка: /teapot")
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)