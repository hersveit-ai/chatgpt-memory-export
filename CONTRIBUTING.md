# Contributing

Thanks for your interest in chatgpt-memory-export. Here's how to contribute effectively.

## Reporting issues

- Check existing issues before opening a new one
- Include your OS, Python version, and Chrome version
- If extraction broke, note the date -- ChatGPT's DOM changes frequently

## Pull requests

1. Fork the repo and create a branch from `master`
2. Keep changes focused -- one fix or feature per PR
3. Test your changes against a real ChatGPT account
4. Update the README if you changed CLI behavior

### What we'll merge

- Bug fixes with clear reproduction steps
- DOM selector updates when ChatGPT changes their frontend
- Performance improvements to download/conversion
- Cross-platform compatibility fixes (macOS, Linux)

### What we won't merge

- Features that require additional dependencies beyond playwright/rich
- Changes that break the single-file architecture
- Anything that adds auth token handling or credential storage
- Cosmetic-only changes (formatting, comments, variable renames)

## DOM selectors

ChatGPT updates their frontend regularly. If extraction breaks, the fix is usually updating the selectors at the top of `chatgpt_export.py`:

```python
SEL_MESSAGE = "[data-message-author-role]"
SEL_SIDEBAR_LINK = 'nav a[href^="/c/"]'
```

If you find updated selectors that work, please open a PR.

## Code style

- Single file, no package structure
- Stdlib + playwright + rich only
- Functions over classes where possible
- Keep it simple -- if the fix is 3 lines, don't add an abstraction
