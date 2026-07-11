#!/usr/bin/env python3
import argparse
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
import platform
from datetime import datetime, timezone

try:
    from packaging.version import Version, InvalidVersion
except ImportError:
    Version = None
    InvalidVersion = Exception

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
YTDLP_BIN = os.path.join(BIN_DIR, "yt-dlp.exe" if platform.system().lower() == "windows" else "yt-dlp")
FFMPEG_BIN = os.path.join(BIN_DIR, "ffmpeg.exe" if platform.system().lower() == "windows" else "ffmpeg")
CONFIG_PATH = os.path.expanduser("~/.config/ytdl-cli/config.json")
HISTORY_PATH = os.path.expanduser("~/.config/ytdl-cli/history.json")
FFBINARIES_VERSION = "v4.4.1"

# Regex to parse yt-dlp progress
PROGRESS_REGEX = re.compile(
    r"\[download\]\s+([0-9.]+)%"
    r"(?:\s+of\s+(?:~\s*)?([0-9.]+)([a-zA-Z]+))?"
    r"(?:\s+at\s+([0-9.]+)([a-zA-Z]+/s))?"
    r"(?:\s+ETA\s+([0-9a-zA-Z:\-]+))?"
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

def parse_version_safe(version_str):
    """Parse a version string; returns None when parsing fails."""
    if not version_str:
        return None
    cleaned = version_str.strip().lstrip("v")
    if Version is not None:
        try:
            return Version(cleaned)
        except InvalidVersion:
            pass
    match = re.search(r"(\d+(?:\.\d+)*)", cleaned)
    if not match:
        return None
    parts = [int(p) for p in match.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)

def is_version_newer(latest_version, local_version):
    """Return True when latest_version is newer than local_version."""
    latest = parse_version_safe(latest_version)
    if latest is None:
        return False
    local = parse_version_safe(local_version)
    if local is None:
        return not local_version
    if isinstance(latest, tuple):
        return latest > local
    try:
        return latest > local
    except Exception:
        return False

def get_ffmpeg_platform_tag():
    """Map current OS/arch to ffbinaries platform tag."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return "linux-64"
        if machine in ("aarch64", "arm64"):
            return "linux-arm64"
        if machine in ("i386", "i686", "x86"):
            return "linux-32"
    elif system == "darwin":
        return "osx-64"
    elif system == "windows":
        if machine in ("x86_64", "amd64"):
            return "windows-64"
        return "windows-32"
    return None

def get_ffmpeg_download_url():
    tag = get_ffmpeg_platform_tag()
    if not tag:
        return None
    return (
        f"https://github.com/ffbinaries/ffbinaries-prebuilt/releases/download/"
        f"{FFBINARIES_VERSION}/ffmpeg-{FFBINARIES_VERSION.lstrip('v')}-{tag}.zip"
    )

def load_urls_from_file(file_path):
    """Read URLs from a text file, ignoring blank lines and # comments."""
    urls = []
    with open(file_path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            urls.append(line)
    return urls

def resolve_output_template(output_arg):
    """Turn a directory path into a yt-dlp output template."""
    if not output_arg:
        return None
    expanded = os.path.expanduser(output_arg)
    if "%(" in expanded:
        return expanded
    return os.path.join(expanded, "%(title)s.%(ext)s")

def load_history():
    if not os.path.exists(HISTORY_PATH):
        return []
    try:
        with open(HISTORY_PATH, "r", encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, list) else []
    except Exception:
        return []

def append_history(record):
    """Append a download record to the JSON history log."""
    history = load_history()
    history.append(record)
    try:
        os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
        with open(HISTORY_PATH, "w", encoding="utf-8") as handle:
            json.dump(history[-500:], handle, indent=2)
    except Exception as e:
        print_warning(f"Could not write download history: {e}")

def get_format_size_from_meta(meta, format_id):
    """Estimate bytes for a format selector using metadata formats list."""
    formats = meta.get("formats") or []
    format_map = {str(fmt.get("format_id")): fmt for fmt in formats if fmt.get("format_id") is not None}
    total = 0
    found = False

    for part in format_id.split("/")[0].split("+"):
        part = part.strip()
        if not part or part in ("best", "bestvideo", "bestaudio", "b"):
            continue
        fmt = format_map.get(part)
        if fmt:
            size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
            total += size
            found = True

    if found:
        return total

    # Fallback for best*/selector strings: use largest video+audio combo estimate.
    best_audio = 0
    best_video = 0
    for fmt in formats:
        size = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        if fmt.get("vcodec") not in (None, "none") and size > best_video:
            best_video = size
        if fmt.get("acodec") not in (None, "none") and fmt.get("vcodec") in (None, "none") and size > best_audio:
            best_audio = size

    fid_lower = format_id.split("/")[0].lower()
    if "bestvideo" in fid_lower or ("best" in fid_lower and "bestaudio" not in fid_lower) or "video" in fid_lower:
        return best_video + best_audio
    if "audio" in fid_lower:
        return best_audio
    return 0

def estimate_playlist_download(sample_meta, format_id, entries):
    """Estimate total playlist size and duration before downloading."""
    per_video_size = get_format_size_from_meta(sample_meta, format_id)
    entry_count = len(entries)
    known_sizes = 0
    estimated_total = 0
    known_durations = 0
    total_duration = 0

    for entry in entries:
        if not entry:
            continue
        duration = entry.get("duration") or 0
        if duration:
            total_duration += duration
            known_durations += 1
            
        size = entry.get("filesize") or entry.get("filesize_approx")
        if size:
            estimated_total += size
            known_sizes += 1

    if known_sizes > 0:
        avg_size = estimated_total / known_sizes
        missing_count = entry_count - known_sizes
        estimated_total += avg_size * missing_count
    elif per_video_size > 0:
        estimated_total = per_video_size * entry_count
        known_sizes = 1

    return {
        "entry_count": entry_count,
        "per_video_size": per_video_size,
        "estimated_total_size": estimated_total,
        "total_duration": total_duration,
        "known_durations": known_durations,
        "known_sizes": known_sizes,
    }

def format_duration(seconds):
    if not seconds:
        return "Unknown"
    return time.strftime("%H:%M:%S", time.gmtime(seconds))

def resolve_format_id(args, config):
    """Resolve format from CLI flags and config defaults."""
    if args.audio_only:
        return "bestaudio/best"
    if args.format_id:
        return args.format_id
    return config.get("default_format", "bestvideo+bestaudio/best")

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Interactive YouTube video downloader powered by yt-dlp.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  ytdl-cli.py https://youtu.be/VIDEO_ID\n"
            "  ytdl-cli.py --file urls.txt --no-interactive -y\n"
            "  ytdl-cli.py URL --format bestvideo+bestaudio -o ~/Videos\n"
            "  ytdl-cli.py --audio-only -o ~/Music URL\n"
            "  ytdl-cli.py --update"
        ),
    )
    parser.add_argument("urls", nargs="*", help="YouTube video or playlist URLs")
    parser.add_argument("-f", "--file", dest="url_file", help="Read URLs from a text file (one per line, # for comments)")
    parser.add_argument("--update", action="store_true", help="Update yt-dlp to the latest release and exit")
    parser.add_argument("-F", "--format", dest="format_id", help="yt-dlp format selector (e.g. bestvideo+bestaudio, 137+140)")
    parser.add_argument("--audio-only", action="store_true", help="Download best available audio only")
    parser.add_argument("-o", "--output", help="Output directory or yt-dlp output template")
    parser.add_argument("--no-interactive", action="store_true", help="Skip interactive menus; use flags and saved defaults")
    parser.add_argument("-y", "--yes", action="store_true", help="Auto-confirm prompts (updates, playlist downloads)")
    return parser.parse_args(argv)

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
        self.max_len = len(message) + 20

    def _spin(self):
        idx = 0
        while not self.stop_running.is_set():
            msg = f"\r{CYAN}{self.spinner_chars[idx]} {self.message}{RESET}"
            sys.stdout.write(msg)
            sys.stdout.flush()
            clean_msg_len = len(re.sub(r'\033\[[0-9;]*m', '', msg))
            if clean_msg_len > self.max_len:
                self.max_len = clean_msg_len
            idx = (idx + 1) % len(self.spinner_chars)
            time.sleep(0.08)

    def start(self):
        self.max_len = len(self.message) + 20
        self.stop_running.clear()
        self.thread = threading.Thread(target=self._spin)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        if self.thread:
            self.stop_running.set()
            self.thread.join()
            sys.stdout.write("\r" + " " * self.max_len + "\r")
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
    url = (
        "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
        if platform.system().lower() == "windows"
        else "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
    )
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

    platform_tag = get_ffmpeg_platform_tag()
    if not platform_tag:
        print_warning(
            f"ffmpeg not found and no prebuilt binary is available for "
            f"{platform.system()}/{platform.machine()}."
        )
        print_warning("Downloading will continue without quality merging or slicing capabilities.")
        return None

    url = get_ffmpeg_download_url()
    print_warning(
        f"ffmpeg not found on system. Downloading static binary for {platform_tag}..."
    )
    zip_path = os.path.join(BIN_DIR, "ffmpeg.zip")
    try:
        download_file_with_progress(url, zip_path, "ffmpeg")
        print_status("Extracting ffmpeg...")
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(BIN_DIR)
        os.remove(zip_path)

        if not os.path.exists(FFMPEG_BIN):
            ffmpeg_bin = None
            for name in ("ffmpeg", "ffmpeg.exe"):
                candidate = os.path.join(BIN_DIR, name)
                if os.path.exists(candidate):
                    ffmpeg_bin = candidate
                    break
            if not ffmpeg_bin:
                raise FileNotFoundError("ffmpeg binary not found after extraction")
        else:
            ffmpeg_bin = FFMPEG_BIN

        st = os.stat(ffmpeg_bin)
        os.chmod(ffmpeg_bin, st.st_mode | stat.S_IEXEC)
        print_success("ffmpeg setup complete.")
        return ffmpeg_bin
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
        "embed_chapters": True,
        "default_format": "bestvideo+bestaudio/best",
        "output_template": None,
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
def interactive_menu(title, options, multi_select=False, default_selections=None, non_interactive=False):
    # Non-TTY / Non-interactive fallback
    if non_interactive or not sys.stdin.isatty() or not tty or not termios:
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
    try:
        return float(time_str)
    except ValueError:
        pass
    
    parts = time_str.split(":")
    try:
        if len(parts) == 2: # mm:ss
            return float(parts[0]) * 60 + float(parts[1])
        elif len(parts) == 3: # hh:mm:ss
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    
    raise ValueError("Invalid time format. Use seconds, mm:ss, or hh:mm:ss")

