import os
import shutil
import threading
import tkinter as tk
from tkinter import filedialog
from io import BytesIO
from PIL import Image
from fastapi.responses import Response
from nicegui import ui, app
from core import db, utils
from core.llm import LLM
from core.utils import ANALYSIS_PRESETS

THUMB_CACHE = os.path.join(os.path.dirname(__file__), '.thumb_cache')
os.makedirs(THUMB_CACHE, exist_ok=True)

PRESET_KEYS = {str(i + 1): name for i, name in enumerate(ANALYSIS_PRESETS.keys())}
GALLERY_COLS = 5
GALLERY_PAGE_SIZE = 80

state = {
    'images': [],
    'current_index': 0,
    'focused_index': 0,
    'gallery_page': 0,        # current page (0-based)
    'view': 'gallery',
    'filters': {},
    'selected': set(),
    'llm': None,
    'media_routes': {},
    'analyze_mode': False,
    'tag_mode': False,
    'collection_mode': False,
    'running_analyses': {},
    '_refresh_cb': None,
}

def get_llm():
    if state['llm'] is None:
        state['llm'] = LLM(model='gpt-4o-mini')
    return state['llm']

def reload_images():
    state['images'] = db.get_all_images(state['filters'] or None)

def current_img():
    if not state['images']:
        return None
    idx = max(0, min(state['current_index'], len(state['images']) - 1))
    return state['images'][idx]

def focused_img():
    if not state['images']:
        return None
    idx = max(0, min(state['focused_index'], len(state['images']) - 1))
    return state['images'][idx]

def action_targets():
    if state['selected']:
        return list(state['selected'])
    img = focused_img() if state['view'] == 'gallery' else current_img()
    return [img['id']] if img else []

