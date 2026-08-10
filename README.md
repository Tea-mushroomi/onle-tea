# 🏛️ Реставратор фасадов

Нейросеть восстанавливает облик зданий по фото и описанию.
Pollinations — генерация по тексту, AI Horde — реставрация из фото.

## ✨ Возможности
- 📝 Генерация зданий по описанию (38 стилей, только здания)
- 🔨 Реставратор: из фото → целое здание (img2img / outpainting)
- 🧠 Обучение на 👍/ — промпты улучшаются
- 🖼️ Галерея, лайтбокс, удаление всех
- 🎭 Темы, анимации, 🐹 хомяк-тапалка (hamstercoin 🪙 / )
- 🫖 Пасхалка-чайник со звуком
- 📄 Полное логирование (`server.log`, `/log`)
- 🐍 Python 3.13 / 3.14

## 🚀 Запуск локально
```bash
pip install -r requirements.txt
python server.py
```
Открой http://localhost:5000

## 🔑 Ключи
Скопируй `keys.example.json` → `keys.json` и впиши свои ключи.
`keys.json` НЕ коммитится (см. `.gitignore`).

## 🌐 Запуск в сети (Render)
- Build: `pip install -r requirements.txt`
- Start: `gunicorn server:app`
- Ключи — в Environment Variables (`AIHORDE_API_KEY`, `POLLINATIONS_TOKEN`).

## 🚇 Быстрый туннель
```bash
python server.py
cloudflared tunnel --url http://localhost:5000
```