def select_quality_menu(json_data, non_interactive=False, preset_format_id=None, is_playlist=False):
    if preset_format_id:
        print_status(f"Using format: {preset_format_id}")
        return preset_format_id

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
        if is_playlist:
            label = f"{c['label']:<20} | V-Codec: {v_codec:<10} | A-Codec: {a_codec}"
        else:
            label = f"{c['label']:<20} | Est: {size_str:<10} | V-Codec: {v_codec:<10} | A-Codec: {a_codec}"
        menu_options.append({"label": label, "value": c["format_id"]})
        
    title = f"Select Resolution Quality for: {json_data.get('title')}"
    selected_idx = interactive_menu(title, menu_options, non_interactive=non_interactive)
    
    chosen_val = menu_options[selected_idx]["value"]
    if chosen_val == "CUSTOM":
        custom_id = input(f"Enter custom format ID combination (e.g. 137+140): ").strip()
        return custom_id
    return chosen_val

def select_features_menu(config, non_interactive=False):
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
    
    selections = interactive_menu(
        "Configure Advanced Download Options",
        features_list,
        multi_select=True,
        default_selections=default_selections,
        non_interactive=non_interactive,
    )
    
    active_features = {}
    for i, feature in enumerate(features_list[:-1]):
        active_features[feature["key"]] = selections[i]
        
    save_defaults = selections[-1]
    if save_defaults:
        full_config = {**config, **active_features}
        save_config(full_config)
        
    return active_features

