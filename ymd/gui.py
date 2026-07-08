import io
import json
import os
import shutil
import sys
import queue
import subprocess
import threading
import tkinter as tk
import urllib.request
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from tkinter import filedialog, messagebox
from urllib.parse import urlparse
import re
import itertools
from typing import Any, Iterable, Optional, Tuple, Union, cast

import customtkinter as ctk
from yandex_music import Playlist, Track

from ymd import core

_PIL_AVAILABLE = False
try:
    from PIL import Image as _PILImage  # type: ignore[import]
    _PIL_AVAILABLE = True
except ImportError:
    pass

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# ── Regex patterns ───────────────────────────────────────────────
TRACK_RE         = re.compile(r"track/(\d+)")
ALBUM_RE         = re.compile(r"album/(\d+)$")
ARTIST_RE        = re.compile(r"artist/(\d+)$")
PLAYLIST_RE      = re.compile(r"playlists/(.+)$")
PLAYLIST_LIKED_RE = re.compile(r"/playlists/((?:lk|ik)\.[\w-]+)$")

FETCH_PAGE_SIZE = 10

CONFIG_DIR_NAME = "yandex-music-downloader"
CONFIG_FILE_NAME = "gui.json"

GUI_VERSION = "1.1.0"
GUI_AUTHOR  = "atyonekilla"
CLI_AUTHOR  = "llistochek"

QUALITY_OPTIONS = [
    ("Лучшее (FLAC)",           2),
    ("Оптимальное (AAC 192kbps)", 1),
    ("Низкое (AAC 64kbps)",     0),
]
WORKERS_OPTIONS = [
    ("Обычная",                         1),
    ("Быстро",                          2),
    ("Очень быстро",                    3),
    ("Максимум (нагружает компьютер!)", 4),
]

# ── Color palette ────────────────────────────────────────────────
BG          = "#1a1b2e"
FRAME_BG    = "#1e1f38"
INPUT_BG    = "#252640"
INPUT_BDR   = "#3d3e60"
TEXT_CLR    = "#e8e9f3"
MUTED_CLR   = "#8b8ca8"
ACCENT_CLR  = "#4c8bf5"
BTN_BG      = "#2d2e50"
BTN_HOVER   = "#3a3d6b"
SEP_CLR     = "#2d2e60"
COVER_SIZE  = 200


# ── Entry keyboard shortcuts + context menu ──────────────────────
def _setup_entry(ctk_entry: ctk.CTkEntry) -> None:
    try:
        inner: tk.Entry = ctk_entry._entry  # type: ignore[attr-defined]
    except AttributeError:
        return

    def paste(*_) -> str:
        try:
            text = inner.clipboard_get()
        except tk.TclError:
            try:
                text = inner.selection_get(selection="PRIMARY")
            except tk.TclError:
                return "break"
        try:
            if inner.selection_present():
                inner.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        inner.insert(tk.INSERT, text)
        return "break"

    def copy(*_) -> str:
        try:
            text = inner.selection_get()
            if text:
                inner.clipboard_clear()
                inner.clipboard_append(text)
        except tk.TclError:
            pass
        return "break"

    def cut(*_) -> str:
        try:
            text = inner.selection_get()
            if text:
                inner.clipboard_clear()
                inner.clipboard_append(text)
                inner.delete("sel.first", "sel.last")
        except tk.TclError:
            pass
        return "break"

    def select_all(*_) -> str:
        inner.selection_range(0, tk.END)
        inner.icursor(tk.END)
        return "break"

    def ctrl_handler(event: tk.Event) -> Optional[str]:
        ks = (event.keysym or "").lower()
        ch = event.char or ""
        MAP: dict[str, Any] = {
            "v": paste, "c": copy, "x": cut, "a": select_all,
            "м": paste, "с": copy, "ч": cut, "ф": select_all,
            "cyrillic_em": paste, "cyrillic_es": copy,
            "cyrillic_che": cut, "cyrillic_ef": select_all,
        }
        CCHARS: dict[str, Any] = {
            "\x16": paste, "\x03": copy, "\x18": cut, "\x01": select_all,
        }
        fn = MAP.get(ks) or (MAP.get(ch.lower()) if ch else None) or CCHARS.get(ch)
        return fn() if fn else None

    menu = tk.Menu(inner, tearoff=0, bg="#2a2b45", fg=TEXT_CLR,
                   activebackground=ACCENT_CLR, activeforeground="white",
                   relief="flat", bd=0)
    menu.add_command(label="Вырезать",     command=cut)
    menu.add_command(label="Копировать",   command=copy)
    menu.add_command(label="Вставить",     command=paste)
    menu.add_separator()
    menu.add_command(label="Выделить всё", command=select_all)

    def show_menu(e: tk.Event) -> None:
        try:
            menu.tk_popup(e.x_root, e.y_root)
        finally:
            menu.grab_release()

    inner.bind("<Button-3>",        show_menu)
    inner.bind("<Control-v>",       paste)
    inner.bind("<Control-V>",       paste)
    inner.bind("<Shift-Insert>",    paste)
    inner.bind("<Control-c>",       copy)
    inner.bind("<Control-C>",       copy)
    inner.bind("<Control-x>",       cut)
    inner.bind("<Control-X>",       cut)
    inner.bind("<Control-a>",       select_all)
    inner.bind("<Control-A>",       select_all)
    inner.bind("<Control-KeyPress>", ctrl_handler)


# ── Config ───────────────────────────────────────────────────────
def _config_path() -> Path:
    if os.name == "nt":
        base = os.getenv("APPDATA") or os.path.expanduser("~")
        return Path(base) / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    xdg = os.getenv("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / CONFIG_DIR_NAME / CONFIG_FILE_NAME
    return Path.home() / ".config" / CONFIG_DIR_NAME / CONFIG_FILE_NAME


def _load_saved_token() -> str:
    path = _config_path()
    try:
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            token = data.get("token")
            if isinstance(token, str):
                return token
    except (OSError, json.JSONDecodeError):
        pass
    return ""


def _save_token(token: str) -> None:
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if token:
            path.write_text(json.dumps({"token": token}), encoding="utf-8")
        elif path.exists():
            path.unlink()
    except OSError:
        pass


# ── URL parsing ──────────────────────────────────────────────────
def _parse_url(url: str) -> Tuple[str, str]:
    path = urlparse(url).path
    if m := ARTIST_RE.search(path):
        return ("artist", m.group(1))
    if m := ALBUM_RE.search(path):
        return ("album", m.group(1))
    if m := TRACK_RE.search(path):
        return ("track", m.group(1))
    if m := PLAYLIST_LIKED_RE.search(path):
        return ("playlist", m.group(1))
    if m := PLAYLIST_RE.search(path):
        return ("playlist", m.group(1))
    raise ValueError("Ссылка не распознана: вставьте ссылку на артиста/альбом/трек/плейлист")


def _preview_fallback(target_type: str, target_id: str, token_missing: bool) -> str:
    labels = {"artist": "Артист", "album": "Альбом", "track": "Трек", "playlist": "Плейлист"}
    label = labels.get(target_type, "Ссылка")
    if target_type == "playlist" and target_id.startswith(("lk.", "ik.")):
        label = "Мне нравится"
    suffix = " Для названия нужен токен." if token_missing else ""
    return f"{label} (ID: {target_id}).{suffix}"


# ── Cover fetching ───────────────────────────────────────────────
def _get_cover_bytes(cover_uri: Optional[str], size: int = COVER_SIZE) -> Optional[bytes]:
    if not cover_uri:
        return None
    try:
        url = "https://" + cover_uri.replace("%%", f"{size}x{size}")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.read()
    except Exception:
        return None


def _get_preview_cover_uri(
    client: core.Client, target_type: str, target_id: str
) -> Optional[str]:
    try:
        if target_type == "artist":
            arts = client.artists(target_id)
            if arts and arts[0].cover:
                return arts[0].cover.uri
        elif target_type == "album":
            alb = client.albums_with_tracks(target_id)
            if alb:
                return alb.cover_uri
        elif target_type == "track":
            trs = client.tracks(target_id)
            if trs:
                return trs[0].cover_uri
        elif target_type == "playlist":
            if target_id.startswith(("lk.", "ik.")):
                return None
            if "/" in target_id:
                u, k = target_id.split("/")
                pl = client.users_playlists(k, u)
            else:
                pl = client.playlist(target_id)
            if pl and not isinstance(pl, list):
                pl_cover = cast(Playlist, pl).cover
                if pl_cover is not None:
                    return pl_cover.uri
    except Exception:
        pass
    return None


# ── Preview text ─────────────────────────────────────────────────
def _build_preview_text(client: core.Client, target_type: str, target_id: str) -> str:
    if target_type == "artist":
        arts = client.artists(target_id)
        if arts:
            name = arts[0].name or f"ID {target_id}"
            total = arts[0].counts.tracks if arts[0].counts else None
            return (f"Артист: {name}. Будет загружено треков: {total}."
                    if total else f"Артист: {name}. Будут загружены все доступные треки.")
        return f"Артист (ID: {target_id})."

    if target_type == "album":
        alb = client.albums_with_tracks(target_id)
        if alb:
            title = core.full_title(alb) or f"ID {target_id}"
            return (f"Альбом: {title}. Треков: {alb.track_count}."
                    if alb.track_count else f"Альбом: {title}. Будут загружены все доступные треки.")
        return f"Альбом (ID: {target_id})."

    if target_type == "track":
        trs = client.tracks(target_id)
        tr = trs[0] if trs else None
        if tr:
            title = core.full_title(tr) or f"ID {target_id}"
            artist = tr.artists[0].name if tr.artists else ""
            return f"Трек: {artist} — {title}." if artist else f"Трек: {title}."
        return f"Трек (ID: {target_id})."

    if target_type == "playlist":
        if target_id.startswith(("lk.", "ik.")):
            liked = client.users_likes_tracks()
            return f"Мне нравится. Треков: {len(liked.tracks) if liked else 0}."
        if "/" in target_id:
            user, kind = target_id.split("/")
            pl = client.users_playlists(kind, user)
            fb = f"{user}/{kind}"
        else:
            pl = client.playlist(target_id)
            fb = target_id
        if not pl or isinstance(pl, list):
            return f"Плейлист: {fb}."
        playlist = cast(Playlist, pl)
        title = playlist.title or fb
        return (f"Плейлист: {title}. Треков: {playlist.track_count}."
                if playlist.track_count else f"Плейлист: {title}. Будут загружены все доступные треки.")

    return f"Ссылка (ID: {target_id})."


# ── Preview worker ───────────────────────────────────────────────
def _preview_worker(
    token: str, url: str, version: int, queue_out: "queue.Queue[tuple]"
) -> None:
    try:
        if not url:
            queue_out.put(("preview", version, ""))
            queue_out.put(("cover",   version, None))
            return
        try:
            target_type, target_id = _parse_url(url)
        except ValueError as exc:
            queue_out.put(("preview", version, str(exc)))
            queue_out.put(("cover",   version, None))
            return
        if not token:
            queue_out.put(("preview", version, _preview_fallback(target_type, target_id, True)))
            queue_out.put(("cover",   version, None))
            return
        client = core.init_client(token=token, timeout=10, max_try_count=3, retry_delay=2)
        text = _build_preview_text(client, target_type, target_id)
        queue_out.put(("preview", version, text))
        cover_uri   = _get_preview_cover_uri(client, target_type, target_id)
        cover_bytes = _get_cover_bytes(cover_uri)
        queue_out.put(("cover", version, cover_bytes))
    except Exception as exc:
        queue_out.put(("preview", version, f"Не удалось получить данные: {exc}"))
        queue_out.put(("cover",   version, None))


# ── Track resolution ─────────────────────────────────────────────
def _album_tracks_gen(
    client: core.Client, album_ids: Iterable[Union[int, str]]
) -> Iterable[Track]:
    for album_id in album_ids:
        full_album = client.albums_with_tracks(album_id)
        if full_album and full_album.volumes:
            yield from itertools.chain.from_iterable(full_album.volumes)


def _resolve_tracks(
    client: core.Client, url: str
) -> Tuple[Iterable[Track], Optional[int]]:
    target_type, target_id = _parse_url(url)

    if target_type == "artist":
        album_ids: list[int] = []
        total = 0
        has_next, page = True, 0
        while has_next:
            info = client.artists_direct_albums(target_id, page)
            if not info:
                break
            for alb in info.albums:
                if alb.id and alb.available:
                    album_ids.append(alb.id)
                    if alb.track_count:
                        total += alb.track_count
            if pager := info.pager:
                page = pager.page + 1
                has_next = pager.per_page * page < pager.total
            else:
                break
        return (_album_tracks_gen(client, album_ids), total or None)

    if target_type == "album":
        alb = client.albums_with_tracks(target_id)
        return (_album_tracks_gen(client, (target_id,)), alb.track_count if alb else None)

    if target_type == "track":
        return (client.tracks(target_id), 1)

    if target_type == "playlist":
        if target_id.startswith(("lk.", "ik.")):
            liked = client.users_likes_tracks()
            ids = liked.tracks_ids if liked else []

            def liked_gen() -> Iterable[Track]:
                for i in range(0, len(ids), FETCH_PAGE_SIZE):
                    yield from client.tracks(ids[i: i + FETCH_PAGE_SIZE])

            return (liked_gen(), len(ids))

        if "/" in target_id:
            user, kind = target_id.split("/")
            pl = client.users_playlists(kind, user)
        else:
            pl = client.playlist(target_id)
        if not pl or isinstance(pl, list):
            raise ValueError("Плейлист не найден")
        playlist = cast(Playlist, pl)

        def pl_gen() -> Iterable[Track]:
            trs = playlist.fetch_tracks()
            for i in range(0, len(trs), FETCH_PAGE_SIZE):
                yield from client.tracks([t.id for t in trs[i: i + FETCH_PAGE_SIZE]])

        return (pl_gen(), playlist.track_count)

    raise ValueError("Неизвестный тип ссылки")


# ── ffmpeg resolver ──────────────────────────────────────────────
def _get_ffmpeg_exe() -> Optional[str]:
    """Returns path to ffmpeg: system PATH → imageio-ffmpeg bundled binary."""
    if exe := shutil.which("ffmpeg"):
        return exe
    try:
        import imageio_ffmpeg  # type: ignore[import]
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except ImportError:
        pass  # imageio-ffmpeg not installed
    except Exception:
        pass  # other error
    return None


def _ffmpeg_error_message() -> str:
    """Returns a user-friendly error message when ffmpeg is not available."""
    try:
        import imageio_ffmpeg  # type: ignore[import]  # noqa: F401
        return (
            "ffmpeg не найден и не удалось его загрузить.\n"
            "Попробуйте переустановить пакет:\n"
            "pip install --force-reinstall imageio-ffmpeg"
        )
    except ImportError:
        return (
            "Для конвертации необходим ffmpeg.\n\n"
            "Установите автоматически:\n"
            "pip install imageio-ffmpeg\n\n"
            "Или переустановите программу:\n"
            "pip install -e ."
        )


# ── MP3 conversion ───────────────────────────────────────────────
def _convert_to_mp3(m4a_path: Path) -> Path:
    ffmpeg_exe = _get_ffmpeg_exe()
    if ffmpeg_exe is None:
        raise RuntimeError("ffmpeg не найден и не удалось загрузить автоматически.")
    mp3_path = m4a_path.with_suffix(".mp3")
    result = subprocess.run(
        [ffmpeg_exe, "-y", "-i", str(m4a_path),
         "-c:a", "libmp3lame", "-q:a", "0", "-map_metadata", "0", str(mp3_path)],
        capture_output=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip().splitlines()[-1])
    m4a_path.unlink()
    return mp3_path


# ── Download worker ──────────────────────────────────────────────
def _download_worker(
    token: str, url: str, quality: int,
    download_dir: str, workers: int,
    queue_out: "queue.Queue[tuple]",
    convert_mp3: bool = False,
) -> None:
    try:
        client = core.init_client(token=token, timeout=20, max_try_count=20, retry_delay=5)
        tracks, total = _resolve_tracks(client, url)
        queue_out.put(("total", total))

        base_dir = Path(download_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        track_counter = 0
        completed = 0

        def build(track: Track) -> core.DownloadableTrack:
            sp = base_dir / core.prepare_base_path(core.DEFAULT_PATH_PATTERN, track, unsafe_path=False)
            sp.parent.mkdir(parents=True, exist_ok=True)
            return core.to_downloadable_track(track, core.CoreTrackQuality(quality), sp)

        def run(dl: core.DownloadableTrack) -> None:
            core.download_track(
                track_info=dl, lyrics_format=core.LyricsFormat.NONE,
                embed_cover=False, cover_resolution=core.DEFAULT_COVER_RESOLUTION,
                covers_cache=None, compatibility_level=1,
            )
            if convert_mp3 and dl.path.suffix == ".m4a":
                _convert_to_mp3(dl.path)

        if workers <= 1:
            for track in tracks:
                track_counter += 1
                ps = f"[{track_counter}/{total}] " if total else f"[{track_counter}] "
                if not track.available:
                    queue_out.put(("status", f"{ps}Трек {track.title} недоступен"))
                    completed += 1
                    queue_out.put(("progress", completed, total))
                    continue
                dl = build(track)
                queue_out.put(("status", f"{ps}Скачивается {dl.path}"))
                run(dl)
                completed += 1
                queue_out.put(("progress", completed, total))
        else:
            pending: set = set()
            with ThreadPoolExecutor(max_workers=workers) as ex:
                for track in tracks:
                    track_counter += 1
                    ps = f"[{track_counter}/{total}] " if total else f"[{track_counter}] "
                    if not track.available:
                        queue_out.put(("status", f"{ps}Трек {track.title} недоступен"))
                        completed += 1
                        queue_out.put(("progress", completed, total))
                        continue
                    dl = build(track)
                    queue_out.put(("status", f"{ps}Скачивается {dl.path}"))
                    pending.add(ex.submit(run, dl))
                    if len(pending) >= workers * 2:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for f in done:
                            f.result()
                            completed += 1
                            queue_out.put(("progress", completed, total))
                if pending:
                    done, _ = wait(pending)
                    for f in done:
                        f.result()
                        completed += 1
                        queue_out.put(("progress", completed, total))

        queue_out.put(("done", None))
    except Exception as exc:
        queue_out.put(("error", f"{type(exc).__name__}: {exc}"))


# ── Main application ─────────────────────────────────────────────
class DownloaderApp:
    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title(f"Yandex Music Downloader — version: {GUI_VERSION}")
        self.root.configure(fg_color=BG)
        self.root.minsize(900, 600)

        self.queue: "queue.Queue[tuple]" = queue.Queue()
        self.worker_thread: Optional[threading.Thread] = None
        self.progress_total: Optional[int] = None
        self.progress_running_indeterminate = False
        self.preview_after_id: Optional[str] = None
        self.preview_version = 0
        self.token_save_after_id: Optional[str] = None
        self._cover_image: Optional[Any] = None  # prevent GC

        self._init_fonts()
        self._build_ui()

        self.url_var.trace_add("write",        lambda *_: self._schedule_preview())
        self.token_var.trace_add("write",      lambda *_: self._schedule_preview())
        self.token_var.trace_add("write",      lambda *_: self._handle_token_change())
        self.save_token_var.trace_add("write", lambda *_: self._handle_save_toggle())
        self.show_token_var.trace_add("write", lambda *_: self._toggle_token())

        self.root.after(100, self._poll_queue)
        self.root.bind("<Configure>", self._on_resize)

    # ── Font init ────────────────────────────────────────────────
    def _init_fonts(self) -> None:
        import tkinter.font as tkfont
        available = set(tkfont.families())
        preferred = ["Nunito", "Inter", "Segoe UI Variable", "Segoe UI",
                     "Helvetica Neue", "Arial"]
        family = next((f for f in preferred if f in available), "Helvetica")

        from typing import Literal
        def F(size: int, weight: "Literal['normal','bold']" = "normal") -> ctk.CTkFont:
            return ctk.CTkFont(family=family, size=size, weight=weight)

        self.f_title    = F(22, "bold")    # главный заголовок
        self.f_subtitle = F(14)            # подзаголовок / версии
        self.f_label    = F(14)            # надписи над полями
        self.f_body     = F(16)            # тело (switches, checkbox)
        self.f_entry    = F(14)            # текст в полях ввода
        self.f_combo    = F(14)            # combobox
        self.f_btn      = F(14)            # вторичные кнопки
        self.f_btn_main = F(14, "bold")    # кнопка Скачать
        self.f_status_h = F(14, "bold")    # «Статус:»
        self.f_status   = F(14)            # текст статуса
        self.f_preview  = F(15, "bold")    # «Предпросмотр»
        self.f_cover    = F(12)            # плейсхолдер обложки
        self.f_preview_t = F(14)           # текст предпросмотра

    # ── UI builder ───────────────────────────────────────────────
    def _build_ui(self) -> None:
        # ── Header ──────────────────────────────────────────────
        hdr = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        hdr.pack(fill="x", padx=24, pady=(18, 0))

        title_frame = ctk.CTkFrame(hdr, fg_color=BG, corner_radius=0)
        title_frame.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(
            title_frame, text="Yandex Music Downloader",
            font=self.f_title, text_color=TEXT_CLR,
        ).pack(anchor="w")
        ctk.CTkLabel(
            title_frame,
            text=f"GUI Версия: {GUI_AUTHOR}  |  CLI Версия: {CLI_AUTHOR}",
            font=self.f_subtitle, text_color=MUTED_CLR,
        ).pack(anchor="w", pady=(2, 0))

        btn_frame = ctk.CTkFrame(hdr, fg_color=BG, corner_radius=0)
        btn_frame.pack(side="right", anchor="n", pady=(4, 0))
        self._sec_btn(btn_frame, "Настройки",    self._open_settings).pack(side="left", padx=(0, 8))
        self._sec_btn(btn_frame, "Благодарности", self._open_credits).pack(side="left")

        # ── Separator ────────────────────────────────────────────
        ctk.CTkFrame(self.root, height=1, fg_color=SEP_CLR, corner_radius=0).pack(
            fill="x", pady=(14, 0)
        )

        # ── Content ──────────────────────────────────────────────
        content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        content.pack(fill="both", expand=True)
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)
        content.grid_columnconfigure(1, weight=0, minsize=1)
        content.grid_columnconfigure(2, weight=0, minsize=300)

        left = ctk.CTkFrame(content, fg_color=BG, corner_radius=0)
        left.grid(row=0, column=0, sticky="nsew", padx=(24, 12), pady=16)

        ctk.CTkFrame(content, width=1, fg_color=SEP_CLR, corner_radius=0).grid(
            row=0, column=1, sticky="ns"
        )

        right = ctk.CTkFrame(content, fg_color=FRAME_BG, corner_radius=0, width=300)
        right.grid(row=0, column=2, sticky="nsew")
        right.grid_propagate(False)

        self._build_left(left)
        self._build_right(right)

    def _build_left(self, p: ctk.CTkFrame) -> None:
        def lbl(text: str) -> ctk.CTkLabel:
            return ctk.CTkLabel(p, text=text, font=self.f_label,
                                text_color=MUTED_CLR, anchor="w")

        # URL
        lbl("Ссылка").pack(fill="x", pady=(0, 4))
        self.url_var = tk.StringVar()
        self.url_entry = ctk.CTkEntry(
            p, textvariable=self.url_var,
            fg_color=INPUT_BG, border_color=INPUT_BDR,
            text_color=TEXT_CLR, corner_radius=8, font=self.f_entry,
        )
        self.url_entry.pack(fill="x", pady=(0, 12), ipady=2)
        _setup_entry(self.url_entry)

        # Token
        lbl("Токен").pack(fill="x", pady=(0, 4))
        saved_token = _load_saved_token()
        token_value = saved_token if saved_token else os.getenv("YANDEX_MUSIC_TOKEN", "")
        self.token_var = tk.StringVar(value=token_value)
        self.token_entry = ctk.CTkEntry(
            p, textvariable=self.token_var, show="*",
            fg_color=INPUT_BG, border_color=INPUT_BDR,
            text_color=TEXT_CLR, corner_radius=8, font=self.f_entry,
        )
        self.token_entry.pack(fill="x", pady=(0, 8), ipady=2)
        _setup_entry(self.token_entry)

        # Token switches
        sw_row = ctk.CTkFrame(p, fg_color=BG, corner_radius=0)
        sw_row.pack(fill="x", pady=(0, 12))

        self.save_token_var = tk.BooleanVar(value=bool(saved_token))
        self.save_token_switch = ctk.CTkSwitch(
            sw_row, text="Сохранить", variable=self.save_token_var,
            font=self.f_body, text_color=TEXT_CLR,
            fg_color=INPUT_BDR, progress_color=ACCENT_CLR,
        )
        self.save_token_switch.pack(side="left", padx=(0, 20))

        self.show_token_var = tk.BooleanVar(value=False)
        self.show_token_switch = ctk.CTkSwitch(
            sw_row, text="Показать", variable=self.show_token_var,
            font=self.f_body, text_color=TEXT_CLR,
            fg_color=INPUT_BDR, progress_color=ACCENT_CLR,
        )
        self.show_token_switch.pack(side="left")

        # Folder
        lbl("Куда сохранить:").pack(fill="x", pady=(0, 4))
        dir_row = ctk.CTkFrame(p, fg_color=BG, corner_radius=0)
        dir_row.pack(fill="x", pady=(0, 12))
        dir_row.columnconfigure(0, weight=1)

        self.dir_var = tk.StringVar(value=os.getcwd())
        self.dir_entry = ctk.CTkEntry(
            dir_row, textvariable=self.dir_var,
            fg_color=INPUT_BG, border_color=INPUT_BDR,
            text_color=TEXT_CLR, corner_radius=8, font=self.f_entry,
        )
        self.dir_entry.grid(row=0, column=0, sticky="ew", ipady=2)
        _setup_entry(self.dir_entry)
        self.dir_button = self._sec_btn(dir_row, "Обзор", self._choose_dir)
        self.dir_button.grid(row=0, column=1, sticky="ew", padx=(8, 0))

        # Quality + Workers
        qw = ctk.CTkFrame(p, fg_color=BG, corner_radius=0)
        qw.pack(fill="x", pady=(0, 12))
        qw.columnconfigure(0, weight=1)
        qw.columnconfigure(1, weight=1)

        q_col = ctk.CTkFrame(qw, fg_color=BG, corner_radius=0)
        q_col.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ctk.CTkLabel(q_col, text="Качество", font=self.f_label,
                     text_color=MUTED_CLR).pack(anchor="w", pady=(0, 4))
        self.quality_var = tk.StringVar(value=QUALITY_OPTIONS[0][0])
        self.quality_combo = ctk.CTkComboBox(
            q_col, variable=self.quality_var,
            values=[q for q, _ in QUALITY_OPTIONS],
            state="readonly", font=self.f_combo,
            fg_color=INPUT_BG, border_color=INPUT_BDR,
            button_color=BTN_BG, button_hover_color=BTN_HOVER,
            dropdown_fg_color=INPUT_BG,
            dropdown_text_color=TEXT_CLR,
            dropdown_hover_color=BTN_HOVER,
            text_color=TEXT_CLR,
        )
        self.quality_combo.pack(fill="x")

        w_col = ctk.CTkFrame(qw, fg_color=BG, corner_radius=0)
        w_col.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(w_col, text="Скорость скачивания", font=self.f_label,
                     text_color=MUTED_CLR).pack(anchor="w", pady=(0, 4))
        self.workers_var = tk.StringVar(value=WORKERS_OPTIONS[1][0])
        self.workers_combo = ctk.CTkComboBox(
            w_col, variable=self.workers_var,
            values=[label for label, _ in WORKERS_OPTIONS],
            state="readonly", font=self.f_combo,
            fg_color=INPUT_BG, border_color=INPUT_BDR,
            button_color=BTN_BG, button_hover_color=BTN_HOVER,
            dropdown_fg_color=INPUT_BG,
            dropdown_text_color=TEXT_CLR,
            dropdown_hover_color=BTN_HOVER,
            text_color=TEXT_CLR,
        )
        self.workers_combo.pack(fill="x")

        # Convert MP3
        self.convert_mp3_var = tk.BooleanVar(value=False)
        self.convert_mp3_check = ctk.CTkCheckBox(
            p, text="Конвертировать в MP3",
            variable=self.convert_mp3_var,
            font=self.f_body, text_color=TEXT_CLR,
            fg_color=ACCENT_CLR, hover_color="#3a7bd5",
            checkmark_color="white", border_color=INPUT_BDR,
        )
        self.convert_mp3_check.pack(anchor="w", pady=(0, 16))

        # Action buttons
        btn_row = ctk.CTkFrame(p, fg_color=BG, corner_radius=0)
        btn_row.pack(fill="x", pady=(0, 12))
        btn_row.columnconfigure(0, weight=1)

        self.download_button = ctk.CTkButton(
            btn_row, text="Скачать", command=self.start_download,
            fg_color=ACCENT_CLR, hover_color="#3a7bd5",
            text_color="white", font=self.f_btn_main,
            corner_radius=8, height=40,
        )
        self.download_button.grid(row=0, column=0, sticky="ew")

        self.open_folder_button = self._sec_btn(btn_row, "Открыть папку", self._open_folder)
        self.open_folder_button.grid(row=0, column=1, sticky="ew", padx=(10, 0))

        # Status
        ctk.CTkLabel(p, text="Статус:", font=self.f_status_h,
                     text_color=TEXT_CLR, anchor="w").pack(fill="x", pady=(8, 2))
        self.status_var = tk.StringVar(value="Готово")
        self.status_label = ctk.CTkLabel(
            p, textvariable=self.status_var,
            font=self.f_status, text_color=MUTED_CLR,
            wraplength=420, justify="left", anchor="w",
        )
        self.status_label.pack(fill="x", pady=(0, 8))

        # Progress
        self.progress = ctk.CTkProgressBar(
            p, fg_color=INPUT_BG, progress_color=ACCENT_CLR,
            corner_radius=4, height=6,
        )
        self.progress.set(0)
        self.progress.pack(fill="x")

    def _build_right(self, p: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            p, text="Предпросмотр",
            font=self.f_preview, text_color=TEXT_CLR,
        ).pack(pady=(20, 12))

        self.cover_label = ctk.CTkLabel(
            p, text="Обложка\nпоявится\nздесь",
            width=COVER_SIZE, height=COVER_SIZE,
            fg_color=INPUT_BG, corner_radius=8,
            text_color=MUTED_CLR, font=self.f_cover,
        )
        self.cover_label.pack(pady=(0, 14))

        self.preview_var = tk.StringVar(value="")
        self.preview_label = ctk.CTkLabel(
            p, textvariable=self.preview_var,
            font=self.f_preview_t, text_color=MUTED_CLR,
            wraplength=260, justify="center",
        )
        self.preview_label.pack(padx=16, fill="x")

    # ── Widget helpers ───────────────────────────────────────────
    def _sec_btn(self, parent: Any, text: str, cmd: Any) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, command=cmd,
            fg_color=BTN_BG, hover_color=BTN_HOVER,
            text_color=TEXT_CLR, font=self.f_btn,
            corner_radius=8, height=34,
        )

    # ── Cover ────────────────────────────────────────────────────
    def _update_cover(self, img_bytes: Optional[bytes]) -> None:
        if not img_bytes:
            self.cover_label.configure(image=None, text="Обложка\nпоявится\nздесь")
            self._cover_image = None
            return
        if not _PIL_AVAILABLE:
            self.cover_label.configure(image=None,
                                        text="Установите Pillow\nдля отображения\nобложки")
            return
        try:
            from PIL import Image as _Img  # type: ignore[import]
            resample = getattr(getattr(_Img, "Resampling", _Img), "LANCZOS", 1)
            pil_img = _Img.open(io.BytesIO(img_bytes)).resize((COVER_SIZE, COVER_SIZE), resample)
            self._cover_image = ctk.CTkImage(
                light_image=pil_img, dark_image=pil_img,
                size=(COVER_SIZE, COVER_SIZE),
            )
            self.cover_label.configure(image=self._cover_image, text="")
        except Exception:
            self.cover_label.configure(image=None, text="Обложка\nпоявится\nздесь")
            self._cover_image = None

    # ── Resize ───────────────────────────────────────────────────
    def _on_resize(self, event: tk.Event) -> None:
        if event.widget is not self.root:
            return
        try:
            w = max(300, event.width - 380)
            self.status_label.configure(wraplength=w)
        except Exception:
            pass

    # ── Token ────────────────────────────────────────────────────
    def _toggle_token(self) -> None:
        self.token_entry.configure(show="" if self.show_token_var.get() else "*")

    def _schedule_token_save(self) -> None:
        if self.token_save_after_id:
            self.root.after_cancel(self.token_save_after_id)
            self.token_save_after_id = None
        self.token_save_after_id = self.root.after(500, self._persist_token)

    def _persist_token(self) -> None:
        self.token_save_after_id = None
        _save_token(self.token_var.get().strip() if self.save_token_var.get() else "")

    def _handle_save_toggle(self) -> None:
        if self.save_token_var.get():
            self._schedule_token_save()
        else:
            _save_token("")

    def _handle_token_change(self) -> None:
        if self.save_token_var.get():
            self._schedule_token_save()

    # ── Preview ──────────────────────────────────────────────────
    def _schedule_preview(self) -> None:
        if self.preview_after_id:
            self.root.after_cancel(self.preview_after_id)
            self.preview_after_id = None
        self.preview_after_id = self.root.after(600, self._start_preview)

    def _start_preview(self) -> None:
        self.preview_after_id = None
        url   = self.url_var.get().strip()
        token = self.token_var.get().strip()
        if not url:
            self.preview_var.set("")
            self.cover_label.configure(image=None, text="Обложка\nпоявится\nздесь")
            self._cover_image = None
            return
        self.preview_var.set("Проверяю ссылку...")
        self.preview_version += 1
        version = self.preview_version
        threading.Thread(
            target=_preview_worker,
            args=(token, url, version, self.queue),
            daemon=True,
        ).start()

    # ── Folders ──────────────────────────────────────────────────
    def _choose_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.dir_var.get().strip() or os.getcwd())
        if d:
            self.dir_var.set(d)

    def _open_folder(self) -> None:
        folder = Path(self.dir_var.get().strip() or os.getcwd())
        if not folder.exists():
            messagebox.showerror("Папка не найдена", "Указанная папка не существует.")
            return
        try:
            if os.name == "nt":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(folder)], check=False)
            else:
                subprocess.run(["xdg-open", str(folder)], check=False)
        except Exception as exc:
            messagebox.showerror("Ошибка", f"Не удалось открыть папку: {exc}")

    # ── Dialogs ──────────────────────────────────────────────────
    def _open_settings(self) -> None:
        messagebox.showinfo("Настройки", "Настройки будут добавлены в следующих версиях.")

    def _open_credits(self) -> None:
        messagebox.showinfo(
            "Благодарности",
            f"GUI версия: {GUI_VERSION}\nАвтор GUI: {GUI_AUTHOR}\n\n"
            "Основан на yandex-music-downloader от llistochek\n"
            "API: yandex-music-api от MarshalX",
        )

    # ── Running state ────────────────────────────────────────────
    def _set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        for w in (
            self.download_button, self.open_folder_button,
            self.url_entry, self.token_entry,
            self.dir_entry, self.dir_button,
            self.save_token_switch, self.show_token_switch,
            self.convert_mp3_check,
        ):
            w.configure(state=state)
        self.quality_combo.configure(state="disabled" if running else "readonly")
        self.workers_combo.configure(state="disabled" if running else "readonly")

    # ── Download ─────────────────────────────────────────────────
    def start_download(self) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            return

        token        = self.token_var.get().strip()
        url          = self.url_var.get().strip()
        download_dir = self.dir_var.get().strip()

        if not token:
            messagebox.showerror("Нужен токен",
                                 "Введите токен или установите YANDEX_MUSIC_TOKEN.")
            return
        if not url:
            messagebox.showerror("Нужна ссылка", "Введите ссылку на Яндекс.Музыку.")
            return
        if not download_dir:
            messagebox.showerror("Нужна папка", "Укажите папку для скачивания.")
            return
        dl_path = Path(download_dir)
        if dl_path.exists() and not dl_path.is_dir():
            messagebox.showerror("Нужна папка", "Указанный путь не является папкой.")
            return

        workers = dict(WORKERS_OPTIONS).get(self.workers_var.get())
        if workers is None:
            messagebox.showerror("Неверное значение", "Выберите скорость скачивания из списка.")
            return

        quality     = dict(QUALITY_OPTIONS).get(self.quality_var.get(), 2)
        convert_mp3 = self.convert_mp3_var.get()

        if convert_mp3 and _get_ffmpeg_exe() is None:
            messagebox.showerror("ffmpeg не найден", _ffmpeg_error_message())
            return

        self.status_var.set("Подготовка...")
        self.progress.set(0)
        self.progress.configure(mode="determinate")
        self.progress_total = None
        self.progress_running_indeterminate = False

        self._set_running(True)
        self.worker_thread = threading.Thread(
            target=_download_worker,
            args=(token, url, quality, download_dir, workers, self.queue, convert_mp3),
            daemon=True,
        )
        self.worker_thread.start()
        self.root.after(100, self._poll_queue)

    # ── Queue ────────────────────────────────────────────────────
    def _poll_queue(self) -> None:
        try:
            while True:
                self._handle_queue_item(self.queue.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def _handle_queue_item(self, item: tuple) -> None:
        event = item[0]
        if event == "total":
            self.progress_total = item[1]
            if self.progress_total:
                self.progress.configure(mode="determinate")
                self.progress.set(0)
            else:
                self.progress.configure(mode="indeterminate")
                self.progress.start()
                self.progress_running_indeterminate = True
        elif event == "status":
            self.status_var.set(item[1])
        elif event == "preview":
            version, text = item[1], item[2]
            if version == self.preview_version:
                self.preview_var.set(text)
        elif event == "cover":
            version, img_bytes = item[1], item[2]
            if version == self.preview_version:
                self._update_cover(img_bytes)
        elif event == "progress":
            current, total = item[1], item[2]
            if total:
                self.progress.set(current / total)
            elif not self.progress_running_indeterminate:
                self.progress.configure(mode="indeterminate")
                self.progress.start()
                self.progress_running_indeterminate = True
        elif event == "error":
            if self.progress_running_indeterminate:
                self.progress.stop()
            self.progress.set(0)
            self.status_var.set("Ошибка")
            self._set_running(False)
            messagebox.showerror("Ошибка", item[1])
        elif event == "done":
            if self.progress_running_indeterminate:
                self.progress.stop()
                self.progress_running_indeterminate = False
            self.progress.set(1)
            self.status_var.set("Готово")
            self._set_running(False)


def main() -> None:
    root = ctk.CTk()
    app = DownloaderApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
