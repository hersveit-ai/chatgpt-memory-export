# chatgpt-memory-export

Export all your ChatGPT conversations when the official export fails.

Opens your real Chrome browser, scrolls through your entire chat history, downloads every conversation, and converts them to clean markdown files -- ready for archival, search, or ingestion into other AI memory systems.

## Why?

ChatGPT's built-in "Export data" feature frequently fails or hangs. This tool takes a different approach: it connects to your actual Chrome session via CDP (Chrome DevTools Protocol), navigates the UI directly, and extracts conversations from the rendered page. No API keys needed, no auth tokens to manage.

## Features

- **Uses your real Chrome session** -- no separate login required
- **Parallel tab downloading** -- opens multiple conversations at once (configurable)
- **Fully resumable** -- skips already-downloaded conversations, tracks failures for retry
- **Clean markdown output** -- each conversation becomes a readable `.md` file with an index
- **Progress bars** -- powered by `rich` (optional, works without it too)

## Quick start

```bash
# Install dependencies
pip install playwright rich
python -m playwright install chromium

# Clone and run
git clone https://github.com/hersveit-ai/chatgpt-memory-export.git
cd chatgpt-memory-export

# Close Chrome completely, then:
python chatgpt_export.py run
```

This will scan your sidebar, download all conversations, and convert them to markdown.

## Commands

| Command | Description |
|---------|-------------|
| `scan` | Scroll through the ChatGPT sidebar and index all conversations |
| `download` | Download each conversation (parallel tabs, resumable) |
| `convert` | Convert raw JSON to clean, readable markdown files |
| `status` | Show progress dashboard (indexed / downloaded / converted) |
| `run` | Full pipeline: scan -> download -> convert |

## Options

```
--output-dir, -o    Output directory (default: current dir)
--parallel, -j      Number of parallel tabs (default: 4)
--timeout, -t       Seconds to wait per conversation load (default: 20)
--chrome-path       Path to Chrome executable (auto-detected)
--port              Chrome debug port (default: 9222)
```

## Examples

```bash
# Download with 6 parallel tabs for faster export
python chatgpt_export.py download -j 6

# Export to a specific directory
python chatgpt_export.py run -o ./my-export

# Retry conversations that failed on the first attempt
python chatgpt_export.py download --retry-failed

# Check progress
python chatgpt_export.py status
```

## Output structure

```
./
  raw_conversations/      Raw JSON files (one per conversation)
    _index.json           Conversation index from sidebar scan
    _failures.json        Failed download log
    *.json                Individual conversations
  memory_export/          Converted markdown files
    INDEX.md              Browsable master index
    index.json            Machine-readable index
    *.md                  Individual conversation markdown
```

## How it works

1. **Launches Chrome** with `--remote-debugging-port` using your existing profile
2. **Connects via CDP** (Chrome DevTools Protocol) using Playwright
3. **Scans the sidebar** by scrolling to load all conversation links
4. **Opens conversations in parallel tabs**, waits for full content to render
5. **Extracts messages** from the DOM using `[data-message-author-role]` selectors
6. **Converts to markdown** with metadata and generates a searchable index

## Important notes

- **Close Chrome completely** before running `scan` or `download`. Chrome locks its debug port -- only one process can use it.
- The first run will open Chrome with a fresh profile. **Log in to ChatGPT** in the browser window. Your session is saved for subsequent runs.
- ChatGPT may update its frontend. If extraction breaks, the DOM selectors are defined at the top of `chatgpt_export.py` for easy updating.

## Requirements

- Python 3.10+
- Google Chrome
- `playwright` (required)
- `rich` (optional, for progress bars)

## License

MIT
