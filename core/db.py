import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional, Tuple

DB_PATH = "photos.db"

def init_db():
    """Initialize the SQLite database with required tables"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Images table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS images (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            filename TEXT NOT NULL,
            source_folder TEXT,
            is_favorite INTEGER DEFAULT 0,
            llm_analysis TEXT,
            analysis_preset TEXT,
            file_size INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # Tags table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL
        )
    """)
    
    # Image-Tags junction table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS image_tags (
            image_id INTEGER,
            tag_id INTEGER,
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE,
            PRIMARY KEY (image_id, tag_id)
        )
    """)
    
    # Scanned paths table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS scanned_paths (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            path TEXT UNIQUE NOT NULL,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Analyses table (multiple analyses per image)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            image_id INTEGER NOT NULL,
            preset_name TEXT NOT NULL,
            result TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
        )
    """)

    # Collections
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS collections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS collection_items (
            collection_id INTEGER NOT NULL,
            image_id INTEGER NOT NULL,
            PRIMARY KEY (collection_id, image_id),
            FOREIGN KEY (collection_id) REFERENCES collections(id) ON DELETE CASCADE,
            FOREIGN KEY (image_id) REFERENCES images(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def scan_and_add_images(base_path: str):
    """Scan directory and add new images to database. Uses INSERT OR IGNORE to avoid duplicates."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    supported_extensions = {'.jpg', '.jpeg', '.png', '.mp4', '.mov', '.avi'}
    added_count = 0
    
    for root, dirs, files in os.walk(base_path):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in supported_extensions:
                full_path = os.path.join(root, file)
                # Store absolute path to handle multiple scan locations
                abs_path = os.path.abspath(full_path)
                source_folder = os.path.basename(root)
                
                try:
                    file_size = os.path.getsize(full_path)
                    # Check if already exists before inserting
                    cursor.execute("SELECT id FROM images WHERE path = ?", (abs_path,))
                    if cursor.fetchone() is None:
                        cursor.execute("""
                            INSERT INTO images (path, filename, source_folder, file_size)
                            VALUES (?, ?, ?, ?)
                        """, (abs_path, file, source_folder, file_size))
                        added_count += 1
                except Exception as e:
                    print(f"Error adding {file}: {e}")
    
    conn.commit()
    conn.close()
    return added_count


def get_all_images(filters: Optional[Dict] = None) -> List[Dict]:
    """Get all images with optional filters"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    query = "SELECT * FROM images WHERE 1=1"
    params = []
    
    if filters:
        if filters.get('favorite_only'):
            query += " AND is_favorite = 1"
        if filters.get('analyzed_only'):
            query += " AND llm_analysis IS NOT NULL"
        if filters.get('not_analyzed'):
            query += " AND llm_analysis IS NULL"
        if filters.get('not_tagged'):
            query += " AND id NOT IN (SELECT image_id FROM image_tags)"
        if filters.get('source_folder'):
            query += " AND source_folder = ?"
            params.append(filters['source_folder'])
        if filters.get('tags'):
            tag_placeholders = ','.join('?' * len(filters['tags']))
            query += f"""
                AND id IN (
                    SELECT image_id FROM image_tags 
                    WHERE tag_id IN (
                        SELECT id FROM tags WHERE name IN ({tag_placeholders})
                    )
                )
            """
            params.extend(filters['tags'])
        if filters.get('collection_id'):
            query += " AND id IN (SELECT image_id FROM collection_items WHERE collection_id = ?)"
            params.append(filters['collection_id'])
    
    query += " ORDER BY created_at DESC"
    
    cursor.execute(query, params)
    images = [dict(row) for row in cursor.fetchall()]
    
    # Get tags for each image
    for img in images:
        cursor.execute("""
            SELECT t.name FROM tags t
            JOIN image_tags it ON t.id = it.tag_id
            WHERE it.image_id = ?
        """, (img['id'],))
        img['tags'] = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return images


def get_image_by_id(image_id: int) -> Optional[Dict]:
    """Get a single image by ID"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM images WHERE id = ?", (image_id,))
    row = cursor.fetchone()
    
    if row:
        img = dict(row)
        cursor.execute("""
            SELECT t.name FROM tags t
            JOIN image_tags it ON t.id = it.tag_id
            WHERE it.image_id = ?
        """, (image_id,))
        img['tags'] = [row[0] for row in cursor.fetchall()]
        conn.close()
        return img
    
    conn.close()
    return None


def toggle_favorite(image_id: int) -> bool:
    """Toggle favorite status of an image"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT is_favorite FROM images WHERE id = ?", (image_id,))
    current = cursor.fetchone()[0]
    new_value = 0 if current else 1
    
    cursor.execute("UPDATE images SET is_favorite = ? WHERE id = ?", (new_value, image_id))
    conn.commit()
    conn.close()
    return bool(new_value)


def update_llm_analysis(image_id: int, analysis: str, preset: str):
    """Update LLM analysis for an image"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        UPDATE images 
        SET llm_analysis = ?, analysis_preset = ?
        WHERE id = ?
    """, (analysis, preset, image_id))
    
    conn.commit()
    conn.close()


def add_tag(tag_name: str) -> int:
    """Add a new tag or return existing tag ID"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tag_name,))
    cursor.execute("SELECT id FROM tags WHERE name = ?", (tag_name,))
    tag_id = cursor.fetchone()[0]
    
    conn.commit()
    conn.close()
    return tag_id


def get_all_tags() -> List[str]:
    """Get all available tags"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT name FROM tags ORDER BY name")
    tags = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return tags


