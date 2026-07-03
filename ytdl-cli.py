#!/usr/bin/env python3
import os
import sys
import subprocess
import json
import re
import urllib.request
import ssl
import zipfile
import threading
import time
import shutil
import stat

# TTY/Termios imports for Linux keyboard listening
try:
    import tty
    import termios
except ImportError:
    
    tty = None
    termios = None

# Colors
GREEN = "\033[92m"
BLUE = "\033[94m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"
DIM = "\033[2m"

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
YTDLP_BIN = os.path.join(BIN_DIR, "yt-dlp")
FFMPEG_BIN = os.path.join(BIN_DIR, "ffmpeg")
CONFIG_PATH = os.path.expanduser("~/.config/ytdl-cli/config.json")

# Regex to parse yt-dlp progress
PROGRESS_REGEX = re.compile(
    r"\[download\]\s+([0-9.]+)%\s+of\s+(?:~\s*)?([0-9.]+)([a-zA-Z]+)\s+at\s+([0-9.]+)([a-zA-Z]+/s)\s+ETA\s+([0-9:-]+)"
)

def print_status(message, color=BLUE):
    print(f"{color}[*] {message}{RESET}")

def print_success(message):
    print(f"{GREEN}[+] {message}{RESET}")

def print_error(message):
    print(f"{RED}[!] {message}{RESET}")

def print_warning(message):
    print(f"{YELLOW}[!] {message}{RESET}")

def format_size(bytes_size):
    if not bytes_size:
        return "Unknown size"
    for unit in ['B', 'KB', 'MB', 'GB']:
        if bytes_size < 1024.0:
            return f"{bytes_size:.2f} {unit}"
        bytes_size /= 1024.0
    return f"{bytes_size:.2f} TB"

def draw_progress_bar(percentage, total_size, speed, eta, status="Downloading"):
    width = 30
    filled_len = int(round(width * percentage / 100))
    bar = "█" * filled_len + "░" * (width - filled_len)
    
    if percentage < 30:
        color = RED
    elif percentage < 70:
        color = YELLOW
    else:
        color = GREEN
        
    sys.stdout.write(
        f"\r{BOLD}{status:<15}{RESET} [{color}{bar}{RESET}] "
        f"{BOLD}{percentage:5.1f}%{RESET} | "
        f"{CYAN}{total_size:<10}{RESET} | "
        f"{BLUE}{speed:<10}{RESET} | "
        f"ETA: {YELLOW}{eta:<6}{RESET}"
    )
    sys.stdout.flush()

class Spinner:
    def __init__(self, message="Loading..."):
        self.message = message
        self.spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        self.stop_running = threading.Event()
        self.thread = None

    def _spin(self):
        idx = 0
        while not self.stop_running.is_set():
            sys.stdout.write(f"\r{CYAN}{self.spinner_chars[idx]} {self.message}{RESET}")
            sys.stdout.flush()
            idx = (idx + 1) % len(self.spinner_chars)
            time.sleep(0.08)

    def start(self):
        self.stop_running.clear()
        self.thread = threading.Thread(target=self._spin)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        if self.thread:
            self.stop_running.set()
            self.thread.join()
            sys.stdout.write("\r" + " " * (len(self.message) + 20) + "\r")
            sys.stdout.flush()

def download_file_with_progress(url, dest_path, item_name):
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url, 
        headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
    )
    
    with urllib.request.urlopen(req, context=ctx) as response:
        total_size = int(response.info().get('Content-Length', 0))
        block_size = 1024 * 8
        downloaded = 0
        
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        
        with open(dest_path, 'wb') as f:
            while True:
                buffer = response.read(block_size)
                if not buffer:
                    break
                downloaded += len(buffer)
                f.write(buffer)
                
                if total_size > 0:
                    percent = downloaded * 100 / total_size
                    draw_progress_bar(percent, format_size(total_size), "", "", f"Setup {item_name}")
                else:
                    sys.stdout.write(f"\rSetup {item_name}: {format_size(downloaded)} downloaded...")
                    sys.stdout.flush()
        print()