def page_count():
    total = len(state['images'])
    return max(1, (total + GALLERY_PAGE_SIZE - 1) // GALLERY_PAGE_SIZE)

def page_images():
    p = state['gallery_page']
    start = p * GALLERY_PAGE_SIZE
    return state['images'][start:start + GALLERY_PAGE_SIZE], start

def open_folder_picker():
    result = {'path': None}
    def _pick():
        root = tk.Tk()
        root.withdraw()
        root.wm_attributes('-topmost', True)
        result['path'] = filedialog.askdirectory(title='Select Photo Folder')
        root.destroy()
    t = threading.Thread(target=_pick)
    t.start()
    t.join()
    return result['path']

def register_media_folder(folder_path):
    if folder_path in state['media_routes']:
        return state['media_routes'][folder_path]
    slug = f'media_{len(state["media_routes"])}'
    url_prefix = f'/{slug}'
    app.add_media_files(url_prefix, folder_path)
    state['media_routes'][folder_path] = url_prefix
    return url_prefix

def get_image_url(img):
    folder = os.path.dirname(img['path'])
    prefix = register_media_folder(folder)
    return f"{prefix}/{img['filename']}"

def run_analysis_for(img_id: int, preset_name: str):
    img_data = db.get_image_by_id(img_id)
    if not img_data or not utils.is_image(img_data['filename']):
        ui.notify('LLM analysis only works with images', type='warning')
        return
    running = state['running_analyses'].setdefault(img_id, set())
    if preset_name in running:
        return
    running.add(preset_name)
    cb = state.get('_refresh_cb')
    if cb:
        cb()

    def worker():
        try:
            llm = get_llm()
            b64 = utils.get_image_base64(img_data['path'])
            if b64:
                prompt, temp = utils.get_analysis_prompt(preset_name)
                result, _ = llm.visionchat(b64, prompt, img_type='base64', temperature=temp)
                db.add_analysis(img_id, preset_name, result)
                ui.notify(f'✅ Done: {preset_name}', type='positive')
            else:
                ui.notify('Failed to read image', type='negative')
        except Exception as e:
            ui.notify(f'Analysis error: {e}', type='negative')
        finally:
            state['running_analyses'].get(img_id, set()).discard(preset_name)
            cb = state.get('_refresh_cb')
            if cb:
                cb()

    threading.Thread(target=worker, daemon=True).start()

@app.get('/thumb/{img_id}')
def serve_thumb(img_id: int):
    cached = os.path.join(THUMB_CACHE, f'{img_id}.jpg')
    if not os.path.exists(cached):
        img_data = db.get_image_by_id(img_id)
        if not img_data or not utils.is_image(img_data['filename']):
            return Response(status_code=404)
        try:
            im = Image.open(img_data['path'])
            im.thumbnail((300, 300))
            im = im.convert('RGB')
            buf = BytesIO()
            im.save(buf, 'JPEG', quality=82)
            with open(cached, 'wb') as f:
                f.write(buf.getvalue())
        except Exception:
            return Response(status_code=500)
    with open(cached, 'rb') as f:
        return Response(f.read(), media_type='image/jpeg')

@app.post('/delete_analysis')
def delete_analysis_endpoint(id: int):
    db.delete_analysis(id)
    return {'ok': True}

@app.get('/navigate')
def navigate_endpoint(idx: int):
    state['current_index'] = idx
    return {'ok': True}

@app.post('/clear_selection')
def clear_selection_endpoint():
    state['selected'].clear()
    return {'ok': True}

db.init_db()
reload_images()

for folder in db.get_scanned_paths():
    if os.path.isdir(folder):
        register_media_folder(folder)

@ui.page('/')
def main_page():
    ui.add_body_html("""
<script>
function navigateTo(idx) {
    fetch('/navigate?idx=' + idx).then(() => location.reload());
}
function deleteAnalysis(id) {
    fetch('/delete_analysis?id=' + id, {method:'POST'}).then(() => location.reload());
}
document.addEventListener('keydown', function(e) {
    var tag = document.activeElement ? document.activeElement.tagName : '';
    if (['INPUT','TEXTAREA','SELECT'].indexOf(tag) !== -1) return;
    if (['ArrowUp','ArrowDown','ArrowLeft','ArrowRight',' '].indexOf(e.key) !== -1) {
        e.preventDefault();
    }
}, {passive: false});

// Fast DOM-only focus/select update — avoids full Python re-render
function galleryMoveFocus(oldIdx, newIdx) {
    var old = document.querySelector('[data-gidx="' + oldIdx + '"]');
    var nw  = document.querySelector('[data-gidx="' + newIdx + '"]');
    if (old) old.classList.remove('focused');
    if (nw)  { nw.classList.add('focused'); nw.scrollIntoView({block:'nearest',inline:'nearest'}); }
}
function galleryToggleSelect(idx, imgId, selected) {
    var card = document.querySelector('[data-gidx="' + idx + '"]');
    if (!card) return;
    if (selected) {
        card.style.background = '#1e3a5f';
        card.style.border = '2px solid #42a5f5';
        var badge = card.querySelector('.sel-badge');
        if (!badge) {
            badge = document.createElement('div');
            badge.className = 'sel-badge';
            badge.textContent = '✓';
            card.appendChild(badge);
        }
    } else {
        card.style.background = '#1f2937';
        card.style.border = '2px solid #2d3748';
        var badge = card.querySelector('.sel-badge');
        if (badge) badge.remove();
    }
}
function updateSelIndicator(n) {
    var el = document.getElementById('sel-indicator-label');
    var btn = document.getElementById('sel-indicator-clear');
    if (!el) return;
    if (n > 0) {
        el.textContent = '✓ ' + n + ' selected';
        el.style.display = 'inline-flex';
        if (btn) btn.style.display = 'inline-flex';
    } else {
        el.style.display = 'none';
        if (btn) btn.style.display = 'none';
    }
}
</script>
""")

    ui.add_head_html("""
<style>
  body { font-size: 13px !important; }
  .q-toolbar { min-height: 44px !important; padding: 0 8px !important; }
  .q-drawer { font-size: 12px !important; }
  .q-item { min-height: 28px !important; padding: 2px 8px !important; }
  .q-item__label { font-size: 12px !important; }
  .nicegui-content { padding: 4px 8px !important; }

  #filmstrip {
    position: fixed; bottom: 0; left: 0; right: 0;
    height: 90px; background: rgba(20,20,20,0.95);
    display: flex; align-items: center; gap: 4px;
    padding: 4px 8px; z-index: 9999;
    overflow-x: auto; scrollbar-width: thin;
  }
  #filmstrip .thumb {
    flex-shrink: 0; width: 72px; height: 72px;
    object-fit: cover; border-radius: 4px; cursor: pointer;
    border: 2px solid transparent; transition: border-color 0.15s;
  }
  #filmstrip .thumb:hover { border-color: #90caf9; }
  #filmstrip .thumb.active { border-color: #42a5f5; box-shadow: 0 0 6px #42a5f5; }
  #filmstrip .thumb-video {
    flex-shrink: 0; width: 72px; height: 72px; background: #333;
    border-radius: 4px; cursor: pointer; border: 2px solid transparent;
    display: flex; align-items: center; justify-content: center;
    font-size: 24px; color: #aaa;
  }
  #filmstrip .thumb-video.active { border-color: #42a5f5; }
  #filmstrip .nav-btn {
    flex-shrink: 0; background: #444; color: white; border: none;
    border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 18px;
  }
  #filmstrip .nav-btn:hover { background: #666; }

  .single-view-wrap { padding-bottom: 100px; }
  .gallery-wrap { padding-bottom: 100px; }

  .tag-chip-row {
    display: inline-flex; align-items: center; gap: 2px;
    background: #1565c0; color: white;
    border-radius: 12px; padding: 1px 2px 1px 8px;
    font-size: 11px; margin: 2px;
  }
  .tag-chip-row .remove-btn {
    opacity: 0; transition: opacity 0.15s;
    display: inline-flex; align-items: center;
  }
  .tag-chip-row:hover .remove-btn { opacity: 1; }

  .coll-chip-row {
    display: inline-flex; align-items: center; gap: 2px;
    background: #065f46; color: #6ee7b7;
    border-radius: 12px; padding: 1px 2px 1px 8px;
    font-size: 11px; margin: 2px;
  }
  .coll-chip-row .remove-btn {
    opacity: 0; transition: opacity 0.15s;
    display: inline-flex; align-items: center;
  }
  .coll-chip-row:hover .remove-btn { opacity: 1; }

  .kbd {
    display: inline-block; background: #333; color: #eee;
    border: 1px solid #555; border-radius: 3px;
    padding: 0 4px; font-size: 11px; font-family: monospace;
  }

  .analyses-table {
    width: 100%; border-collapse: collapse; font-size: 11px; margin-top: 6px;
  }
  .analyses-table th {
    background: #1f2937; color: #9ca3af; padding: 4px 8px;
    text-align: left; font-weight: 600; border-bottom: 1px solid #374151;
  }
  .analyses-table td {
    padding: 4px 8px; border-bottom: 1px solid #1f2937;
    color: #d1d5db; vertical-align: top;
  }
  .analyses-table tr:hover td { background: #1f2937; }
  .analyses-table .result-cell {
    max-width: 400px; white-space: nowrap;
    overflow: hidden; text-overflow: ellipsis; cursor: default;
  }
  .analyses-table .result-cell:hover {
    white-space: normal; overflow: visible; background: #111827;
    position: relative; z-index: 10;
    box-shadow: 0 4px 12px rgba(0,0,0,0.5);
    border-radius: 4px; padding: 6px 8px;
  }

  .analysis-spinner {
    display: inline-flex; align-items: center; gap: 4px;
    background: #065f46; color: #6ee7b7;
    border-radius: 12px; padding: 2px 8px; font-size: 11px; margin: 2px;
  }

  /* Gallery cards */
  .gallery-card {
    cursor: pointer; position: relative;
    background: #1f2937; border: 2px solid #2d3748;
    border-radius: 4px; overflow: hidden;
    transition: border-color 0.1s;
  }
  .gallery-card:hover { border-color: #4b5563 !important; }
  .gallery-card.focused { outline: 2px dashed #60a5fa !important; outline-offset: 1px; }
  .gallery-card .sel-badge {
    position: absolute; top: 4px; right: 4px;
    background: #2563eb; color: white; border-radius: 50%;
    width: 18px; height: 18px; font-size: 11px;
    display: flex; align-items: center; justify-content: center;
    font-weight: bold; z-index: 2;
  }
  .gallery-card .card-thumb {
    width: 100%; height: 120px; object-fit: cover; display: block;
  }
  .gallery-card .card-video-icon {
    width: 100%; height: 120px; display: flex; align-items: center;
    justify-content: center; font-size: 40px; color: #9ca3af; background: #111;
  }
  .gallery-card .card-info {
    padding: 2px 4px; font-size: 10px; color: #9ca3af;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
    display: flex; align-items: center; gap: 3px;
  }
  .gallery-card .card-tags {
    padding: 0 4px 2px; display: flex; flex-wrap: wrap; gap: 2px;
  }
  .gallery-card .card-tag {
    background: #1d4ed8; color: #bfdbfe; border-radius: 8px;
    padding: 0 5px; font-size: 9px;
  }

  /* Floating panels */
  .float-panel {
    position: fixed; top: 50px; right: 16px; z-index: 8000;
    background: #1f2937; border: 1px solid #374151;
    border-radius: 8px; min-width: 260px; padding: 10px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.6);
  }
  .panel-backdrop {
    position: fixed; inset: 0; z-index: 7999;
    background: transparent; cursor: default;
  }

  .tag-row {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 6px; cursor: pointer; border-radius: 4px;
    font-size: 11px; color: #d1d5db;
  }
  .tag-row:hover { background: #374151; }
  .tag-row.hi { background: #374151; }

  /* Pagination bar */
  .page-bar {
    display: flex; align-items: center; gap: 8px;
    padding: 4px 0 8px; font-size: 11px; color: #9ca3af;
  }
  .page-bar button {
    background: #374151; color: #d1d5db; border: none;
    border-radius: 4px; padding: 2px 8px; cursor: pointer; font-size: 11px;
  }
  .page-bar button:hover { background: #4b5563; }
  .page-bar button:disabled { opacity: 0.4; cursor: default; }
  .page-bar .page-info { color: #60a5fa; font-weight: bold; }
</style>
""")
    reload_images()

    # ── Left Drawer ────────────────────────────────────────────────────────────
    with ui.left_drawer(fixed=True).style('width:220px; padding:6px; background:#111827;'):
        ui.label('📸 Photo Gallery').style('font-weight:bold; font-size:14px; color:#90caf9; padding:4px 0;')

        stats = db.get_stats()
        with ui.card().style('background:#1f2937; padding:6px; margin-bottom:6px; width:100%;'):
            ui.label(f"📷 {stats['total_images']} photos").style('font-size:11px; color:#9ca3af;')
            ui.label(f"⭐ {stats['favorites']} favorites").style('font-size:11px; color:#9ca3af;')
            ui.label(f"🤖 {stats['analyzed']} analyzed").style('font-size:11px; color:#9ca3af;')
            ui.label(f"🏷️ {stats['total_tags']} tags").style('font-size:11px; color:#9ca3af;')

        ui.separator().style('margin:4px 0;')
        ui.label('📁 Scan Paths').style('font-size:12px; color:#d1d5db; font-weight:bold;')
        paths_container = ui.column().style('width:100%; gap:2px;')

        def refresh_paths():
            paths_container.clear()
            with paths_container:
                for p in db.get_scanned_paths():
                    with ui.row().style('align-items:center; gap:4px; width:100%;'):
                        ui.label(os.path.basename(p)).style(
                            'font-size:10px; color:#9ca3af; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;'
                        ).tooltip(p)
                        ui.button(icon='close', on_click=lambda _, path=p: remove_path(path)).props('flat dense size=xs color=red')

        def remove_path(path):
            db.remove_scanned_path(path)
            refresh_paths()

        def pick_and_scan():
            folder = open_folder_picker()
            if folder:
                db.add_scanned_path(folder)
                register_media_folder(folder)
                refresh_paths()
                with ui.dialog() as scanning_dialog, ui.card():
                    ui.label(f'Scanning {os.path.basename(folder)}...').style('font-size:13px;')
                scanning_dialog.open()
                added = db.scan_and_add_images(folder)
                scanning_dialog.close()
                reload_images()
                ui.notify(f'Added {added} new photos from {os.path.basename(folder)}', type='positive')
                ui.navigate.reload()

        ui.button('+ Add Folder', on_click=pick_and_scan).props('flat dense color=blue size=sm').style('width:100%; margin-top:4px;')
        refresh_paths()

        ui.separator().style('margin:4px 0;')
        ui.label('🔍 Filters').style('font-size:12px; color:#d1d5db; font-weight:bold;')

        filter_fav = ui.checkbox('⭐ Favorites').style('font-size:11px;')
        filter_analyzed = ui.checkbox('🤖 Analyzed').style('font-size:11px;')
        filter_not_analyzed = ui.checkbox('❌ Not Analyzed').style('font-size:11px;')
        filter_not_tagged = ui.checkbox('🏷️ Untagged').style('font-size:11px;')

        all_folders = ['All'] + db.get_source_folders()
        folder_select = ui.select(all_folders, value='All', label='Folder').style('font-size:11px; width:100%;').props('dense outlined')

        all_tags = db.get_all_tags()
        tag_filter = ui.select(all_tags, multiple=True, label='Tags').style('font-size:11px; width:100%;').props('dense outlined use-chips')

        def apply_filters():
            f = {}
            if filter_fav.value:
                f['favorite_only'] = True
            if filter_analyzed.value:
                f['analyzed_only'] = True
            if filter_not_analyzed.value:
                f['not_analyzed'] = True
            if filter_not_tagged.value:
                f['not_tagged'] = True
            if folder_select.value and folder_select.value != 'All':
                f['source_folder'] = folder_select.value
            if tag_filter.value:
                f['tags'] = tag_filter.value
            state['filters'] = f
            state['current_index'] = 0
            state['focused_index'] = 0
            state['gallery_page'] = 0
            reload_images()
            ui.navigate.reload()

        ui.button('Apply Filters', on_click=apply_filters).props('flat dense color=blue size=sm').style('width:100%; margin-top:4px;')

        def clear_filters():
            state['filters'] = {}
            state['gallery_page'] = 0
            reload_images()
            ui.navigate.reload()

        ui.button('Clear Filters', on_click=clear_filters).props('flat dense color=grey size=sm').style('width:100%;')

        ui.separator().style('margin:4px 0;')

        def open_manage_tags_dialog():
            with ui.dialog() as dlg, ui.card().style('min-width:320px; max-height:70vh; overflow-y:auto;'):
                ui.label('🏷️ Manage Tags').style('font-weight:bold; font-size:14px;')
                ui.label('Deleting a tag removes it from all images.').style('font-size:11px; color:#9ca3af;')
                ui.separator()
                tag_list_container = ui.column().style('width:100%; gap:2px;')

                def refresh_tag_list():
                    tag_list_container.clear()
                    with tag_list_container:
                        for t in db.get_tag_usage_counts():
                            with ui.row().style('align-items:center; gap:8px; padding:2px 0;'):
                                ui.label(t['name']).style('flex:1; font-size:12px;')
                                ui.label(f"{t['count']} images").style('font-size:10px; color:#9ca3af;')
                                def make_delete(name=t['name']):
                                    def do_delete():
                                        db.delete_tag(name)
                                        reload_images()
                                        refresh_tag_list()
                                        ui.notify(f'Deleted tag: {name}', type='positive')
                                    return do_delete
                                ui.button(icon='delete', on_click=make_delete()).props('flat dense size=xs color=red').tooltip('Delete tag globally')

                refresh_tag_list()
                with ui.row().style('justify-content:flex-end; margin-top:8px;'):
                    ui.button('Close', on_click=dlg.close).props('flat dense')
            dlg.open()

        ui.button('🏷️ Manage Tags', on_click=open_manage_tags_dialog).props('flat dense color=grey size=sm').style('width:100%;')

        # ── Collections sidebar ────────────────────────────────────────────────
        ui.separator().style('margin:4px 0;')
        ui.label('📦 Collections').style('font-size:12px; color:#d1d5db; font-weight:bold;')
        coll_list_container = ui.column().style('width:100%; gap:2px;')

        def refresh_coll_list():
            coll_list_container.clear()
            with coll_list_container:
                for c in db.list_collections():
                    with ui.row().style('align-items:center; gap:2px; width:100%; padding:1px 0;'):
                        def make_filter_coll(cid=c['id'], cname=c['name']):
                            def do_filter():
                                state['filters'] = {'collection_id': cid}
                                state['gallery_page'] = 0
                                state['current_index'] = 0
                                state['focused_index'] = 0
                                reload_images()
                                ui.navigate.reload()
                            return do_filter
                        ui.button(
                            f"{c['name']} ({c['count']})",
                            on_click=make_filter_coll()
                        ).props('flat dense color=teal size=sm').style('flex:1; text-align:left; font-size:10px; overflow:hidden;')

                        def make_export(cid=c['id'], cname=c['name']):
                            def do_export():
                                export_collection(cid, cname)
                            return do_export

                        def make_del_coll(cid=c['id'], cname=c['name']):
                            def do_del():
                                db.delete_collection(cid)
                                refresh_coll_list()
                                ui.notify(f'Deleted collection: {cname}', type='positive')
                            return do_del

                        ui.button(icon='download', on_click=make_export()).props('flat dense size=xs color=green').tooltip('Export collection')
                        ui.button(icon='delete', on_click=make_del_coll()).props('flat dense size=xs color=red').tooltip('Delete collection')

        refresh_coll_list()

        def select_all_action():
            state['selected'] = {img['id'] for img in state['images']}
            render_content()

        ui.button('Select All', on_click=select_all_action).props('flat dense color=blue size=sm').style('width:100%; margin-top:4px;')

        ui.separator().style('margin:4px 0;')
        ui.label('⌨️ Shortcuts').style('font-size:12px; color:#d1d5db; font-weight:bold;')
        for key, action in [
            ('←↑→↓', 'Move focus'), ('Space', 'Select/deselect'),
            ('Enter', 'Open image'), ('Ctrl+A', 'Select all'),
            ('PgDn/PgUp', 'Next/prev page'), ('Home/End', 'First/last page'),
            ('F', 'Favorite'), ('T', 'Tag'), ('A then 1-9', 'Analyze'),
            ('C', 'Collection'), ('G', 'Gallery'), ('S', 'Single'), ('Esc', 'Cancel/clear'),
        ]:
            with ui.row().style('gap:4px; align-items:center;'):
                ui.html(f'<span class="kbd">{key}</span>')
                ui.label(action).style('font-size:11px; color:#9ca3af;')

    # ── Header ─────────────────────────────────────────────────────────────────
    with ui.header().style('background:#0f172a; padding:4px 12px; min-height:40px; align-items:center;'):
        with ui.row().style('align-items:center; gap:8px; width:100%;'):
            ui.button(icon='photo_library', on_click=lambda: switch_view('gallery')).props('flat color=white dense').tooltip('Gallery (G)')
            ui.button(icon='photo', on_click=lambda: switch_view('single')).props('flat color=white dense').tooltip('Single View (S)')
            ui.separator().props('vertical color=white').style('height:20px; opacity:0.3;')
            ui.label('Sar Pass Trek').style('color:#90caf9; font-weight:bold; font-size:14px;')
            ui.space()
            # Selection indicator — NiceGUI components so Python can update them directly
            sel_label = ui.label('').style(
                'display:none; background:#1e3a5f; color:#90caf9; '
                'border-radius:12px; padding:2px 10px; font-size:11px;'
            )
            sel_clear_btn = ui.button('✕ clear').props('flat dense size=sm color=grey').style('display:none; font-size:11px;')

    def update_sel_indicator():
        n = len(state['selected'])
        if n > 0:
            sel_label.set_text(f'✓ {n} selected')
            sel_label.style('display:inline-flex; background:#1e3a5f; color:#90caf9; border-radius:12px; padding:2px 10px; font-size:11px;')
            sel_clear_btn.style('display:inline-flex; font-size:11px;')
        else:
            sel_label.style('display:none;')
            sel_clear_btn.style('display:none;')

    def clear_selection_action():
        page_start = state['gallery_page'] * GALLERY_PAGE_SIZE
        for img_id in list(state['selected']):
            img_local_idx = next(
                (i for i, img in enumerate(page_images()[0]) if img['id'] == img_id),
                None
            )
            if img_local_idx is not None:
                ui.run_javascript(f'galleryToggleSelect({img_local_idx},{img_id},false)')
        state['selected'].clear()
        update_sel_indicator()

    sel_clear_btn.on('click', clear_selection_action)

    content_area = ui.column().style('width:100%; padding:4px;')
    filmstrip_html = ui.html('').style('position:fixed; bottom:0; left:0; right:0; z-index:9999;')

    # ── Shared backdrop ────────────────────────────────────────────────────────
    backdrop = ui.element('div').classes('panel-backdrop').style('display:none;')

    def show_backdrop(on_click_fn):
        backdrop.style('display:block;')
        backdrop.on('click', on_click_fn)

    def hide_backdrop():
        backdrop.style('display:none;')

    # ── Analyze panel ──────────────────────────────────────────────────────────
    analyze_panel = ui.element('div').classes('float-panel').style('display:none;')

    def show_analyze_panel():
        analyze_panel.style('display:block;')
        show_backdrop(close_analyze)

    def close_analyze():
        analyze_panel.style('display:none;')
        hide_backdrop()
        state['analyze_mode'] = False

    with analyze_panel:
        ui.label('🤖 Analyze — pick a preset').style('color:#90caf9; font-weight:bold; font-size:12px; margin-bottom:4px;')
        for key, name in PRESET_KEYS.items():
            def make_analyze_handler(n=name):
                def handler():
                    close_analyze()
                    for img_id in action_targets():
                        run_analysis_for(img_id, n)
                return handler
            with ui.row().style('align-items:center; gap:8px; padding:2px 0; cursor:pointer;').on('click', make_analyze_handler()):
                ui.html(f'<span class="kbd" style="color:#fbbf24;">{key}</span>')
                ui.label(name).style('font-size:11px; color:#d1d5db;')
        ui.label('Esc / click outside to cancel').style('font-size:10px; color:#6b7280; margin-top:6px;')

    # ── Tag panel ──────────────────────────────────────────────────────────────
    tag_panel = ui.element('div').classes('float-panel').style('display:none; min-width:280px;')
    tp = {'query': '', 'highlight': 0}

    def show_tag_panel_ui():
        tag_panel.style('display:block; min-width:280px;')
        show_backdrop(close_tag_panel)

    def close_tag_panel():
        tag_panel.style('display:none; min-width:280px;')
        hide_backdrop()
        tp['query'] = ''
        tp['highlight'] = 0
        state['tag_mode'] = False

    def filtered_tags():
        q = tp['query'].strip().lower()
        all_t = db.get_all_tags()
        return all_t if not q else [t for t in all_t if q in t.lower()]

    tag_list_col = None

    def rebuild_tag_list():
        if tag_list_col is None:
            return
        tag_list_col.clear()
        targets = action_targets()
        first_img = db.get_image_by_id(targets[0]) if targets else None
        applied = set(first_img['tags']) if first_img else set()
        tags = filtered_tags()
        hi = max(0, min(tp['highlight'], len(tags) - 1)) if tags else 0
        tp['highlight'] = hi

        with tag_list_col:
            if not tags and tp['query']:
                ui.label(f'↵ to create "{tp["query"]}"').style('font-size:11px; color:#fbbf24; padding:4px 6px;')
                return
            for i, tag in enumerate(tags):
                is_applied = tag in applied
                row_classes = 'tag-row hi' if i == hi else 'tag-row'

                def make_click(t=tag):
                    def handler():
                        for img_id in action_targets():
                            img2 = db.get_image_by_id(img_id)
                            if img2:
                                if t in img2['tags']:
                                    db.remove_tag_from_image(img_id, t)
                                else:
                                    db.add_tag_to_image(img_id, t)
                        reload_images()
                        tp['query'] = ''
                        tp['highlight'] = 0
                        tag_input_el.set_value('')
                        rebuild_tag_list()
                        cb = state.get('_refresh_cb')
                        if cb:
                            cb()
                    return handler

                with ui.element('div').classes(row_classes).on('click', make_click(tag)):
                    ui.label('✓' if is_applied else '').style(
                        f'width:14px; font-size:12px; {"color:#34d399;" if is_applied else "color:transparent;"}'
                    )
                    ui.label(tag).style('font-size:11px; color:#d1d5db; flex:1;')

    def commit_tag():
        tags = filtered_tags()
        hi = tp['highlight']
        tag_to_apply = None
        if tags and hi < len(tags):
            tag_to_apply = tags[hi]
        elif tp['query'].strip():
            tag_to_apply = tp['query'].strip()
        if not tag_to_apply:
            return
        for img_id in action_targets():
            img2 = db.get_image_by_id(img_id)
            if img2:
                if tag_to_apply in img2['tags']:
                    db.remove_tag_from_image(img_id, tag_to_apply)
                else:
                    db.add_tag_to_image(img_id, tag_to_apply)
        reload_images()
        tag_filter.set_options(db.get_all_tags())
        tp['query'] = ''
        tp['highlight'] = 0
        tag_input_el.set_value('')
        rebuild_tag_list()
        cb = state.get('_refresh_cb')
        if cb:
            cb()

    with tag_panel:
        ui.label('🏷️ Tags').style('color:#90caf9; font-weight:bold; font-size:12px; margin-bottom:4px;')

        def on_tag_change(e):
            tp['query'] = e.value if e.value else ''
            tp['highlight'] = 0
            rebuild_tag_list()

        tag_input_el = ui.input(
            placeholder='Filter or type new tag…',
            on_change=on_tag_change
        ).props('dense outlined').style('width:100%; margin-bottom:4px;')

        tag_input_el.on('keydown.down.prevent', lambda _: (tp.update({'highlight': min(tp['highlight'] + 1, max(0, len(filtered_tags()) - 1))}), rebuild_tag_list()))
        tag_input_el.on('keydown.up.prevent',   lambda _: (tp.update({'highlight': max(0, tp['highlight'] - 1)}), rebuild_tag_list()))
        tag_input_el.on('keydown.enter',         lambda _: commit_tag())
        tag_input_el.on('keydown.esc',           lambda _: close_tag_panel())

        tag_list_col = ui.column().style('width:100%; gap:1px; max-height:220px; overflow-y:auto;')
        ui.label('↑↓ navigate · Enter add/toggle · Esc close').style('font-size:10px; color:#6b7280; margin-top:4px;')

    # ── Collection panel ───────────────────────────────────────────────────────
    coll_panel = ui.element('div').classes('float-panel').style('display:none; min-width:280px;')
    cp = {'query': '', 'highlight': 0}

    def show_coll_panel():
        coll_panel.style('display:block; min-width:280px;')
        show_backdrop(close_coll_panel)

    def close_coll_panel():
        coll_panel.style('display:none; min-width:280px;')
        hide_backdrop()
        cp['query'] = ''
        cp['highlight'] = 0
        state['collection_mode'] = False

    def filtered_colls():
        q = cp['query'].strip().lower()
        all_c = db.list_collections()
        return all_c if not q else [c for c in all_c if q in c['name'].lower()]

    coll_list_panel_col = None

    def rebuild_coll_list_panel():
        if coll_list_panel_col is None:
            return
        coll_list_panel_col.clear()
        targets = action_targets()
        first_img_colls = {c['id'] for c in db.get_image_collections(targets[0])} if targets else set()
        colls = filtered_colls()
        hi = max(0, min(cp['highlight'], len(colls) - 1)) if colls else 0
        cp['highlight'] = hi

        with coll_list_panel_col:
            if not colls and cp['query']:
                ui.label(f'↵ to create "{cp["query"]}"').style('font-size:11px; color:#fbbf24; padding:4px 6px;')
                return
            for i, c in enumerate(colls):
                is_in = c['id'] in first_img_colls
                row_classes = 'tag-row hi' if i == hi else 'tag-row'

                def make_coll_click(cid=c['id'], cname=c['name']):
                    def handler():
                        for img_id in action_targets():
                            img_colls = {col['id'] for col in db.get_image_collections(img_id)}
                            if cid in img_colls:
                                db.remove_from_collection(cid, img_id)
                            else:
                                db.add_to_collection(cid, img_id)
                        cp['query'] = ''
                        cp['highlight'] = 0
                        coll_input_el.set_value('')
                        rebuild_coll_list_panel()
                        refresh_coll_list()
                        cb = state.get('_refresh_cb')
                        if cb:
                            cb()
                    return handler

                with ui.element('div').classes(row_classes).on('click', make_coll_click(c['id'], c['name'])):
                    ui.label('✓' if is_in else '').style(
                        f'width:14px; font-size:12px; {"color:#34d399;" if is_in else "color:transparent;"}'
                    )
                    ui.label(c['name']).style('font-size:11px; color:#d1d5db; flex:1;')
                    ui.label(f"({c['count']})").style('font-size:10px; color:#6b7280;')

    def commit_coll():
        colls = filtered_colls()
        hi = cp['highlight']
        if colls and hi < len(colls):
            cid = colls[hi]['id']
            for img_id in action_targets():
                img_colls = {c['id'] for c in db.get_image_collections(img_id)}
                if cid in img_colls:
                    db.remove_from_collection(cid, img_id)
                else:
                    db.add_to_collection(cid, img_id)
        elif cp['query'].strip():
            new_name = cp['query'].strip()
            cid = db.create_collection(new_name)
            for img_id in action_targets():
                db.add_to_collection(cid, img_id)
            ui.notify(f'Created collection: {new_name}', type='positive')
        cp['query'] = ''
        cp['highlight'] = 0
        coll_input_el.set_value('')
        rebuild_coll_list_panel()
        refresh_coll_list()
        cb = state.get('_refresh_cb')
        if cb:
            cb()

    with coll_panel:
        ui.label('📦 Collections').style('color:#90caf9; font-weight:bold; font-size:12px; margin-bottom:4px;')

        def on_coll_change(e):
            cp['query'] = e.value if e.value else ''
            cp['highlight'] = 0
            rebuild_coll_list_panel()

        coll_input_el = ui.input(
            placeholder='Filter or type new collection…',
            on_change=on_coll_change
        ).props('dense outlined').style('width:100%; margin-bottom:4px;')

        coll_input_el.on('keydown.down.prevent', lambda _: (cp.update({'highlight': min(cp['highlight'] + 1, max(0, len(filtered_colls()) - 1))}), rebuild_coll_list_panel()))
        coll_input_el.on('keydown.up.prevent',   lambda _: (cp.update({'highlight': max(0, cp['highlight'] - 1)}), rebuild_coll_list_panel()))
        coll_input_el.on('keydown.enter',         lambda _: commit_coll())
        coll_input_el.on('keydown.esc',           lambda _: close_coll_panel())

        coll_list_panel_col = ui.column().style('width:100%; gap:1px; max-height:220px; overflow-y:auto;')
        ui.label('↑↓ navigate · Enter add/toggle · Esc close').style('font-size:10px; color:#6b7280; margin-top:4px;')

    # ── Export collection ──────────────────────────────────────────────────────
    def export_collection(collection_id: int, collection_name: str):
        dest = open_folder_picker()
        if not dest:
            return
        items = db.get_collection_image_paths(collection_id)
        if not items:
            ui.notify('Collection is empty', type='warning')
            return

        with ui.dialog() as progress_dlg, ui.card().style('min-width:340px;'):
            ui.label(f'Exporting "{collection_name}"…').style('font-weight:bold;')
            progress = ui.linear_progress(value=0).style('width:100%;')
            status_label = ui.label('Starting…').style('font-size:12px; color:#9ca3af;')
        progress_dlg.open()

        def do_export():
            copied, skipped, errors = [], [], []
            total = len(items)
            for i, item in enumerate(items):
                dst = os.path.join(dest, item['filename'])
                status_label.set_text(f'{i+1}/{total}: {item["filename"]}')
                try:
                    if os.path.exists(dst):
                        skipped.append(item['filename'])
                    else:
                        shutil.copy2(item['path'], dst)
                        copied.append(item['filename'])
                except Exception as e:
                    errors.append(f'{item["filename"]}: {e}')
                progress.set_value((i + 1) / total)

            progress_dlg.close()

            with ui.dialog() as result_dlg, ui.card().style('min-width:360px; max-height:70vh; overflow-y:auto;'):
                ui.label(f'Export complete — "{collection_name}"').style('font-weight:bold; font-size:14px;')
                ui.label(f'Destination: {dest}').style('font-size:11px; color:#9ca3af; margin-bottom:6px;')
                ui.label(f'✅ Copied: {len(copied)}').style('color:#34d399; font-size:12px;')
                if skipped:
                    ui.label(f'⚠️ Skipped (already exist): {len(skipped)}').style('color:#fbbf24; font-size:12px; margin-top:4px;')
                    with ui.scroll_area().style('max-height:120px; width:100%;'):
                        for f in skipped:
                            ui.label(f'  • {f}').style('font-size:11px; color:#9ca3af;')
                if errors:
                    ui.label(f'❌ Errors: {len(errors)}').style('color:#ef4444; font-size:12px; margin-top:4px;')
                    with ui.scroll_area().style('max-height:120px; width:100%;'):
                        for e in errors:
                            ui.label(f'  • {e}').style('font-size:11px; color:#9ca3af;')
                with ui.row().style('justify-content:flex-end; margin-top:8px;'):
                    ui.button('Close', on_click=result_dlg.close).props('flat dense')
            result_dlg.open()

        threading.Thread(target=do_export, daemon=True).start()

    # ── Panel toggle helpers (page-scoped so gallery keys can call them) ───────
    def toggle_tags():
        if state['tag_mode']:
            close_tag_panel()
        else:
            state['tag_mode'] = True
            state['analyze_mode'] = False
            state['collection_mode'] = False
            close_analyze()
            close_coll_panel()
            tp['query'] = ''
            tp['highlight'] = 0
            tag_input_el.set_value('')
            rebuild_tag_list()
            show_tag_panel_ui()
            tag_input_el.run_method('focus')

    def toggle_analyze():
        if state['analyze_mode']:
            close_analyze()
        else:
            state['analyze_mode'] = True
            state['tag_mode'] = False
            state['collection_mode'] = False
            close_tag_panel()
            close_coll_panel()
            show_analyze_panel()

    def toggle_coll():
        if state['collection_mode']:
            close_coll_panel()
        else:
            state['collection_mode'] = True
            state['analyze_mode'] = False
            state['tag_mode'] = False
            close_analyze()
            close_tag_panel()
            cp['query'] = ''
            cp['highlight'] = 0
            coll_input_el.set_value('')
            rebuild_coll_list_panel()
            show_coll_panel()
            coll_input_el.run_method('focus')

    # ── View switching ─────────────────────────────────────────────────────────
    def switch_view(view):
        state['view'] = view
        state['analyze_mode'] = False
        state['tag_mode'] = False
        state['collection_mode'] = False
        close_analyze()
        close_tag_panel()
        close_coll_panel()
        render_content()

    def refresh_view():
        reload_images()
        render_content()

    state['_refresh_cb'] = refresh_view

    def render_content():
        content_area.clear()
        with content_area:
            if state['view'] == 'gallery':
                render_gallery()
            else:
                render_single()
        update_filmstrip()

    # ── Gallery ────────────────────────────────────────────────────────────────
    def render_gallery():
        images = state['images']
        total = len(images)
        if not total:
            ui.label('No photos found. Add a folder to scan.').style('color:#9ca3af; padding:20px;')
            return

        pc = page_count()
        p = state['gallery_page']
        visible, page_start = page_images()
        page_end = page_start + len(visible)

        # Pagination bar
        with ui.element('div').classes('page-bar'):
            def go_prev():
                state['gallery_page'] = max(0, p - 1)
                state['focused_index'] = state['gallery_page'] * GALLERY_PAGE_SIZE
                render_content()
            def go_next():
                state['gallery_page'] = min(pc - 1, p + 1)
                state['focused_index'] = state['gallery_page'] * GALLERY_PAGE_SIZE
                render_content()
            def go_first():
                state['gallery_page'] = 0
                state['focused_index'] = 0
                render_content()
            def go_last():
                state['gallery_page'] = pc - 1
                state['focused_index'] = state['gallery_page'] * GALLERY_PAGE_SIZE
                render_content()

            ui.html(f'<button onclick="" id="pgbtn-first" {"disabled" if p == 0 else ""}>⏮</button>').on('click', go_first)
            ui.html(f'<button id="pgbtn-prev" {"disabled" if p == 0 else ""}>◀</button>').on('click', go_prev)
            ui.html(f'<span class="page-info">Page {p+1} / {pc}</span>')
            ui.html(f'<span>· {page_start+1}–{page_end} of {total}</span>')
            ui.html(f'<button id="pgbtn-next" {"disabled" if p >= pc-1 else ""}>▶</button>').on('click', go_next)
            ui.html(f'<button id="pgbtn-last" {"disabled" if p >= pc-1 else ""}>⏭</button>').on('click', go_last)

        with ui.element('div').classes('gallery-wrap').style('width:100%;'):
            with ui.grid(columns=GALLERY_COLS).style('gap:4px; width:100%;'):
                for local_i, img in enumerate(visible):
                    global_i = page_start + local_i
                    render_gallery_card(global_i, local_i, img)

    def render_gallery_card(global_idx: int, local_idx: int, img):
        is_selected = img['id'] in state['selected']
        is_focused = global_idx == state['focused_index']
        has_analyses = bool(db.get_analyses_for_image(img['id']))

        def open_single(gi=global_idx):
            state['current_index'] = gi
            state['focused_index'] = gi
            switch_view('single')

        def on_click(e, gi=global_idx, img_id=img['id']):
            args = e.args if isinstance(e.args, dict) else {}
            shift = args.get('shiftKey', False)
            ctrl = args.get('ctrlKey', False) or args.get('metaKey', False)
            if shift or ctrl:
                if img_id in state['selected']:
                    state['selected'].discard(img_id)
                    now_sel = False
                else:
                    state['selected'].add(img_id)
                    now_sel = True
                state['focused_index'] = gi
                ui.run_javascript(f'galleryToggleSelect({local_idx},{img_id},{str(now_sel).lower()})')
                update_sel_indicator()
            else:
                open_single(gi)

        border = '2px solid #42a5f5' if is_selected else '2px solid #2d3748'
        bg = '#1e3a5f' if is_selected else '#1f2937'
        focused_cls = ' focused' if is_focused else ''

        # Build card as a single html block for minimal component count
        name = img['filename']
        short_name = (name[:18] + '…') if len(name) > 18 else name
        icons = ''
        if img['is_favorite']:
            icons += '⭐'
        if has_analyses:
            icons += '🤖'
        tags_html = ''
        for tag in img['tags'][:3]:
            tags_html += f'<span class="card-tag">{tag}</span>'

        if utils.is_video(img['filename']):
            media_html = '<div class="card-video-icon">🎥</div>'
        else:
            media_html = f'<img class="card-thumb" src="/thumb/{img["id"]}" loading="lazy">'

        sel_badge = '<div class="sel-badge">✓</div>' if is_selected else ''

        card_html = f'''{sel_badge}{media_html}
<div class="card-info">{icons} {short_name}</div>
<div class="card-tags">{tags_html}</div>'''

        ui.html(f'<div class="gallery-card{focused_cls}" data-gidx="{local_idx}" data-imgid="{img["id"]}" '
                f'style="background:{bg}; border:{border};">{card_html}</div>').on(
            'click', on_click, args=['shiftKey', 'ctrlKey', 'metaKey']
        )

    # ── Single view ────────────────────────────────────────────────────────────
    def render_single():
        img = current_img()
        if not img:
            ui.label('No images loaded.').style('color:#9ca3af; padding:20px;')
            return

        def toggle_favorite_action():
            db.toggle_favorite(img['id'])
            reload_images()
            render_content()

        def remove_tag_action(tag):
            db.remove_tag_from_image(img['id'], tag)
            reload_images()
            render_content()

        def remove_from_coll_action(cid):
            db.remove_from_collection(cid, img['id'])
            refresh_coll_list()
            render_content()

        def toggle_current_select():
            if img['id'] in state['selected']:
                state['selected'].discard(img['id'])
            else:
                state['selected'].add(img['id'])
            render_content()

        with ui.element('div').classes('single-view-wrap').style('width:100%;'):
            with ui.element('div').style(
                'background:#1a1a2e; border-radius:6px; padding:6px 12px; '
                'display:flex; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:4px;'
            ):
                ui.label(img['filename']).style('color:#e2e8f0; font-weight:bold; font-size:13px;')
                ui.label(f"📁 {img['source_folder']}").style('color:#9ca3af; font-size:11px;')
                ui.label(utils.format_file_size(img['file_size'])).style('color:#9ca3af; font-size:11px;')
                ui.space()

                running = state['running_analyses'].get(img['id'], set())
                for preset in running:
                    ui.html(f'<span class="analysis-spinner">⏳ {preset}</span>')

                ui.button(
                    icon='star' if img['is_favorite'] else 'star_border',
                    on_click=toggle_favorite_action
                ).props(f'flat dense {"color=amber" if img["is_favorite"] else "color=grey"}').tooltip('Favorite (F)')

                ui.button(icon='label', on_click=toggle_tags).props('flat dense color=blue').tooltip('Tags (T)')
                ui.button(icon='smart_toy', on_click=toggle_analyze).props('flat dense color=green').tooltip('Analyze (A then 1-9)')
                ui.button(icon='collections_bookmark', on_click=toggle_coll).props('flat dense color=teal').tooltip('Collection (C)')

                is_sel = img['id'] in state['selected']
                ui.button(
                    icon='check_box' if is_sel else 'check_box_outline_blank',
                    on_click=toggle_current_select
                ).props('flat dense color=blue').tooltip('Select (Space)')

            with ui.element('div').style(
                'display:flex; justify-content:center; align-items:center; '
                'width:100%; max-height:calc(100vh - 240px); overflow:hidden;'
            ):
                if utils.is_video(img['path']):
                    ui.video(get_image_url(img)).style('max-width:100%; max-height:calc(100vh - 240px);')
                else:
                    ui.image(get_image_url(img)).style('max-width:100%; max-height:calc(100vh - 240px); object-fit:contain;')

            chips_row = ui.row().style('flex-wrap:wrap; gap:4px; margin-top:4px;')
            with chips_row:
                for tag in img['tags']:
                    with ui.row().classes('tag-chip-row'):
                        ui.label(tag).style('color:white; font-size:11px;')
                        ui.button(
                            icon='close',
                            on_click=lambda _, t=tag: remove_tag_action(t)
                        ).classes('remove-btn').props('flat dense size=xs round color=white').style('font-size:10px; padding:0;')

                for coll in db.get_image_collections(img['id']):
                    with ui.row().classes('coll-chip-row'):
                        ui.label(f"📦 {coll['name']}").style('font-size:11px;')
                        ui.button(
                            icon='close',
                            on_click=lambda _, cid=coll['id']: remove_from_coll_action(cid)
                        ).classes('remove-btn').props('flat dense size=xs round color=white').style('font-size:10px; padding:0;')

            analyses = db.get_analyses_for_image(img['id'])
            if analyses:
                rows_html = ''
                for a in analyses:
                    ts = a['created_at'][:16] if a['created_at'] else ''
                    r = a['result'].replace('&', '&amp;').replace('<', '&lt;').replace('"', '&quot;')
                    rows_html += f"""<tr>
                      <td style="white-space:nowrap;color:#90caf9;">{a['preset_name']}</td>
                      <td class="result-cell">{r}</td>
                      <td style="white-space:nowrap;color:#6b7280;font-size:10px;">{ts}</td>
                      <td><button onclick="deleteAnalysis({a['id']})"
                          style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:12px;">✕</button></td>
                    </tr>"""
                ui.html(f"""<div style="margin-top:8px;">
                  <table class="analyses-table">
                    <thead><tr><th>Preset</th><th>Result (hover to expand)</th><th>When</th><th></th></tr></thead>
                    <tbody>{rows_html}</tbody>
                  </table></div>""")

    # ── Filmstrip ──────────────────────────────────────────────────────────────
    def update_filmstrip():
        if state['view'] != 'single' or not state['images']:
            filmstrip_html.set_content('')
            return

        idx = state['current_index']
        images = state['images']
        total = len(images)
        half = 6
        start = max(0, idx - half)
        end = min(total, start + 13)
        start = max(0, end - 13)

        thumbs_html = ''
        for i in range(start, end):
            img_t = images[i]
            active_class = 'active' if i == idx else ''
            fname = img_t['filename'].replace("'", "")
            if utils.is_video(img_t['filename']):
                thumbs_html += f'<div class="thumb-video {active_class}" onclick="navigateTo({i})" title="{fname}">🎥</div>'
            else:
                thumbs_html += f'<img class="thumb {active_class}" src="/thumb/{img_t["id"]}" onclick="navigateTo({i})" title="{fname}" loading="lazy">'

        prev_idx = (idx - 1) % total
        next_idx = (idx + 1) % total

        filmstrip_html.set_content(f"""
        <div id="filmstrip">
            <button class="nav-btn" onclick="navigateTo({prev_idx})">&#8249;</button>
            {thumbs_html}
            <button class="nav-btn" onclick="navigateTo({next_idx})">&#8250;</button>
            <span style="color:#9ca3af;font-size:11px;margin-left:8px;white-space:nowrap;">{idx+1} / {total}</span>
        </div>
        """)

    # ── Keyboard shortcuts ─────────────────────────────────────────────────────
    def handle_key(e):
        if not e.action.keydown:
            return
        key = e.key

        if state['tag_mode']:
            if key == 'Escape':
                close_tag_panel()
            return

        if state['collection_mode']:
            if key == 'Escape':
                close_coll_panel()
            return

        if state['analyze_mode']:
            if key == 'Escape':
                close_analyze()
            else:
                for k, name in PRESET_KEYS.items():
                    if key == k:
                        close_analyze()
                        for img_id in action_targets():
                            run_analysis_for(img_id, name)
                        break
            return

        # ── Gallery keyboard nav ───────────────────────────────────────────────
        if state['view'] == 'gallery':
            total = len(state['images'])
            if total == 0:
                return
            fi = state['focused_index']
            pc = page_count()
            p = state['gallery_page']
            page_start = p * GALLERY_PAGE_SIZE
            page_end = min(total, page_start + GALLERY_PAGE_SIZE) - 1

            def move_focus(new_fi):
                old_local = fi - page_start
                new_page = new_fi // GALLERY_PAGE_SIZE
                state['focused_index'] = new_fi
                if new_page != p:
                    state['gallery_page'] = new_page
                    render_content()
                    # After render, scroll focused card into view (handles both directions)
                    ui.run_javascript(
                        "document.querySelector('.gallery-card.focused')"
                        "?.scrollIntoView({block:'nearest',inline:'nearest'})"
                    )
                else:
                    new_local = new_fi - page_start
                    ui.run_javascript(f'galleryMoveFocus({old_local},{new_local})')

            if key == 'ArrowRight':
                move_focus(min(fi + 1, total - 1))
            elif key == 'ArrowLeft':
                move_focus(max(fi - 1, 0))
            elif key == 'ArrowDown':
                move_focus(min(fi + GALLERY_COLS, total - 1))
            elif key == 'ArrowUp':
                move_focus(max(fi - GALLERY_COLS, 0))
            elif key == 'PageDown':
                new_p = min(pc - 1, p + 1)
                state['gallery_page'] = new_p
                state['focused_index'] = new_p * GALLERY_PAGE_SIZE
                render_content()
            elif key == 'PageUp':
                new_p = max(0, p - 1)
                state['gallery_page'] = new_p
                state['focused_index'] = new_p * GALLERY_PAGE_SIZE
                render_content()
            elif key == 'Home':
                state['gallery_page'] = 0
                state['focused_index'] = 0
                render_content()
            elif key == 'End':
                state['gallery_page'] = pc - 1
                state['focused_index'] = (pc - 1) * GALLERY_PAGE_SIZE
                render_content()
            elif key == ' ':
                img = focused_img()
                if img:
                    local_idx = fi - page_start
                    if img['id'] in state['selected']:
                        state['selected'].discard(img['id'])
                        ui.run_javascript(f'galleryToggleSelect({local_idx},{img["id"]},false)')
                    else:
                        state['selected'].add(img['id'])
                        ui.run_javascript(f'galleryToggleSelect({local_idx},{img["id"]},true)')
                    update_sel_indicator()
            elif key == 'Enter':
                state['current_index'] = fi
                switch_view('single')
            elif key == 'a' and getattr(e, 'ctrl_key', False):
                state['selected'] = {img['id'] for img in state['images']}
                render_content()
            elif key == 'Escape':
                if state['selected']:
                    state['selected'].clear()
                    update_sel_indicator()
                    render_content()
                else:
                    close_analyze()
                    close_tag_panel()
                    close_coll_panel()
            elif key in ('f', 'F'):
                img = focused_img()
                if img:
                    for img_id in action_targets():
                        db.toggle_favorite(img_id)
                    reload_images()
                    render_content()
            elif key in ('t', 'T'):
                toggle_tags()
            elif key in ('a', 'A'):
                toggle_analyze()
            elif key in ('c', 'C'):
                toggle_coll()
            elif key in ('s', 'S'):
                switch_view('single')
            return

        # ── Single view keys ───────────────────────────────────────────────────
        if key in ('ArrowLeft', 'ArrowRight'):
            if state['images']:
                delta = -1 if key == 'ArrowLeft' else 1
                state['current_index'] = (state['current_index'] + delta) % len(state['images'])
                render_content()
        elif key in ('f', 'F'):
            img = current_img()
            if img:
                db.toggle_favorite(img['id'])
                reload_images()
                render_content()
        elif key == ' ':
            img = current_img()
            if img:
                if img['id'] in state['selected']:
                    state['selected'].discard(img['id'])
                else:
                    state['selected'].add(img['id'])
                render_content()
        elif key in ('t', 'T'):
            toggle_tags()
        elif key in ('a', 'A'):
            toggle_analyze()
        elif key in ('c', 'C'):
            toggle_coll()
        elif key in ('g', 'G'):
            switch_view('gallery')
        elif key == 'Escape':
            if state['selected']:
                state['selected'].clear()
                render_content()
            else:
                close_analyze()
                close_tag_panel()
                close_coll_panel()

    ui.keyboard(on_key=handle_key, ignore=['input', 'select', 'textarea'])

    render_content()
    update_filmstrip()


ui.run(
    title='📸 Photo Gallery Organizer',
    dark=True,
    port=8080,
    reload=False,
    favicon='📸'
)
