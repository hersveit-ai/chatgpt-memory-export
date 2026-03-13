#!/usr/bin/env python3
"""
chatgpt-memory-export -- Download and convert all your ChatGPT conversations.

Connects to Chrome via CDP (Chrome DevTools Protocol), navigates ChatGPT's UI
to discover and download every conversation, then converts them to clean
markdown files for archival, search, or ingestion into other AI systems.

Usage:
    chatgpt-export scan                  Scan sidebar for all conversations
    chatgpt-export download              Download conversations (parallel tabs)
    chatgpt-export convert               Convert raw JSON to readable markdown
    chatgpt-export status                Show progress dashboard
    chatgpt-export run                   Full pipeline: scan -> download -> convert

Requirements:
    - Google Chrome installed
    - Close Chrome before running scan/download
    - Python 3.10+, playwright, rich (optional)
"""

__version__ = "1.0.0"

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

def _auto_install(packages: list[str]):
    """Install packages via pip. Returns True on success."""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", *packages],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except subprocess.CalledProcessError:
        return False


def _ensure_dependencies():
    """Auto-install missing dependencies on first run."""
    missing = []

    try:
        import rich  # noqa: F401
    except ImportError:
        missing.append("rich")

    try:
        import playwright  # noqa: F401
    except ImportError:
        missing.append("playwright")

    if not missing:
        return

    print(f"First run -- installing dependencies: {', '.join(missing)}...")
    if not _auto_install(missing):
        print(f"\nFailed to auto-install. Run manually:")
        print(f"  pip install {' '.join(missing)}")
        if "playwright" in missing:
            print("  python -m playwright install chromium")
        sys.exit(1)

    if "playwright" in missing:
        print("Setting up Playwright browsers...")
        try:
            subprocess.check_call(
                [sys.executable, "-m", "playwright", "install", "chromium"],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError:
            print("\nFailed to install browsers. Run manually:")
            print("  python -m playwright install chromium")
            sys.exit(1)

    print("Ready.\n")


_ensure_dependencies()

try:
    from rich.console import Console
    from rich.progress import (
        Progress,
        SpinnerColumn,
        BarColumn,
        TextColumn,
        TimeRemainingColumn,
    )
    from rich.table import Table

    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------------
# DOM selectors -- update these if ChatGPT's frontend changes
# ---------------------------------------------------------------------------
SEL_MESSAGE = "[data-message-author-role]"
SEL_SIDEBAR_LINK = 'nav a[href^="/c/"]'


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
@dataclass
class Config:
    output_dir: Path = Path(".")
    chrome_path: str = ""
    profile_dir: Path = Path(".chrome_profile")
    debug_port: int = 9222
    parallel: int = 4
    load_timeout: int = 20

    @property
    def raw_dir(self) -> Path:
        return self.output_dir / "raw_conversations"

    @property
    def md_dir(self) -> Path:
        return self.output_dir / "memory_export"

    @property
    def index_path(self) -> Path:
        return self.raw_dir / "_index.json"

    @property
    def failures_path(self) -> Path:
        return self.raw_dir / "_failures.json"

    def ensure_dirs(self):
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.md_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Console output helpers
# ---------------------------------------------------------------------------
console = Console() if HAS_RICH else None


def _safe(msg):
    """Ensure string is printable on limited encodings like Windows cp1252."""
    return msg.encode("ascii", "replace").decode() if isinstance(msg, str) else msg


def out(msg, style=None):
    try:
        if console:
            console.print(msg, style=style)
        else:
            print(_safe(msg))
    except UnicodeEncodeError:
        print(_safe(msg))


def header(text):
    out(f"\n  {text}", style="bold cyan")
    out(f"  {'-' * len(text)}", style="dim")


def success(text):
    out(f"  [+] {text}", style="bold green")


def warn(text):
    out(f"  [!] {text}", style="bold yellow")


def error(text):
    out(f"  [-] {text}", style="bold red")


def info(text):
    out(f"  {text}", style="dim")


# ---------------------------------------------------------------------------
# Chrome management
# ---------------------------------------------------------------------------
CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def find_chrome() -> str | None:
    for path in CHROME_PATHS:
        if Path(path).exists():
            return path
    for name in ("google-chrome", "chromium-browser", "chromium"):
        found = shutil.which(name)
        if found:
            return found
    return None


def launch_chrome(cfg: Config):
    chrome = cfg.chrome_path or find_chrome()
    if not chrome:
        error("Chrome not found. Install Chrome or use --chrome-path.")
        sys.exit(1)

    info(f"Chrome: {chrome}")
    info(f"Profile: {cfg.profile_dir}")

    return subprocess.Popen([
        chrome,
        f"--remote-debugging-port={cfg.debug_port}",
        f"--user-data-dir={cfg.profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "https://chatgpt.com",
    ])


def connect_cdp(pw, cfg: Config, chrome_proc):
    for attempt in range(8):
        try:
            return pw.chromium.connect_over_cdp(f"http://127.0.0.1:{cfg.debug_port}")
        except Exception:
            if attempt < 7:
                time.sleep(3)
    error("Cannot connect to Chrome. Make sure Chrome is fully closed before running.")
    chrome_proc.terminate()
    sys.exit(1)


def find_chatgpt_page(browser, timeout=60):
    for _ in range(timeout // 3):
        for ctx in browser.contexts:
            for pg in ctx.pages:
                if "chatgpt.com" in pg.url:
                    return pg
        time.sleep(3)
    return None


# ---------------------------------------------------------------------------
# DOM extraction
# ---------------------------------------------------------------------------
def extract_messages(page) -> list[dict]:
    return page.evaluate(
        """() => {
            const msgs = [];
            for (const el of document.querySelectorAll('"""
        + SEL_MESSAGE
        + """')) {
                const role = el.getAttribute('data-message-author-role');
                const text = el.innerText.trim();
                if (text) msgs.push({ role, text });
            }
            return msgs;
        }"""
    )


def get_sidebar_links(page) -> list[dict]:
    return page.evaluate(
        """() => {
            const links = [];
            for (const a of document.querySelectorAll('"""
        + SEL_SIDEBAR_LINK
        + """')) {
                links.push({
                    url: a.href,
                    title: a.innerText.trim() || 'Untitled',
                    id: a.getAttribute('href').replace('/c/', '')
                });
            }
            return links;
        }"""
    )


def wait_for_full_load(page, timeout=20) -> list[dict]:
    """Poll until message count stabilizes, scrolling to trigger lazy loads."""
    prev = 0
    stable = 0
    for _ in range(timeout):
        try:
            msgs = extract_messages(page)
            count = len(msgs)
            if count > 0 and count == prev:
                stable += 1
                if stable >= 2:
                    return msgs
            else:
                stable = 0
            prev = count
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            pass
        time.sleep(1)
    try:
        return extract_messages(page)
    except Exception:
        return []


def make_filepath(raw_dir: Path, title: str, convo_id: str) -> Path:
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title)[:80]
    return raw_dir / f"{safe}_{convo_id[:8]}.json"


def _scroll_sidebar(page):
    page.evaluate(
        """() => {
            const nav = document.querySelector('nav');
            if (nav) {
                const s = nav.closest('[class*="overflow"]') || nav.parentElement;
                s.scrollTop = s.scrollHeight;
            }
        }"""
    )


# ---------------------------------------------------------------------------
# SCAN
# ---------------------------------------------------------------------------
def cmd_scan(cfg: Config):
    header("Scanning ChatGPT sidebar")
    cfg.ensure_dirs()

    chrome_proc = launch_chrome(cfg)
    info("Waiting for Chrome...")
    time.sleep(8)

    with sync_playwright() as pw:
        browser = connect_cdp(pw, cfg, chrome_proc)
        page = find_chatgpt_page(browser)

        if not page:
            error("No ChatGPT tab found. Log in to ChatGPT in the browser window.")
            browser.close()
            chrome_proc.terminate()
            return

        info(f"Connected: {page.url}")
        time.sleep(5)

        links = get_sidebar_links(page)
        info(f"Found {len(links)} initially")

        prev_count = 0
        stale = 0

        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=30),
                TextColumn("{task.completed} conversations"),
                console=console,
            ) as progress:
                task = progress.add_task("Scrolling sidebar...", total=None)
                for _ in range(300):
                    _scroll_sidebar(page)
                    time.sleep(1)
                    links = get_sidebar_links(page)
                    count = len(links)
                    progress.update(task, completed=count)
                    if count > prev_count:
                        prev_count = count
                        stale = 0
                    else:
                        stale += 1
                    if stale >= 5:
                        break
        else:
            for _ in range(300):
                _scroll_sidebar(page)
                time.sleep(1)
                links = get_sidebar_links(page)
                count = len(links)
                if count > prev_count:
                    info(f"  {count} conversations...")
                    prev_count = count
                    stale = 0
                else:
                    stale += 1
                if stale >= 5:
                    break

        # Deduplicate
        seen = set()
        unique = []
        for link in links:
            if link["id"] not in seen:
                seen.add(link["id"])
                unique.append(link)

        cfg.index_path.write_text(
            json.dumps(unique, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        success(f"Indexed {len(unique)} conversations -> {cfg.index_path}")
        browser.close()


# ---------------------------------------------------------------------------
# DOWNLOAD
# ---------------------------------------------------------------------------
def cmd_download(cfg: Config, retry_failed=False):
    header("Downloading conversations")
    cfg.ensure_dirs()

    if not cfg.index_path.exists():
        error("No index found. Run 'scan' first.")
        return

    links = json.loads(cfg.index_path.read_text(encoding="utf-8"))

    failures = {}
    if cfg.failures_path.exists():
        failures = json.loads(cfg.failures_path.read_text(encoding="utf-8"))

    todo, done = [], 0
    for link in links:
        fp = make_filepath(cfg.raw_dir, link["title"], link["id"])
        if fp.exists() and not (retry_failed and link["id"] in failures):
            done += 1
        else:
            todo.append(link)

    if not todo:
        success(f"All {len(links)} conversations already downloaded!")
        return

    total = len(links)
    info(f"{total} total | {done} done | {len(todo)} remaining")
    est = (len(todo) / cfg.parallel) * 12 / 60
    info(f"Estimated time: ~{est:.0f} min (parallel={cfg.parallel})")

    chrome_proc = launch_chrome(cfg)
    info("Waiting for Chrome...")
    time.sleep(8)

    with sync_playwright() as pw:
        browser = connect_cdp(pw, cfg, chrome_proc)
        page = find_chatgpt_page(browser)

        if not page:
            error("No ChatGPT tab found.")
            browser.close()
            chrome_proc.terminate()
            return

        info(f"Connected: {page.url}")
        time.sleep(3)

        context = browser.contexts[0]
        downloaded, failed = done, 0
        new_failures = dict(failures)
        start_time = time.time()

        def _process_batch(batch, progress=None, task=None):
            nonlocal downloaded, failed

            tabs = []
            for link in batch:
                tab = context.new_page()
                try:
                    tab.goto(link["url"], timeout=30000, wait_until="domcontentloaded")
                except Exception:
                    pass
                tabs.append((tab, link))

            time.sleep(5)

            for tab, link in tabs:
                msgs = wait_for_full_load(tab, cfg.load_timeout)
                fp = make_filepath(cfg.raw_dir, link["title"], link["id"])

                if msgs:
                    data = {
                        "id": link["id"],
                        "title": link["title"],
                        "url": link["url"],
                        "messages": msgs,
                    }
                    fp.write_text(
                        json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
                    )
                    downloaded += 1
                    new_failures.pop(link["id"], None)
                else:
                    failed += 1
                    new_failures[link["id"]] = {
                        "title": link["title"],
                        "url": link["url"],
                        "time": datetime.now().isoformat(),
                    }

                if progress and task is not None:
                    progress.advance(task)
                elif not progress:
                    title_safe = _safe(link["title"][:40])
                    count = len(msgs) if msgs else 0
                    print(f"  [{downloaded}/{total}] {title_safe} ({count} msgs)")

            for tab, _ in tabs:
                try:
                    tab.close()
                except Exception:
                    pass

        if HAS_RICH:
            with Progress(
                SpinnerColumn(),
                TextColumn("[bold blue]{task.description}"),
                BarColumn(bar_width=40),
                TextColumn("{task.completed}/{task.total}"),
                TimeRemainingColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("Downloading", total=len(todo))
                for i in range(0, len(todo), cfg.parallel):
                    _process_batch(todo[i : i + cfg.parallel], progress, task)
                    time.sleep(1)
        else:
            for i in range(0, len(todo), cfg.parallel):
                _process_batch(todo[i : i + cfg.parallel])
                time.sleep(1)

        cfg.failures_path.write_text(
            json.dumps(new_failures, indent=2, ensure_ascii=False), encoding="utf-8"
        )

        elapsed = time.time() - start_time
        success(f"Downloaded {downloaded}/{total} ({failed} failed) in {elapsed/60:.1f} min")
        browser.close()


# ---------------------------------------------------------------------------
# CONVERT
# ---------------------------------------------------------------------------
def cmd_convert(cfg: Config, force=False):
    header("Converting to markdown")
    cfg.ensure_dirs()

    json_files = sorted(cfg.raw_dir.glob("*.json"))
    json_files = [f for f in json_files if not f.name.startswith("_")]

    if not json_files:
        error(f"No conversation files in {cfg.raw_dir}. Run 'download' first.")
        return

    index_entries = []
    converted, skipped = 0, 0

    def _process(filepath):
        nonlocal converted, skipped
        result = _convert_one(filepath, cfg)
        if result:
            index_entries.append(result)
            converted += 1
        else:
            skipped += 1

    if HAS_RICH:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold blue]{task.description}"),
            BarColumn(bar_width=40),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("Converting", total=len(json_files))
            for fp in json_files:
                _process(fp)
                progress.advance(task)
    else:
        for i, fp in enumerate(json_files, 1):
            _process(fp)
            if i % 100 == 0:
                print(f"  {i}/{len(json_files)}...")

    # Build index files
    index_entries.sort(key=lambda x: x["title"].lower())

    lines = [
        "# ChatGPT Conversation Archive",
        "",
        f"**Total:** {converted} conversations",
        f"**Exported:** {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "---",
        "",
    ]
    for e in index_entries:
        lines.append(f"### [{e['title']}]({e['file']})")
        lines.append(f"{e['messages']} messages")
        lines.append(f"> {e['summary']}")
        lines.append("")

    (cfg.md_dir / "INDEX.md").write_text("\n".join(lines), encoding="utf-8")
    (cfg.md_dir / "index.json").write_text(
        json.dumps(index_entries, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    success(f"Converted {converted} conversations -> {cfg.md_dir}")
    if skipped:
        warn(f"Skipped {skipped} empty conversations")


ROLE_MAP = {
    "user": "User",
    "assistant": "Assistant",
    "tool": "Tool Output",
    "system": "System",
}


def _convert_one(filepath: Path, cfg: Config) -> dict | None:
    try:
        data = json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    messages = data.get("messages", [])
    if not messages:
        return None

    title = data.get("title", "Untitled")
    url = data.get("url", "")

    safe = re.sub(r"[^\w\s\-]", "_", title)[:80].strip()
    safe = re.sub(r"\s+", "_", safe) or "untitled"
    out_path = cfg.md_dir / f"{safe}.md"

    counter = 1
    while out_path.exists():
        out_path = cfg.md_dir / f"{safe}_{counter}.md"
        counter += 1

    lines = [f"# {title}", ""]
    meta = []
    if url:
        meta.append(f"**Source:** {url}")
    meta.append(f"**Messages:** {len(messages)}")
    lines.append(" | ".join(meta))
    lines.extend(["", "---", ""])

    for msg in messages:
        role = msg.get("role", "unknown")
        lines.append(f"## {ROLE_MAP.get(role, role.title())}")
        lines.append("")
        lines.append(msg.get("text", ""))
        lines.extend(["", "---", ""])

    out_path.write_text("\n".join(lines), encoding="utf-8")

    summary = "(no user messages)"
    for msg in messages:
        if msg.get("role") == "user":
            summary = msg["text"][:150].replace("\n", " ").strip()
            if len(msg["text"]) > 150:
                summary += "..."
            break

    return {
        "title": title,
        "file": out_path.name,
        "messages": len(messages),
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# STATUS
# ---------------------------------------------------------------------------
def cmd_status(cfg: Config):
    header("Export Status")

    indexed = 0
    if cfg.index_path.exists():
        indexed = len(json.loads(cfg.index_path.read_text(encoding="utf-8")))

    raw_files = list(cfg.raw_dir.glob("*.json"))
    downloaded = len([f for f in raw_files if not f.name.startswith("_")])

    fail_count = 0
    if cfg.failures_path.exists():
        fail_count = len(json.loads(cfg.failures_path.read_text(encoding="utf-8")))

    md_files = list(cfg.md_dir.glob("*.md"))
    converted = len([f for f in md_files if f.name != "INDEX.md"])

    if HAS_RICH:
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column(style="bold")
        table.add_column(style="cyan", justify="right")
        table.add_row("Indexed", str(indexed))
        table.add_row("Downloaded", str(downloaded))
        if fail_count:
            table.add_row("Failed", f"[red]{fail_count}[/red]")
        table.add_row("Converted", str(converted))
        console.print()
        console.print(table)
        console.print()

        if indexed and downloaded < indexed:
            remaining = indexed - downloaded
            est = (remaining / 4) * 12 / 60
            warn(f"{remaining} left to download (~{est:.0f} min)")
        if downloaded and converted < downloaded:
            warn(f"Run 'convert' to generate {downloaded - converted} markdown files")
        if indexed and downloaded >= indexed and converted >= downloaded:
            success("All done!")
    else:
        print(f"  Indexed:     {indexed}")
        print(f"  Downloaded:  {downloaded}")
        if fail_count:
            print(f"  Failed:      {fail_count}")
        print(f"  Converted:   {converted}")


# ---------------------------------------------------------------------------
# RUN (full pipeline)
# ---------------------------------------------------------------------------
def cmd_run(cfg: Config):
    cmd_scan(cfg)
    cmd_download(cfg)
    cmd_convert(cfg)
    cmd_status(cfg)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        prog="chatgpt-export",
        description="Download and convert all your ChatGPT conversations.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  chatgpt-export scan                   Discover all conversations\n"
            "  chatgpt-export download -j 6          Download with 6 parallel tabs\n"
            "  chatgpt-export convert                Convert to markdown\n"
            "  chatgpt-export run -o ./export         Full pipeline to custom dir\n"
        ),
    )
    parser.add_argument(
        "--output-dir", "-o", type=Path, default=Path("."),
        help="output directory (default: current dir)",
    )
    parser.add_argument(
        "--chrome-path", type=str, default="",
        help="path to Chrome executable (auto-detected if omitted)",
    )
    parser.add_argument(
        "--profile-dir", type=Path, default=None,
        help="Chrome profile directory (default: .chrome_profile in output dir)",
    )
    parser.add_argument(
        "--port", type=int, default=9222,
        help="Chrome remote debugging port (default: 9222)",
    )
    parser.add_argument(
        "--parallel", "-j", type=int, default=4,
        help="number of parallel tabs for download (default: 4)",
    )
    parser.add_argument(
        "--timeout", "-t", type=int, default=20,
        help="seconds to wait per conversation load (default: 20)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="scan sidebar for conversations")

    dl = sub.add_parser("download", help="download conversations")
    dl.add_argument(
        "--retry-failed", action="store_true",
        help="re-attempt previously failed conversations",
    )

    cv = sub.add_parser("convert", help="convert raw JSON to markdown")
    cv.add_argument(
        "--force", action="store_true",
        help="re-convert all files (overwrite existing)",
    )

    sub.add_parser("status", help="show progress dashboard")
    sub.add_parser("run", help="full pipeline: scan -> download -> convert")

    args = parser.parse_args()

    cfg = Config(
        output_dir=args.output_dir.resolve(),
        chrome_path=args.chrome_path,
        profile_dir=(args.profile_dir or args.output_dir / ".chrome_profile").resolve(),
        debug_port=args.port,
        parallel=args.parallel,
        load_timeout=args.timeout,
    )

    commands = {
        "scan": lambda: cmd_scan(cfg),
        "download": lambda: cmd_download(cfg, retry_failed=args.retry_failed),
        "convert": lambda: cmd_convert(cfg),
        "status": lambda: cmd_status(cfg),
        "run": lambda: cmd_run(cfg),
    }
    commands[args.command]()


if __name__ == "__main__":
    main()
