# Clipper Tool - Web Version

Multi-clip extractor web app. Analyze and clip viral segments from any video
without downloading the full file. Runs on Railway with FFmpeg + yt-dlp.

## Features

- Analyze any video URL (YouTube, TikTok, Instagram, Twitter/X, etc.)
- Transcript-based hook detection (finds viral keywords)
- Audio loudness probing (no full download needed)
- Smart 9:16 crop using FFmpeg pixel analysis (no OpenCV required)
- Real-time progress via Server-Sent Events
- Download up to 11 non-overlapping clips per video
- Dark responsive UI for all device sizes

## Deploy to Railway

1. Push this folder to a GitHub repo
2. Go to https://railway.app and create a New Project
3. Connect your GitHub repo
4. Railway will auto-detect nixpacks.toml and install ffmpeg + yt-dlp
5. Set PORT environment variable if needed (default: 5000)
6. Done - your app will be live at the Railway URL

## Local run

```bash
pip install flask yt-dlp
# Make sure ffmpeg is installed: sudo apt install ffmpeg
python app.py
```

Then open http://localhost:5000

## Environment variables

- PORT: HTTP port (default 5000)
- SECRET_KEY: Flask session secret (set a random string in production)

## Notes

- yt-dlp and ffmpeg must be available in PATH
- Clips are saved in ClipperTool/clips/
- No OpenCV or other heavy ML libraries required
- Smart crop uses FFmpeg pixel analysis (luminance-based center of mass)