def download_video(url, format_id, ytdlp_path, ffmpeg_path=None, start_time=None, end_time=None, features=None, output_template=None):
    """Run yt-dlp download. Returns (success, file_size)."""
    cmd = [ytdlp_path, "-f", format_id, "--newline", "--no-playlist", url]

    if output_template:
        cmd.extend(["-o", output_template])
    
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
    preferred_format = format_id.split("/")[0]
    total_streams = len(preferred_format.split("+"))
    error_lines = []
    final_path = None
    
    for line in process.stdout:
        clean_line = line.strip()
        
        if "Destination:" in clean_line:
            stream_count += 1
            dest_path = clean_line.split("Destination:")[1].strip()
            if not dest_path.endswith(".part"):
                final_path = dest_path
            continue

        if clean_line.startswith("ERROR:") or " error:" in clean_line.lower():
            error_lines.append(clean_line)
            sys.stdout.write("\n")
            print_error(clean_line.replace("ERROR:", "").strip() or clean_line)
            continue
            
        match = PROGRESS_REGEX.search(clean_line)
        if match:
            percent = float(match.group(1))
            total = f"{match.group(2)}{match.group(3)}" if match.group(2) and match.group(3) else "Unknown"
            speed = f"{match.group(4)}{match.group(5)}" if match.group(4) and match.group(5) else "Unknown"
            eta = match.group(6) if match.group(6) else "Unknown"
            
            stream_str = f"Stream {stream_count}/{total_streams}" if total_streams > 1 else "Downloading"
            draw_progress_bar(percent, total, speed, eta, stream_str)
        elif "Merging formats" in clean_line or "Merging formats into" in clean_line:
            sys.stdout.write("\n")
            print_status("Merging streams into final file...", GREEN)
            m = re.search(r'Merging formats into\s+"([^"]+)"', clean_line)
            if m:
                final_path = m.group(1)
        elif "Extracting video section" in clean_line or "Converting" in clean_line:
            sys.stdout.write("\n")
            print_status("Applying slice cuts and formatting...", GREEN)
        elif "SponsorBlock" in clean_line:
            sys.stdout.write("\n")
            print_status(f"SponsorBlock: {clean_line.replace('[SponsorBlock] ', '')}", GREEN)
        elif clean_line.startswith("[download]") and "100% of" in clean_line:
            # One stream finished; final success still depends on merge/post-processing.
            sys.stdout.write("\n")
            stream_label = f"stream {stream_count}/{total_streams}" if total_streams > 1 else "stream"
            print_status(f"{stream_label.capitalize()} download finished. Finalizing...", CYAN)
            
    process.wait()
    
    if process.returncode in (0, 1):
        sys.stdout.write("\n")
        if process.returncode == 0:
            print_success("Download finished successfully.")
        else:
            print_warning("Download finished with warnings (exit code 1).")
        
        file_size = 0
        if final_path:
            full_path = os.path.abspath(final_path)
            if os.path.exists(full_path):
                file_size = os.path.getsize(full_path)
        return True, file_size

    sys.stdout.write("\n")
    if error_lines:
        print_error(f"Download failed: {error_lines[-1].replace('ERROR:', '').strip()}")
    else:
        print_error(f"Download failed with exit code: {process.returncode}")
    return False, 0

def print_queue_summary(success_count, failed_count, total_downloads):
    """Print an accurate final status — never claim success when anything failed."""
    print(f"\n{BOLD}{'=' * 52}{RESET}")
    if total_downloads == 0:
        print(f"{BOLD}{YELLOW}            NO DOWNLOADS PERFORMED               {RESET}")
    elif failed_count == 0:
        print(f"{BOLD}{GREEN}        DOWNLOADS COMPLETED SUCCESSFULLY     {RESET}")
        print(f"{DIM}({success_count}/{total_downloads} succeeded){RESET}")
    elif success_count == 0:
        print(f"{BOLD}{RED}            DOWNLOADS FAILED                 {RESET}")
        print(f"{DIM}({failed_count}/{total_downloads} failed){RESET}")
    else:
        print(f"{BOLD}{YELLOW}     DOWNLOAD QUEUE FINISHED WITH ERRORS        {RESET}")
        print(f"{DIM}({success_count} succeeded, {failed_count} failed out of {total_downloads}){RESET}")
    print(f"{BOLD}{'=' * 52}{RESET}")

def handle_single_video(url, ytdlp_path, ffmpeg_path, config, args):
    try:
        meta = fetch_metadata(url, ytdlp_path, ffmpeg_path)
    except Exception as e:
        print_error(f"Error fetching metadata: {e}")
        return None
        
    duration = meta.get("duration", 0)
    duration_str = time.strftime('%H:%M:%S', time.gmtime(duration))
    print_success(f"Successfully loaded: {BOLD}{meta.get('title')}{RESET} (Duration: {duration_str})")
    
    preset_format = resolve_format_id(args, config) if args.no_interactive or args.format_id or args.audio_only else None

    # 1. Choose quality
    format_id = select_quality_menu(
        meta,
        non_interactive=args.no_interactive,
        preset_format_id=preset_format,
        is_playlist=False
    )
    
    # 2. Configure features
    features = select_features_menu(config, non_interactive=args.no_interactive)
    
    # 3. Ask for cut/trim
    start_sec = None
    end_sec = None
    
    if not ffmpeg_path:
        print_warning("Video cutting disabled because FFMpeg binary is not loaded.")
    elif not args.no_interactive:
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
        
    # Return queue task
    return {
        "url": url,
        "format_id": format_id,
        "start_sec": start_sec,
        "end_sec": end_sec,
        "features": features,
        "title": meta.get("title")
    }

