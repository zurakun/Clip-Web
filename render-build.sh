#!/bin/bash
# Install ffmpeg
apt-get update && apt-get install -y ffmpeg

# Install yt-dlp
pip install yt-dlp

# Install requirements
pip install -r requirements.txt
