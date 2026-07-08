<div align="center">

# 🎵 Yandex Music Downloader GUI

Простой и красивый загрузчик музыки из Яндекс.Музыки  
**для обычных пользователей**: вставили ссылку → выбрали папку → нажали **Скачать**

![Python](https://img.shields.io/badge/Python-3.9%2B-3776AB?style=for-the-badge&logo=python&logoColor=white)
![GUI](https://img.shields.io/badge/Interface-GUI-2563EB?style=for-the-badge)
![Windows](https://img.shields.io/badge/Windows-Supported-0078D6?style=for-the-badge&logo=windows&logoColor=white)
![Linux](https://img.shields.io/badge/Linux-Supported-FCC624?style=for-the-badge&logo=linux&logoColor=black)

</div>

---
<img width="899" height="654" alt="изображение" src="https://github.com/user-attachments/assets/5fe78202-49b7-4686-84f1-77d3b2bb9cfd" />


## ✨ Что умеет

- 🎧 Скачивание по ссылкам: артист / альбом / трек / плейлист / «Мне нравится»
- 🧾 Автопредпросмотр: программа показывает, что именно будет загружено
- 🎚️ Выбор качества: AAC или FLAC
- 📁 Выбор папки и кнопка **«Открыть папку»**
- ⚡ Многопоточная загрузка (1–4 потока)
- 🔐 Удобная работа с токеном (показать/скрыть, сохранение по галочке)

---

## 🚀 Быстрый старт

### 1) Установка

```bash
py -3.14 -m pip install -e .
```

Если `py` недоступен:

```bash
python -m pip install -e .
```

### 2) Запуск GUI

```bash
yandex-music-downloader-gui
```

### 3) 5 шагов в интерфейсе

1. Вставьте ссылку на Яндекс.Музыку
2. Укажите папку для файлов
3. Выберите качество и потоки
4. Вставьте токен (и включите галочку **Сохранить**, если нужно)
5. Нажмите **Скачать**

---

## 🔗 Поддерживаемые ссылки

```text
https://music.yandex.ru/artist/208167
https://music.yandex.ru/album/294912
https://music.yandex.ru/album/11644078/track/6705392
https://music.yandex.ru/users/<user>/playlists/3
https://music.yandex.ru/playlists/lk.<uuid>
https://music.yandex.ru/playlists/ik.<uuid>
```

---

## 🔑 Токен

Токен нужен для доступа к вашему аккаунту и разделу «Мне нравится».

Инструкция по получению токена:  
https://ym.marshal.dev/token/#implicit-oauth

Если включена галочка **Сохранить**, токен хранится локально:

- 🪟 Windows: `%APPDATA%\yandex-music-downloader\gui.json`
- 🐧 Linux: `~/.config/yandex-music-downloader/gui.json`

---

## ❓ Частые вопросы

<details>
<summary><b>Ссылка не распознана</b></summary>

- Проверьте, что это ссылка именно с `music.yandex.ru`
- Проверьте формат: артист / альбом / трек / плейлист
</details>

<details>
<summary><b>Не удалось получить данные</b></summary>

- Обычно причина в токене (истёк или неверный)
- Обновите токен и вставьте снова
</details>

<details>
<summary><b>Скачивание медленное</b></summary>

- Поставьте 2–4 потока
- Проверьте скорость сети/VPN/прокси
</details>

---

## 🛠️ Сборка `.exe` локально

```bash
py -3.14 -m pip install pyinstaller
py -3.14 -m PyInstaller --onefile --noconsole --clean --name ymd-gui --collect-all yandex_music ymd/gui.py
```

Результат: `dist/ymd-gui.exe`

---


## ⚠️ Важно

- Используйте программу в рамках правил сервиса
- Не публикуйте токен в открытом доступе

---

## 🙌 Благодарности

Этот GUI-проект основан на исходном проекте  
**`yandex-music-downloader` от `llistochek`**  
и использует  
**`yandex-music-api` от `MarshalX`**.

Спасибо авторам и контрибьюторам оригинального проекта.

---

## 📄 Лицензия

См. файл `LICENSE`.