def get_entry_url(entry):
    if not entry:
        return None
    url = entry.get("url") or entry.get("webpage_url")
    if url and url.startswith("http"):
        return url
    video_id = entry.get("id") or entry.get("url", "").split("=")[-1]
    if video_id:
        return f"https://www.youtube.com/watch?v={video_id}"
    return None

def handle_playlist(url, ytdlp_path, ffmpeg_path, config, playlist_meta, args):
    title = playlist_meta.get("title", "Unknown Playlist")
    entries = [entry for entry in (playlist_meta.get("entries") or []) if entry]
    
    print_success(f"Playlist Found: {BOLD}{title}{RESET} ({len(entries)} items)")

    if args.no_interactive:
        choice = "2"
        print_status("Non-interactive mode: downloading entire playlist.")
    else:
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
                    video_url = get_entry_url(selected_entry)
                    if video_url:
                        task = handle_single_video(video_url, ytdlp_path, ffmpeg_path, config, args)
                        if task:
                            return [task]
                        return []
                    else:
                        print_error("Could not resolve video URL.")
                        return []
            print_error("Invalid selection.")
            
    elif choice == '2':
        if not entries:
            print_error("Playlist has no downloadable entries.")
            return []

        # Get template quality for the entire playlist
        first_entry = entries[0]
        first_url = get_entry_url(first_entry)
        if not first_url:
            print_error("Could not resolve URL for the first playlist entry.")
            return []
            
        try:
            sample_meta = fetch_metadata(first_url, ytdlp_path, ffmpeg_path)
            preset_format = resolve_format_id(args, config) if args.no_interactive or args.format_id or args.audio_only else None
            format_id = select_quality_menu(
                sample_meta,
                non_interactive=args.no_interactive,
                preset_format_id=preset_format,
                is_playlist=True
            )
            features = select_features_menu(config, non_interactive=args.no_interactive)

            if not args.no_interactive:
                if not args.yes:
                    confirm = input(f"\n{BOLD}Proceed with downloading all {len(entries)} videos? (Y/n): {RESET}").strip().lower()
                    if confirm in ("n", "no"):
                        print_warning("Playlist download cancelled.")
                        return []
        except Exception as e:
            print_warning(f"Could not parse formats dynamically: {e}. Defaulting to standard best video/audio combination.")
            format_id = resolve_format_id(args, config)
            features = {k: config[k] for k in ("sponsorblock", "embed_metadata", "embed_thumbnail", "embed_subtitles", "embed_chapters") if k in config}
            
        # Quality fallback logic
        if "/" not in format_id and format_id != "best":
            task_format = f"{format_id}/bestvideo+bestaudio/best"
        else:
            task_format = format_id

        tasks = []
        for entry in entries:
            video_url = get_entry_url(entry)
            if not video_url:
                continue
            tasks.append({
                "url": video_url,
                "format_id": task_format,
                "start_sec": None,
                "end_sec": None,
                "features": features,
                "title": entry.get("title"),
                "duration": entry.get("duration") or 0
            })
        return tasks
    else:
        print_error("Invalid option selection.")
    return []
