import base64
import os
from PIL import Image
import io

def get_image_base64(image_path: str, max_size: tuple = (1024, 1024)) -> str:
    """
    Load image and convert to base64 string, resizing if needed for LLM processing
    
    Args:
        image_path: Path to the image file
        max_size: Maximum dimensions (width, height) for resizing
    
    Returns:
        Base64 encoded string of the image
    """
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            
            # Resize if larger than max_size
            img.thumbnail(max_size, Image.Resampling.LANCZOS)
            
            # Convert to base64
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=85)
            img_bytes = buffer.getvalue()
            
            return base64.b64encode(img_bytes).decode('utf-8')
    except Exception as e:
        print(f"Error processing image {image_path}: {e}")
        return None


def get_thumbnail(image_path: str, size: tuple = (300, 300)) -> Image.Image:
    """
    Generate thumbnail for display in gallery
    
    Args:
        image_path: Path to the image file
        size: Thumbnail size (width, height)
    
    Returns:
        PIL Image object
    """
    try:
        with Image.open(image_path) as img:
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            
            # Create thumbnail
            img.thumbnail(size, Image.Resampling.LANCZOS)
            return img.copy()
    except Exception as e:
        print(f"Error creating thumbnail for {image_path}: {e}")
        return None


def is_video(filename: str) -> bool:
    """Check if file is a video based on extension"""
    video_extensions = {'.mp4', '.mov', '.avi', '.mkv', '.wmv', '.flv'}
    ext = os.path.splitext(filename)[1].lower()
    return ext in video_extensions


def is_image(filename: str) -> bool:
    """Check if file is an image based on extension"""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp'}
    ext = os.path.splitext(filename)[1].lower()
    return ext in image_extensions


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


# LLM Analysis Presets
ANALYSIS_PRESETS = {
    "Quick Overview": {
        "prompt": "Provide a brief 2-3 sentence description of this image, focusing on the main subject and setting.",
        "temperature": 0.3
    },
    "Detailed Analysis": {
        "prompt": """Analyze this image in detail and provide:
1. Main Subject: What is the primary focus?
2. Setting/Location: Describe the environment
3. People: Number of people visible and their activities
4. Mood/Atmosphere: Overall feeling of the scene
5. Notable Details: Any interesting or unique elements""",
        "temperature": 0.5
    },
    "Instagram Caption - Witty": {
        "prompt": "Write a witty, clever Instagram caption for this photo. Include 1-2 relevant emojis and make it engaging and fun.",
        "temperature": 0.9
    },
    "Instagram Caption - Inspirational": {
        "prompt": "Write an inspirational Instagram caption for this photo. Make it uplifting and motivational. Include 2-3 relevant emojis.",
        "temperature": 0.8
    },
    "Instagram Caption - Funny": {
        "prompt": "Write a funny, humorous Instagram caption for this photo. Make people laugh! Include 2-3 relevant emojis.",
        "temperature": 1.0
    },
    "Instagram Caption - Poetic": {
        "prompt": "Write a poetic, artistic Instagram caption for this photo. Be creative and evocative. Include 1-2 relevant emojis.",
        "temperature": 0.9
    },
    "Instagram Caption - Critical": {
        "prompt": "Write a thoughtful, critical Instagram caption for this photo that makes people think. Be insightful. Include 1-2 relevant emojis.",
        "temperature": 0.7
    },
    "People Detection": {
        "prompt": """Analyze the people in this image:
1. Number of people visible
2. Their approximate ages/demographics
3. What they are doing
4. Their expressions/emotions
5. Clothing/appearance details
If no people are visible, state that clearly.""",
        "temperature": 0.3
    },
    "Location & Scenery": {
        "prompt": """Describe the location and scenery in this image:
1. Type of location (mountain, beach, city, indoor, etc.)
2. Weather conditions
3. Time of day (if discernible)
4. Natural features or landmarks
5. Overall atmosphere""",
        "temperature": 0.4
    },
    "Technical Analysis": {
        "prompt": """Provide a technical analysis of this photograph:
1. Composition: Rule of thirds, framing, balance
2. Lighting: Quality, direction, mood
3. Colors: Dominant colors and their effect
4. Focus: What's in focus, depth of field
5. Overall quality and suggestions for improvement""",
        "temperature": 0.5
    }
}


def get_analysis_prompt(preset_name: str) -> tuple:
    """
    Get the prompt and temperature for a given preset
    
    Returns:
        (prompt, temperature) tuple
    """
    preset = ANALYSIS_PRESETS.get(preset_name, ANALYSIS_PRESETS["Quick Overview"])
    return preset["prompt"], preset["temperature"]
