# 📸 Media Organiser

A lightweight, local-first photo & video gallery for organising large dumps of personal media (vacation backups, GoPro/phone exports, etc.). Browse, tag, favourite, group into collections, and run LLM vision analyses — all backed by a local SQLite database. Nothing leaves your machine except the explicit LLM calls you trigger.

## What it does

- **Gallery view** — paginated thumbnail grid (300×300 cached JPEGs) with keyboard navigation, multi-select, and focus highlighting.
- **Single view** — full-size image / inline video player with a horizontal filmstrip for quick scrubbing.
- **Tags** — free-form, autocompleted from existing tags. Filter the gallery by one or many tags, or by "untagged".
- **Favourites** — toggle with `F`.
- **Collections** — named groups of images, separate from tags. Each collection can be exported (copy files) to any folder via a native picker.
- **LLM analyses** — per-image vision-LLM runs against named presets (Quick Overview, Detailed Analysis, IG captions in several tones, People Detection, Location & Scenery, Technical Analysis). Results are stored against the image with a timestamp and the preset name; multiple analyses per image are kept.
- **Batch operations** — select N images, then any action (tag, favourite, analyse, add-to-collection) applies to the whole selection.
- **Filtering** — Favourites / Analysed / Not Analysed / Untagged / Source-folder / Tags / Collection, combinable.
- **Multi-folder ingestion** — register any number of folders; the scanner walks them, deduplicates by path, and serves files via mounted media routes.

## Stack

- **Backend & UI:** [NiceGUI](https://nicegui.io) (FastAPI + Quasar/Vue under the hood) — single-process Python app, no separate frontend build.
- **Database:** SQLite (single file, `media.db`) — schema covers images, tags, image↔tag, collections, image↔collection, analyses, and scanned paths.
- **Imaging:** Pillow for thumbnail generation (cached on disk under `.thumb_cache/`) and base64 encoding for LLM payloads.
- **LLM:** Pluggable provider in `core/llm.py` — supports OpenAI vision and Google Gemini. Model is configured in `app.py` (`get_llm()`); presets and their temperatures live in `core/utils.py:ANALYSIS_PRESETS`.
- **Native file/folder pickers:** Tkinter dialogs (folder picker for scan paths and export destinations).

## Design

- **State lives in SQLite, not in memory.** Tags, favourites, collections, and analyses are written through `core/db.py` and re-read on demand. A single page-scoped `state` dict in `app.py` only holds transient UI state (current page, focus index, selection set, view mode, open panel).
- **Render-on-change.** Most interactions clear and rebuild the relevant NiceGUI container. Keyboard navigation inside a single page is the one exception: it patches the DOM directly via `ui.run_javascript()` to avoid re-rendering 80 cards on every arrow key.
- **Floating panels** for tag / analyse / collection pickers, each with a transparent backdrop that closes on click. Each panel supports search-as-you-type, keyboard navigation (↑↓/Enter/Esc), and inline creation ("press Enter to create new tag/collection").
- **LLM work is off the UI thread.** `run_analysis_for()` spawns a daemon thread per call, tracks in-flight presets per image in `state['running_analyses']`, and shows a spinner chip in the single view until the result is committed to the DB.
- **Thumbnails are served on demand** at `/thumb/{id}`, generated lazily and cached as JPEGs. Originals are served via per-folder mounted media routes so the gallery works across multiple disk locations without copying files.

## Layout

```
app.py                  # NiceGUI page, all UI + event wiring + HTTP endpoints
core/
  db.py                 # SQLite schema, queries, scanning
  llm.py                # Vision LLM client (OpenAI / Gemini)
  utils.py              # image helpers, format helpers, analysis presets
requirements.txt
.thumb_cache/           # auto-created
media.db                # auto-created
```

## Running

```bash
pip install -r requirements.txt
# Set OPENAI_API_KEY (or GOOGLE_API_KEY) in your environment for LLM analyses
python app.py
```

Then open http://localhost:8080. Use **+ Add Folder** to register a scan path; the gallery populates as soon as the scan completes.

## Keyboard shortcuts

| Key | Action |
|---|---|
| `←↑→↓` | Move focus (gallery) / prev–next (single view) |
| `Space` | Toggle selection on focused image |
| `Enter` | Open focused image in single view |
| `Ctrl+A` | Select all in current filter |
| `PgUp` / `PgDn` | Previous / next page |
| `Home` / `End` | First / last page |
| `F` | Toggle favourite |
| `T` | Open tag panel |
| `A` then `1`–`9` | Open analyse panel, then pick preset |
| `C` | Open collection panel |
| `G` / `S` | Switch to gallery / single view |
| `Esc` | Close panel / clear selection |

## Scope

Deliberately small. No auth, no multi-user, no cloud sync, no DAM-style ingest pipeline. It's a single-user local tool for "I have 2,000 photos from a trip and want to triage, label, and pick the keepers."