def check_for_ytdlp_update(ytdlp_path, force=False, auto_yes=False):
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

    needs_update = force or is_version_newer(latest_version, local_version)

    if needs_update:
        if not force and not auto_yes:
            print_warning(f"New yt-dlp version found: {latest_version} (Local: {local_version or 'None'})")
            choice = input(f"{BOLD}Would you like to update yt-dlp now? (Y/n): {RESET}").strip().lower()
            if choice in ['n', 'no']:
                return
        
        print_status(f"Updating yt-dlp to {latest_version}...")
        download_url = (
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp.exe"
            if platform.system().lower() == "windows"
            else "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp"
        )
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

def main(argv=None):
    args = parse_args(argv)

    print(f"{BOLD}{GREEN}===================================================={RESET}")
    print(f"{BOLD}{GREEN}     YOUTUBE VIDEO DOWNLOAD (CLI)         {RESET}")
    print(f"{BOLD}{GREEN}===================================================={RESET}")
    
    # Initialize dependencies
    ytdlp_path = get_ytdlp_path()
    ffmpeg_path = get_ffmpeg_path()
    
    if args.update:
        check_for_ytdlp_update(ytdlp_path, force=True, auto_yes=args.yes)
        return
        
    if not args.no_interactive:
        check_for_ytdlp_update(ytdlp_path, force=False, auto_yes=args.yes)
    
    config = load_config()
    output_template = resolve_output_template(args.output or config.get("output_template"))
    urls = list(args.urls)

    if args.url_file:
        try:
            file_urls = load_urls_from_file(args.url_file)
            urls.extend(file_urls)
            print_status(f"Loaded {len(file_urls)} URL(s) from {args.url_file}")
        except OSError as e:
            print_error(f"Could not read URL file '{args.url_file}': {e}")
            sys.exit(1)

    if not urls and not args.no_interactive:
        first_url = input(f"\n{BOLD}Enter YouTube Video or Playlist URL: {RESET}").strip()
        if first_url:
            urls.append(first_url)
            
    if not urls:
        print_error("No URL provided. Exiting.")
        return
        
    download_queue = []
    is_playlist_download = False
    
    for url in urls:
        try:
            print_status(f"Checking URL: {url}...")
            flat_meta = fetch_metadata(url, ytdlp_path, ffmpeg_path, flat=True)
            
            if flat_meta.get("_type") == "playlist":
                is_playlist_download = True
                tasks = handle_playlist(url, ytdlp_path, ffmpeg_path, config, flat_meta, args)
                download_queue.extend(tasks)
            else:
                task = handle_single_video(url, ytdlp_path, ffmpeg_path, config, args)
                if task:
                    download_queue.append(task)
        except Exception as e:
            print_error(f"Failed to load metadata for {url}: {e}")
            
    if not args.no_interactive and not args.url_file and len(args.urls) == 0 and download_queue:
        while True:
            more = input(f"\n{BOLD}Do you want to add another URL to the queue? (y/N): {RESET}").strip().lower()
            if more in ['y', 'yes']:
                next_url = input(f"{BOLD}Enter YouTube Video or Playlist URL: {RESET}").strip()
                if next_url:
                    try:
                        print_status("Checking URL...")
                        flat_meta = fetch_metadata(next_url, ytdlp_path, ffmpeg_path, flat=True)
                        if flat_meta.get("_type") == "playlist":
                            is_playlist_download = True
                            tasks = handle_playlist(next_url, ytdlp_path, ffmpeg_path, config, flat_meta, args)
                            download_queue.extend(tasks)
                        else:
                            task = handle_single_video(next_url, ytdlp_path, ffmpeg_path, config, args)
                            if task:
                                download_queue.append(task)
                    except Exception as e:
                        print_error(f"Failed to load: {e}")
            else:
                break
                
    total_downloads = len(download_queue)
    if total_downloads == 0:
        print_warning("No videos in the download queue. Exiting.")
        return

    # Folder routing for playlist download
    if is_playlist_download:
        playlist_dir = "Ytdl-Playlist"
        base_out = args.output or config.get("output_template")
        if base_out:
            if "%(" in base_out:
                if os.path.isdir(os.path.expanduser(base_out)) or not "%(" in base_out:
                    output_dir = os.path.join(base_out, playlist_dir)
                    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
                else:
                    dir_part, file_part = os.path.split(base_out)
                    output_template = os.path.join(dir_part, playlist_dir, file_part)
            else:
                output_dir = os.path.join(base_out, playlist_dir)
                output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
        else:
            output_dir = os.path.join(os.getcwd(), playlist_dir)
            output_template = os.path.join(output_dir, "%(title)s.%(ext)s")
        
    print(f"\n{BOLD}{GREEN}=== Starting Download Queue ({total_downloads} videos) ==={RESET}")
    success_count = 0
    failed_count = 0
    downloaded_sizes = []
    downloaded_durations = []

    for idx, task in enumerate(download_queue, 1):
        print(f"\n{BOLD}{BLUE}[{idx}/{total_downloads}] Downloading: {task['title']}{RESET}")
        success = False
        file_size = 0
        try:
            success, file_size = download_video(
                url=task["url"],
                format_id=task["format_id"],
                ytdlp_path=ytdlp_path,
                ffmpeg_path=ffmpeg_path,
                start_time=task["start_sec"],
                end_time=task["end_sec"],
                features=task["features"],
                output_template=output_template,
            )
            if success:
                success_count += 1
                if file_size > 0:
                    downloaded_sizes.append(file_size)
                if task.get("duration"):
                    downloaded_durations.append(task["duration"])
            else:
                failed_count += 1
        except Exception as e:
            failed_count += 1
            print_error(f"Failed to download '{task['title']}': {e}")

        append_history({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "url": task["url"],
            "title": task.get("title"),
            "format_id": task["format_id"],
            "success": success,
            "output_template": output_template,
        })

    # Playlist final summary card
    if is_playlist_download:
        total_size = sum(downloaded_sizes)
        total_duration = sum(downloaded_durations)
        avg_size = total_size / len(downloaded_sizes) if downloaded_sizes else 0
        
        duration_str = format_duration(total_duration)
        size_str = format_size(total_size)
        avg_size_str = format_size(avg_size)
        
        print(f"\n{BOLD}{GREEN}{'=' * 52}{RESET}")
        print(f"{BOLD}{GREEN}             PLAYLIST DOWNLOAD SUMMARY              {RESET}")
        print(f"{BOLD}{GREEN}{'=' * 52}{RESET}")
        print(f"  Videos Downloaded:  {BOLD}{success_count} / {total_downloads}{RESET}")
        print(f"  Total Duration:     {BOLD}{duration_str}{RESET}")
        print(f"  Total Size:         {BOLD}{size_str}{RESET}")
        print(f"  Average Video Size: {BOLD}{avg_size_str}{RESET}")
        print(f"{BOLD}{GREEN}{'=' * 52}{RESET}")

    print_queue_summary(success_count, failed_count, total_downloads)
    print_status(f"Download history saved to: {HISTORY_PATH}")
    if failed_count > 0:
        sys.exit(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print(f"\n{RED}[!] Process interrupted by user. Exiting.{RESET}")
