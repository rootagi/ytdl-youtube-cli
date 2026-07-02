# YouTube Video Download (CLI)

A beautiful, self-contained, interactive YouTube Downloader CLI tool built in Python. Inspired by the download mechanics of the `ytdlnis` Android application, this tool features an interactive keyboard TUI and advanced integration options.

## Features
- **Zero-Dependency**: Written in pure Python 3 using standard library modules (no third-party Python package installation needed).
- **Interactive Keyboard TUI**: Use `Up`/`Down` arrow keys to choose qualities, select chapters, and navigate options in real-time. Use `Space` to check/uncheck optional download parameters and select playlist items.
- **Solving JS Spinner**: Renders progress of YouTube player JS decryption in real-time.
- **SponsorBlock**: Native support to dynamically strip sponsors, intro/outro, and promotional blocks from downloads.
- **Audio-Only Export & Conversion**: Choose to convert audio-only downloads to MP3 (320kbps/192kbps), FLAC, OPUS, or WAV using `ffmpeg`.
- **Subtitles & Chapters**: Embed chapter markers and soft-subtitles directly inside downloaded files.
- **Chapter-Based Video Slicing**: Interactively select and download individual video chapters directly from the terminal menu.
- **Metadata & Thumbnail Tagging**: Auto-embeds descriptions, uploaders, and high-res thumbnails directly into the video wrapper (VLC and Plex ready).
- **Interactive Playlist Checklist**: Download single videos, the entire playlist, or choose custom selections using a checkbox checklist.
- **Concurrent & Parallel Queue**: Download multiple videos in parallel with adjustable concurrent streams (2-5 threads) and thread-safe logging.
- **Local Configurations & Custom Paths**: Saves your settings as new defaults dynamically into `~/.config/ytdl-cli/config.json`, including custom download directories.
- **Dynamic Binary Setup**: Installs local, standalone versions of `yt-dlp` and `ffmpeg` in `./bin` if they are not already installed on your system PATH.

## Usage
Start the tool interactively:
```bash
python3 ytdl-cli.py
```

Pass single or multiple URLs to queue them automatically:
```bash
python3 ytdl-cli.py "url1" "url2" "url3"
```

Specify a custom download directory explicitly:
```bash
python3 ytdl-cli.py -o /path/to/directory "url"
```

Force-check and update the underlying `yt-dlp` engine:
```bash
python3 ytdl-cli.py --update
```

Show the help menu and exit:
```bash
python3 ytdl-cli.py --help
```

## System Requirements
- Python 3.x (already installed on your system).
- (Optional) `ffmpeg` installed globally:
  ```bash
  sudo apt install ffmpeg
  ```
  *If missing, the script will automatically install it locally into `./bin` for slicing, merging, and embedding features.*

## License
Licensed under the [GNU GPL v3 License](LICENSE). Feel free to modify, distribute, and fork!