def add_tag_to_image(image_id: int, tag_name: str):
    """Add a tag to an image"""
    tag_id = add_tag(tag_name)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        INSERT OR IGNORE INTO image_tags (image_id, tag_id)
        VALUES (?, ?)
    """, (image_id, tag_id))
    
    conn.commit()
    conn.close()


def remove_tag_from_image(image_id: int, tag_name: str):
    """Remove a tag from an image"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("""
        DELETE FROM image_tags
        WHERE image_id = ? AND tag_id = (
            SELECT id FROM tags WHERE name = ?
        )
    """, (image_id, tag_name))
    
    conn.commit()
    conn.close()


def get_source_folders() -> List[str]:
    """Get all unique source folders"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT DISTINCT source_folder FROM images ORDER BY source_folder")
    folders = [row[0] for row in cursor.fetchall()]
    
    conn.close()
    return folders


def get_stats() -> Dict:
    """Get database statistics"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM images")
    total_images = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM images WHERE is_favorite = 1")
    favorites = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM images WHERE llm_analysis IS NOT NULL")
    analyzed = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM tags")
    total_tags = cursor.fetchone()[0]
    
    conn.close()
    
    return {
        'total_images': total_images,
        'favorites': favorites,
        'analyzed': analyzed,
        'total_tags': total_tags
    }


def add_scanned_path(path: str):
    """Add a scanned path to the database"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO scanned_paths (path) VALUES (?)", (os.path.abspath(path),))
    conn.commit()
    conn.close()


def get_scanned_paths() -> List[str]:
    """Get all scanned paths"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT path FROM scanned_paths ORDER BY added_at")
    paths = [row[0] for row in cursor.fetchall()]
    conn.close()
    return paths


def remove_scanned_path(path: str):
    """Remove a scanned path"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM scanned_paths WHERE path = ?", (path,))
    conn.commit()
    conn.close()


def add_analysis(image_id: int, preset_name: str, result: str):
    """Add an analysis result for an image."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO analyses (image_id, preset_name, result) VALUES (?, ?, ?)",
        (image_id, preset_name, result)
    )
    conn.commit()
    conn.close()


def get_analyses_for_image(image_id: int) -> List[Dict]:
    """Get all analyses for an image, newest first."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, preset_name, result, created_at FROM analyses WHERE image_id = ? ORDER BY created_at DESC",
        (image_id,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def delete_analysis(analysis_id: int):
    """Delete a specific analysis entry."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM analyses WHERE id = ?", (analysis_id,))
    conn.commit()
    conn.close()


def get_analyzed_image_ids() -> set:
    """Return set of image IDs that have at least one analysis."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT DISTINCT image_id FROM analyses")
    ids = {row[0] for row in cursor.fetchall()}
    conn.close()
    return ids


def get_tag_usage_counts() -> List[Dict]:
    """Return all tags with how many images use each."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT t.name, COUNT(it.image_id) as count
        FROM tags t
        LEFT JOIN image_tags it ON t.id = it.tag_id
        GROUP BY t.id, t.name
        ORDER BY t.name
    """)
    rows = [{'name': r[0], 'count': r[1]} for r in cursor.fetchall()]
    conn.close()
    return rows


def delete_tag(tag_name: str):
    """Remove a tag and all its image associations. Images themselves are untouched."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM image_tags WHERE tag_id = (SELECT id FROM tags WHERE name = ?)", (tag_name,))
    cursor.execute("DELETE FROM tags WHERE name = ?", (tag_name,))
    conn.commit()
    conn.close()


# ── Collections ────────────────────────────────────────────────────────────────

def create_collection(name: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO collections (name) VALUES (?)", (name,))
    cursor.execute("SELECT id FROM collections WHERE name = ?", (name,))
    cid = cursor.fetchone()[0]
    conn.commit()
    conn.close()
    return cid


def list_collections() -> List[Dict]:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name, COUNT(ci.image_id) as count
        FROM collections c
        LEFT JOIN collection_items ci ON c.id = ci.collection_id
        GROUP BY c.id, c.name
        ORDER BY c.name
    """)
    rows = [{'id': r[0], 'name': r[1], 'count': r[2]} for r in cursor.fetchall()]
    conn.close()
    return rows


def add_to_collection(collection_id: int, image_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO collection_items (collection_id, image_id) VALUES (?, ?)",
        (collection_id, image_id)
    )
    conn.commit()
    conn.close()


def remove_from_collection(collection_id: int, image_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM collection_items WHERE collection_id = ? AND image_id = ?",
        (collection_id, image_id)
    )
    conn.commit()
    conn.close()


def delete_collection(collection_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM collection_items WHERE collection_id = ?", (collection_id,))
    cursor.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    conn.commit()
    conn.close()


def get_collection_image_paths(collection_id: int) -> List[Dict]:
    """Return list of {id, path, filename} for all images in a collection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT i.id, i.path, i.filename
        FROM images i
        JOIN collection_items ci ON i.id = ci.image_id
        WHERE ci.collection_id = ?
        ORDER BY i.filename
    """, (collection_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_image_collections(image_id: int) -> List[Dict]:
    """Return list of {id, name} for all collections an image belongs to."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT c.id, c.name
        FROM collections c
        JOIN collection_items ci ON c.id = ci.collection_id
        WHERE ci.image_id = ?
        ORDER BY c.name
    """, (image_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows
