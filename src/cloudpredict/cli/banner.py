"""Terminal presentation, ANSI color palettes, and branded ASCII banners."""

from __future__ import annotations

import os
import sys

# Detect color support
_NO_COLOR = bool(os.environ.get("NO_COLOR"))
_IS_TTY = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False
_COLOR_ENABLED = _IS_TTY and not _NO_COLOR

if _COLOR_ENABLED:
    ORANGE = "\033[38;5;208m"
    CYAN = "\033[38;5;39m"
    GREEN = "\033[38;5;42m"
    GRAY = "\033[38;5;244m"
    YELLOW = "\033[38;5;220m"
    RED = "\033[38;5;196m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"
else:
    ORANGE = CYAN = GREEN = GRAY = YELLOW = RED = BOLD = DIM = RESET = ""


BANNER_TEXT = f"""{ORANGE}  ██████╗██╗      ██████╗ ██╗   ██╗██████╗ {CYAN}██████╗ ██████╗ ███████╗██████╗ ██╗ ██████╗████████╗
{ORANGE} ██╔════╝██║     ██╔═══██╗██║   ██║██╔══██╗{CYAN}██╔══██╗██╔══██╗██╔════╝██╔══██╗██║██╔════╝╚══██╔══╝
{ORANGE} ██║     ██║     ██║   ██║██║   ██║██║  ██║{CYAN}██████╔╝██████╔╝█████╗  ██║  ██║██║██║        ██║   
{ORANGE} ██║     ██║     ██║   ██║██║   ██║██║  ██║{CYAN}██╔═══╝ ██╔══██╗██╔══╝  ██║  ██║██║██║        ██║   
{ORANGE} ╚██████╗███████╗╚██████╔╝╚██████╔╝██████╔╝{CYAN}██║     ██║  ██║███████╗██████╔╝██║╚██████╗   ██║   
{ORANGE}  ╚═════╝╚══════╝ ╚═════╝  ╚═════╝ ╚═════╝ {CYAN}╚═╝     ╚═╝  ╚═╝╚══════╝╚═════╝ ╚═╝ ╚═════╝   ╚═╝{RESET}

  {BOLD}cloudPredict{RESET} {GRAY}v0.1.0{RESET} {DIM}•{RESET} {ORANGE}HOT ($0.023/GB){RESET} {DIM}→{RESET} {CYAN}COLD ($0.004/GB){RESET} {DIM}•{RESET} {GREEN}Expected-Value Engine{RESET}
  {DIM}Autonomous Cloud Storage Reconciliation Daemon & FinOps Control Plane{RESET}
"""


def print_banner() -> None:
    """Print the branded high-contrast CLI banner."""
    print(BANNER_TEXT)


def print_status_box(title: str, fields: list[tuple[str, str]]) -> None:
    """Print an enterprise status box with neat Unicode border alignment."""
    max_label_len = max(len(k) for k, _ in fields) if fields else 10
    width = 72

    print(f"  {CYAN}┌─{RESET} {BOLD}{title}{RESET} {CYAN}{'─' * (width - len(title) - 5)}┐{RESET}")
    for key, value in fields:
        padding = " " * (max_label_len - len(key))
        print(f"  {CYAN}│{RESET}  {GRAY}{key}:{RESET}{padding} {value}")
    print(f"  {CYAN}└{'─' * (width - 1)}┘{RESET}\n")
