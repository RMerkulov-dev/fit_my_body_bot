---
title: Fit My Body
emoji: 🏋️‍♂️
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: Telegram-бот для підрахунку калорій, ваги та статистики (використовує PostgreSQL)
license: mit
---

# Fit My Body — Telegram-бот для контролю калорій та ваги

Це Telegram-бот, який допомагає вести щоденник харчування, ваги та отримувати статистику.

Особливості:
- Внесення ваги та калорій за прийоми їжі
- Розрахунок базової норми калорій (BMR)
- Статистика за день / тиждень / місяць
- Зберігання даних у PostgreSQL

Проект працює в Docker-контейнері на Hugging Face Spaces.

**Важливо:** Це **не веб-додаток**, а Telegram-бот. Щоб він працював стабільно, рекомендую використовувати webhook-режим (а не polling) та хостинг типу Render / Railway. Hugging Face Spaces погано підходить для довготривалих polling-ботів.

Файли:
- `bot.py` — основний код бота (aiogram)
- `Dockerfile`
- `requirements.txt`

Удачі! 💪