def get_ytdlp_path():
    system_ytdlp = shutil.which("yt-dlp")
    if system_ytdlp:
        return system_ytdlp
    if os.path.exists(YTDLP_BIN):
        return YTDLP_BIN
        
    print_warning("yt-dlp not found on system. Downloading portable version...")
    url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    try:
        download_file_with_progress(url, YTDLP_BIN, "yt-dlp")
        st = os.stat(YTDLP_BIN)
        os.chmod(YTDLP_BIN, st.st_mode | stat.S_IEXEC)
        print_success("yt-dlp setup complete.")
        return YTDLP_BIN
    except Exception as e:
        print_error(f"Failed to download yt-dlp: {e}")
        sys.exit(1)

def get_ffmpeg_path():
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg
    if os.path.exists(FFMPEG_BIN):
        return FFMPEG_BIN
        
    print_warning("ffmpeg not found on system. Slicing/merging requires ffmpeg. Downloading static binary...")
    url = "https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/v4.4.1/ffmpeg-4.4.1-linux-64.zip"
    zip_path = os.path.join(BIN_DIR, "ffmpeg.zip")
    try:
        download_file_with_progress(url, zip_path, "ffmpeg")
        print_status("Extracting ffmpeg...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(BIN_DIR)
        os.remove(zip_path)
        
        st = os.stat(FFMPEG_BIN)
        os.chmod(FFMPEG_BIN, st.st_mode | stat.S_IEXEC)
        print_success("ffmpeg setup complete.")
        return FFMPEG_BIN
    except Exception as e:
        print_error(f"Failed to download/extract ffmpeg: {e}")
        print_warning("Downloading will continue without quality merging or slicing capabilities.")
        return None

# Config management
def load_config():
    default_config = {
        "sponsorblock": True,
        "embed_metadata": True,
        "embed_thumbnail": True,
        "embed_subtitles": False,
        "embed_chapters": True
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                default_config.update(data)
        except Exception:
            pass
    return default_config

def save_config(config):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        with open(CONFIG_PATH, "w") as f:
            json.dump(config, f, indent=4)
        print_success(f"Defaults updated in: {CONFIG_PATH}")
    except Exception as e:
        print_error(f"Failed to save defaults: {e}")

# Key Listening functions
def get_key():
    if not tty or not termios or not sys.stdin.isatty():
        return sys.stdin.read(1)
        
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return sys.stdin.read(1)
        
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == '\x03':  # Ctrl+C
            raise KeyboardInterrupt()
        if ch == '\x1b':
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':
                    return 'UP'
                elif ch3 == 'B':
                    return 'DOWN'
        return ch
    finally:
        try:
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        except Exception:
            pass

# Interactive arrow-key navigation menu
def interactive_menu(title, options, multi_select=False, default_selections=None):
    # Non-TTY / Non-interactive fallback
    if not sys.stdin.isatty() or not tty or not termios:
        if multi_select:
            print(f"\n{BOLD}{CYAN}{title} (Non-Interactive Mode - using defaults){RESET}")
            for i, opt in enumerate(options):
                status = "ENABLED" if (default_selections and default_selections[i]) else "DISABLED"
                print(f"   - {opt['label']}: {status}")
            return default_selections if default_selections else [False] * len(options)
        else:
            print(f"\n{BOLD}{CYAN}{title}{RESET}")
            for i, opt in enumerate(options, 1):
                print(f"  [{i}] {opt['label']}")
            while True:
                try:
                    choice = input(f"Select option (1-{len(options)}): ").strip()
                    if choice.isdigit():
                        idx = int(choice) - 1
                        if 0 <= idx < len(options):
                            return idx
                    print_error("Invalid selection.")
                except (IOError, EOFError):
                    print_warning("No input stream. Selecting option 1 by default.")
                    return 0

    print(f"\n{BOLD}{CYAN}{title}{RESET}")
    if multi_select:
        print(f"{DIM}(Press [Space] to toggle options, [Enter] to confirm selections){RESET}")
    else:
        print(f"{DIM}(Use [Up/Down] arrows to navigate, [Enter] to select){RESET}")
        
    n = len(options)
    selected_idx = 0
    
    if multi_select:
        selections = default_selections[:] if default_selections else [False] * n
    else:
        selections = None
        
    def draw():
        for i in range(n):
            prefix = " > " if i == selected_idx else "   "
            color = GREEN if i == selected_idx else RESET
            
            if multi_select:
                checkbox = f"[{GREEN}x{RESET}]" if selections[i] else "[ ]"
                sys.stdout.write(f"{prefix}{checkbox} {color}{options[i]['label']}{RESET}\033[K\n")
            else:
                sys.stdout.write(f"{prefix}{color}{options[i]['label']}{RESET}\033[K\n")
        sys.stdout.write(f"\033[{n}A")
        sys.stdout.flush()
        
    draw()
    
    try:
        while True:
            key = get_key()
            if key == 'UP':
                selected_idx = (selected_idx - 1) % n
                draw()
            elif key == 'DOWN':
                selected_idx = (selected_idx + 1) % n
                draw()
            elif key == ' ' and multi_select:
                selections[selected_idx] = not selections[selected_idx]
                draw()
            elif key == '\r' or key == '\n':
                sys.stdout.write(f"\033[{n}B\n")
                sys.stdout.flush()
                if multi_select:
                    return selections
                else:
                    return selected_idx
    except KeyboardInterrupt:
        sys.stdout.write(f"\033[{n}B\n")
        sys.stdout.flush()
        raise KeyboardInterrupt()

def fetch_metadata(url, ytdlp_path, ffmpeg_path=None, flat=False):
    cmd = [ytdlp_path, "-J"]
    if flat:
        cmd.append("--flat-playlist")
    else:
        cmd.append("--no-playlist")
    cmd.append(url)
    
    if ffmpeg_path:
        cmd.extend(["--ffmpeg-location", ffmpeg_path])
        
    spinner = Spinner("Solving JS Player & Fetching webpage...")
    spinner.start()
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    
    stderr_lines = []
    
    def read_stderr():
        for line in process.stderr:
            clean_line = line.strip()
            stderr_lines.append(clean_line)
            if "extracting" in clean_line.lower() or "signature" in clean_line.lower() or "js" in clean_line.lower() or "downloading" in clean_line.lower():
                display_line = clean_line.replace("[youtube] ", "").strip()
                if len(display_line) > 50:
                    display_line = display_line[:47] + "..."
                spinner.message = f"Solving JS / Fetching: {display_line}"
                
    stderr_thread = threading.Thread(target=read_stderr)
    stderr_thread.daemon = True
    stderr_thread.start()
    
    stdout_data, _ = process.communicate()
    spinner.stop()
    stderr_thread.join()
    
    if process.returncode != 0:
        error_msg = "\n".join(stderr_lines)
        raise Exception(f"Extraction failed: {error_msg}")
        
    return json.loads(stdout_data)

def parse_time_to_seconds(time_str):
    time_str = time_str.strip()
    if not time_str:
        return None
    if time_str.isdigit():
        return int(time_str)
    
    parts = time_str.split(":")
    if len(parts) == 2: # mm:ss
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3: # hh:mm:ss
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    
    raise ValueError("Invalid time format. Use seconds, mm:ss, or hh:mm:ss")

def select_quality_menu(json_data):
    formats = json_data.get("formats", [])
    
    audio_formats = []
    for fmt in formats:
        if fmt.get("acodec") != "none" and fmt.get("vcodec") == "none":
            audio_formats.append(fmt)
            
    best_audio = None
    if audio_formats:
        best_audio = max(audio_formats, key=lambda f: f.get("abr") or f.get("tbr") or 0)
        
    best_audio_size = (best_audio.get("filesize") or best_audio.get("filesize_approx") or 0) if best_audio else 0
    
    video_resolutions = {}
    for fmt in formats:
        height = fmt.get("height")
        if height and fmt.get("vcodec") != "none":
            if height not in video_resolutions:
                video_resolutions[height] = fmt
            else:
                curr = video_resolutions[height]
                curr_br = curr.get("vbr") or curr.get("tbr") or 0
                fmt_br = fmt.get("vbr") or fmt.get("tbr") or 0
                if fmt_br > curr_br:
                    video_resolutions[height] = fmt
                    
    choices = []
    for height in sorted(video_resolutions.keys(), reverse=True):
        fmt = video_resolutions[height]
        v_size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        total_size = v_size + best_audio_size if fmt.get("acodec") == "none" else v_size
        
        choices.append({
            "label": f"{height}p ({fmt.get('ext', 'mp4')})",
            "format_id": f"{fmt['format_id']}+{best_audio['format_id']}" if fmt.get("acodec") == "none" and best_audio else fmt['format_id'],
            "size": total_size,
            "vcodec": fmt.get("vcodec", "unknown"),
            "acodec": best_audio.get("acodec", "none") if fmt.get("acodec") == "none" and best_audio else fmt.get("acodec", "none")
        })
        
    if best_audio:
        choices.append({
            "label": f"Audio Only ({best_audio.get('ext', 'm4a')})",
            "format_id": best_audio["format_id"],
            "size": best_audio_size,
            "vcodec": "none",
            "acodec": best_audio.get("acodec", "unknown")
        })
        
    choices.append({
        "label": "Custom Format ID (Advanced input)",
        "format_id": "CUSTOM",
        "size": 0,
        "vcodec": "",
        "acodec": ""
    })
    
    # Map options for TUI
    menu_options = []
    for c in choices:
        size_str = format_size(c["size"]) if c["size"] > 0 else "-"
        v_codec = c["vcodec"] if c["vcodec"] else "-"
        a_codec = c["acodec"] if c["acodec"] else "-"
        label = f"{c['label']:<20} | Est: {size_str:<10} | V-Codec: {v_codec:<10} | A-Codec: {a_codec}"
        menu_options.append({"label": label, "value": c["format_id"]})
        
    title = f"Select Resolution Quality for: {json_data.get('title')}"
    selected_idx = interactive_menu(title, menu_options)
    
    chosen_val = menu_options[selected_idx]["value"]
    if chosen_val == "CUSTOM":
        custom_id = input(f"Enter custom format ID combination (e.g. 137+140): ").strip()
        return custom_id
    return chosen_val

def select_features_menu(config):
    features_list = [
        {"label": "Skip Sponsors & Self-promos (SponsorBlock)", "key": "sponsorblock"},
        {"label": "Embed Metadata (Title, Artist, Date)", "key": "embed_metadata"},
        {"label": "Embed Thumbnail", "key": "embed_thumbnail"},
        {"label": "Embed Subtitles (Soft subs if available)", "key": "embed_subtitles"},
        {"label": "Embed Chapter Markers", "key": "embed_chapters"},
        {"label": "Save selections as your new default settings", "key": "save_defaults"}
    ]
    
    default_selections = [
        config["sponsorblock"],
        config["embed_metadata"],
        config["embed_thumbnail"],
        config["embed_subtitles"],
        config["embed_chapters"],
        False
    ]
    
    selections = interactive_menu("Configure Advanced Download Options", features_list, multi_select=True, default_selections=default_selections)
    
    active_features = {}
    for i, feature in enumerate(features_list[:-1]):
        active_features[feature["key"]] = selections[i]
        
    save_defaults = selections[-1]
    if save_defaults:
        save_config(active_features)
        
    return active_features

def download_video(url, format_id, ytdlp_path, ffmpeg_path=None, start_time=None, end_time=None, features=None):
    cmd = [ytdlp_path, "-f", format_id, "--newline", "--no-playlist", url]
    
    if ffmpeg_path:
        cmd.extend(["--ffmpeg-location", ffmpeg_path])
        
    # Inject advanced features parameters
    if features:
        if features.get("sponsorblock"):
            cmd.extend(["--sponsorblock-remove", "all"])
        if features.get("embed_metadata"):
            cmd.append("--embed-metadata")
        if features.get("embed_thumbnail"):
            cmd.append("--embed-thumbnail")
        if features.get("embed_subtitles"):
            cmd.extend(["--embed-subs", "--sub-langs", "all"])
        if features.get("embed_chapters"):
            cmd.append("--embed-chapters")
            
    # Slicing/Cutting setup
    if start_time is not None or end_time is not None:
        start_str = str(start_time) if start_time is not None else "0"
        end_str = str(end_time) if end_time is not None else "inf"
        cmd.extend(["--download-sections", f"*{start_str}-{end_str}"])
        cmd.append("--force-keyframes-at-cuts")
        
    print_status("Starting download process...")
    
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    stream_count = 0
    total_streams = 2 if "+" in format_id else 1
    
    for line in process.stdout:
        clean_line = line.strip()
        
        if "Destination:" in clean_line:
            stream_count += 1
            continue
            
        match = PROGRESS_REGEX.search(clean_line)
        if match:
            percent = float(match.group(1))
            total = f"{match.group(2)}{match.group(3)}"
            speed = f"{match.group(4)}{match.group(5)}"
            eta = match.group(6)
            
            stream_str = f"Stream {stream_count}/{total_streams}" if total_streams > 1 else "Downloading"
            draw_progress_bar(percent, total, speed, eta, stream_str)
        elif "Merging formats" in clean_line:
            sys.stdout.write("\n")
            print_status("Merging streams into final file...", GREEN)
        elif "Extracting video section" in clean_line or "Converting" in clean_line:
            sys.stdout.write("\n")
            print_status("Applying slice cuts and formatting...", GREEN)
        elif "SponsorBlock" in clean_line:
            sys.stdout.write("\n")
            print_status(f"SponsorBlock: {clean_line.replace('[SponsorBlock] ', '')}", GREEN)
        elif "100% of" in clean_line and "in" in clean_line:
            sys.stdout.write("\n")
            print_success("Download chunk finished.")
            
    process.wait()
    
    if process.returncode == 0:
        sys.stdout.write("\n")
        print_success("Process finished successfully!")
    else:
        print_error(f"Download process failed with exit code: {process.returncode}")

def handle_single_video(url, ytdlp_path, ffmpeg_path, config):
    try:
        meta = fetch_metadata(url, ytdlp_path, ffmpeg_path)
    except Exception as e:
        print_error(f"Error fetching metadata: {e}")
        return None
        
    duration = meta.get("duration", 0)
    duration_str = time.strftime('%H:%M:%S', time.gmtime(duration))
    print_success(f"Successfully loaded: {BOLD}{meta.get('title')}{RESET} (Duration: {duration_str})")
    
    # 1. Choose quality
    format_id = select_quality_menu(meta)
    
    # 2. Configure features
    features = select_features_menu(config)
    
    # 3. Ask for cut/trim
    start_sec = None
    end_sec = None
    
    if ffmpeg_path:
        cut_choice = input(f"\n{BOLD}Do you want to cut a specific section of this video? (y/N): {RESET}").strip().lower()
        if cut_choice in ['y', 'yes']:
            while True:
                try:
                    start_str = input(f"Enter Start Time (e.g. 00:00:10 or 10, press Enter for start): ").strip()
                    if start_str:
                        start_sec = parse_time_to_seconds(start_str)
                    
                    end_str = input(f"Enter End Time (e.g. 00:01:20 or 80, press Enter for end): ").strip()
                    if end_str:
                        end_sec = parse_time_to_seconds(end_str)
                        
                    if start_sec and start_sec > duration:
                        print_error("Start time exceeds video duration!")
                        continue
                    if end_sec and end_sec > duration:
                        print_error("End time exceeds video duration!")
                        continue
                    if start_sec and end_sec and start_sec >= end_sec:
                        print_error("Start time must be less than end time!")
                        continue
                    break
                except ValueError as e:
                    print_error(f"Invalid format: {e}. Try again.")
    else:
        print_warning("Video cutting disabled because FFMpeg binary is not loaded.")
        
    # Return queue task
    return {
        "url": url,
        "format_id": format_id,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "features": features,
        "title": meta.get("title")
    }

def handle_playlist(url, ytdlp_path, ffmpeg_path, config, playlist_meta):
    title = playlist_meta.get("title", "Unknown Playlist")
    entries = playlist_meta.get("entries", [])
    
    print_success(f"Playlist Found: {BOLD}{title}{RESET} ({len(entries)} items)")
    print(f"\n{GREEN}[1] {RESET}Download specific video from this playlist")
    print(f"{GREEN}[2] {RESET}Download ENTIRE playlist")
    
    choice = input(f"\n{BOLD}Choose option (1-2): {RESET}").strip()
    
    if choice == '1':
        print(f"\n{BOLD}{CYAN}Playlist entries:{RESET}")
        for i, entry in enumerate(entries[:30], 1):
            print(f" {i:<3}. {entry.get('title')}")
        if len(entries) > 30:
            print(f" ... and {len(entries) - 30} more videos.")
            
        while True:
            idx_input = input(f"\nSelect video number to download (1-{len(entries)}): ").strip()
            if idx_input.isdigit():
                idx = int(idx_input) - 1
                if 0 <= idx < len(entries):
                    selected_entry = entries[idx]
                    video_url = selected_entry.get("url") or selected_entry.get("webpage_url")
                    if video_url:
                        if not video_url.startswith("http"):
                            video_url = f"https://www.youtube.com/watch?v={selected_entry['id']}"
                        task = handle_single_video(video_url, ytdlp_path, ffmpeg_path, config)
                        if task:
                            return [task]
                        return []
                    else:
                        print_error("Could not resolve video URL.")
                        return []
            print_error("Invalid selection.")
            
    elif choice == '2':
        # Get template quality for the entire playlist
        first_entry = entries[0]
        first_url = first_entry.get("url") or first_entry.get("webpage_url")
        if first_url and not first_url.startswith("http"):
            first_url = f"https://www.youtube.com/watch?v={first_entry['id']}"
            
        try:
            sample_meta = fetch_metadata(first_url, ytdlp_path, ffmpeg_path)
            format_id = select_quality_menu(sample_meta)
            features = select_features_menu(config)
        except Exception as e:
            print_warning(f"Could not parse formats dynamically: {e}. Defaulting to standard best video/audio combination.")
            format_id = "bestvideo+bestaudio/best"
            features = config
            
        tasks = []
        for entry in entries:
            video_url = entry.get("url") or entry.get("webpage_url")
            if video_url and not video_url.startswith("http"):
                video_url = f"https://www.youtube.com/watch?v={entry['id']}"
            tasks.append({
                "url": video_url,
                "format_id": format_id,
                "start_sec": None,
                "end_sec": None,
                "features": features,
                "title": entry.get("title")
            })
        return tasks
    else:
        print_error("Invalid option selection.")
    return []
def check_for_ytdlp_update(ytdlp_path, force=False):
    local_version = None
    if os.path.exists(ytdlp_path):
        try:
            result = subprocess.run([ytdlp_path, "--version"], capture_output=True, text=True)
            local_version = result.stdout.strip()
        except Exception:
            pass

    print_status("Checking for yt-dlp updates...")
    latest_version = None
    url = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
    ctx = ssl._create_unverified_context()
    req = urllib.request.Request(
        url,
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    try:
        with urllib.request.urlopen(req, context=ctx) as response:
            data = json.loads(response.read().decode('utf-8'))
            latest_version = data.get("tag_name")
    except Exception as e:
        print_warning(f"Could not check for updates: {e}")
        return

    if not latest_version:
        return

    local_clean = re.sub(r'[^0-9.]', '', local_version) if local_version else "0"
    latest_clean = re.sub(r'[^0-9.]', '', latest_version)

    if force or (local_clean and latest_clean and latest_clean > local_clean):
        if not force:
            print_warning(f"New yt-dlp version found: {latest_version} (Local: {local_version or 'None'})")
            choice = input(f"{BOLD}Would you like to update yt-dlp now? (Y/n): {RESET}").strip().lower()
            if choice in ['n', 'no']:
                return
        
        print_status(f"Updating yt-dlp to {latest_version}...")
        download_url = "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
        try:
            temp_path = ytdlp_path + ".tmp"
            download_file_with_progress(download_url, temp_path, "yt-dlp update")
            if os.path.exists(ytdlp_path):
                os.remove(ytdlp_path)
            os.rename(temp_path, ytdlp_path)
            st = os.stat(ytdlp_path)
            os.chmod(ytdlp_path, st.st_mode | stat.S_IEXEC)
            print_success(f"Successfully updated yt-dlp to version {latest_version}!")
        except Exception as e:
            print_error(f"Failed to update yt-dlp: {e}")
    else:
        if force:
            print_success(f"yt-dlp is already up to date (Version: {local_version}).")
        else:
            print_status(f"yt-dlp is up to date (Version: {local_version}).")

def main():
    print(f"{BOLD}{GREEN}===================================================={RESET}")
    print(f"{BOLD}{GREEN}     YOUTUBE VIDEO DOWNLOAD (CLI)         {RESET}")
    print(f"{BOLD}{GREEN}===================================================={RESET}")
    
    # Initialize dependencies
    ytdlp_path = get_ytdlp_path()
    ffmpeg_path = get_ffmpeg_path()
    
    # Process update check if --update is passed
    if "--update" in sys.argv:
        check_for_ytdlp_update(ytdlp_path, force=True)
        return
        
    # Check for update automatically (non-force)
    check_for_ytdlp_update(ytdlp_path, force=False)
    
    # Load configuration
    config = load_config()
    
    urls = []
    if len(sys.argv) > 1:
        urls = sys.argv[1:]
    else:
        # Prompt for first URL
        first_url = input(f"\n{BOLD}Enter YouTube Video or Playlist URL: {RESET}").strip()
        if first_url:
            urls.append(first_url)
            
    if not urls:
        print_error("No URL provided. Exiting.")
        return
        
    download_queue = []
    
    # Process each URL to compile the queue
    for url in urls:
        try:
            print_status(f"Checking URL: {url}...")
            flat_meta = fetch_metadata(url, ytdlp_path, ffmpeg_path, flat=True)
            
            if flat_meta.get("_type") == "playlist":
                tasks = handle_playlist(url, ytdlp_path, ffmpeg_path, config, flat_meta)
                download_queue.extend(tasks)
            else:
                task = handle_single_video(url, ytdlp_path, ffmpeg_path, config)
                if task:
                    download_queue.append(task)
        except Exception as e:
            print_error(f"Failed to load metadata for {url}: {e}")
            
    # Support adding more items in interactive mode
    if len(sys.argv) == 1 and download_queue:
        while True:
            more = input(f"\n{BOLD}Do you want to add another URL to the queue? (y/N): {RESET}").strip().lower()
            if more in ['y', 'yes']:
                next_url = input(f"{BOLD}Enter YouTube Video or Playlist URL: {RESET}").strip()
                if next_url:
                    try:
                        print_status("Checking URL...")
                        flat_meta = fetch_metadata(next_url, ytdlp_path, ffmpeg_path, flat=True)
                        if flat_meta.get("_type") == "playlist":
                            tasks = handle_playlist(next_url, ytdlp_path, ffmpeg_path, config, flat_meta)
                            download_queue.extend(tasks)
                        else:
                            task = handle_single_video(next_url, ytdlp_path, ffmpeg_path, config)
                            if task:
                                download_queue.append(task)
                    except Exception as e:
                        print_error(f"Failed to load: {e}")
            else:
                break
                
    # Run sequential downloads
    total_downloads = len(download_queue)
    if total_downloads == 0:
        print_warning("No videos in the download queue. Exiting.")
        return
        
    print(f"\n{BOLD}{GREEN}=== Starting Download Queue ({total_downloads} videos) ==={RESET}")
    for idx, task in enumerate(download_queue, 1):
        print(f"\n{BOLD}{BLUE}[{idx}/{total_downloads}] Downloading: {task['title']}{RESET}")
        try:
            download_video(
                url=task["url"],
                format_id=task["format_id"],
                ytdlp_path=ytdlp_path,
                ffmpeg_path=ffmpeg_path,
                start_time=task["start_sec"],
                end_time=task["end_sec"],
                features=task["features"]
            )
        except Exception as e:
            print_error(f"Failed to download '{task['title']}': {e}")
            
    print(f"\n{BOLD}{GREEN}===================================================={RESET}")
    print(f"{BOLD}{GREEN}            DOWNLOAD COMPLETED!                 {RESET}")
    print(f"{BOLD}{GREEN}===================================================={RESET}")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{RED}[!] Process interrupted by user. Exiting.{RESET}")
