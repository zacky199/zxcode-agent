"""
ZX Code terminal UI.

An opencode-inspired TUI with its own identity:
- Bottom status bar showing model / session / tokens / provider.
- Streaming assistant text with an animated prompt.
- Tool activity rendered as inline "cards".
- A distinct "Aurora" color concept (deep space + violet/cyan/rose).
"""

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.markup import escape
from rich import box
from rich.live import Live
from rich.spinner import Spinner
from rich.console import Group

from prompt_toolkit import PromptSession
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style as PromptStyle
from prompt_toolkit.formatted_text import HTML

from config.constants import APP_NAME, APP_VERSION

# ---------------------------------------------------------------------------
# Aurora theme concept: deep space background, violet/cyan/rose accents.
# ---------------------------------------------------------------------------
THEMES = {
    "aurora": {
        "primary": "#8B9DFF", "accent": "#7EF0D4", "error": "#FF5C7A",
        "warn": "#FFC98A", "muted": "#6E7A96", "bg_toolbar": "#131A2E",
        "gradient": ("#8B9DFF", "#7EF0D4"),
    },
    "nebula": {
        "primary": "#C77DFF", "accent": "#FF9EC5", "error": "#FF4D6D",
        "warn": "#FFD166", "muted": "#8075A0", "bg_toolbar": "#1B1432",
        "gradient": ("#C77DFF", "#FF9EC5"),
    },
    "nord": {
        "primary": "#81A1C1", "accent": "#A3BE8C", "error": "#BF616A",
        "warn": "#EBCB8B", "muted": "#616E88", "bg_toolbar": "#3B4252",
        "gradient": ("#88C0D0", "#A3BE8C"),
    },
    "dracula": {
        "primary": "#BD93F9", "accent": "#50FA7B", "error": "#FF5555",
        "warn": "#F1FA8C", "muted": "#6272A4", "bg_toolbar": "#282A36",
        "gradient": ("#BD93F9", "#50FA7B"),
    },
    "monokai": {
        "primary": "#FC9867", "accent": "#A9DC76", "error": "#FF6188",
        "warn": "#FFD866", "muted": "#75715E", "bg_toolbar": "#2D2A2E",
        "gradient": ("#FF6188", "#A9DC76"),
    },
    "night_owl": {
        "primary": "#82AAFF", "accent": "#C3E88D", "error": "#EF5350",
        "warn": "#FFCB6B", "muted": "#546E7A", "bg_toolbar": "#011627",
        "gradient": ("#82AAFF", "#C3E88D"),
    },
    "one_dark": {
        "primary": "#61AFEF", "accent": "#98C379", "error": "#E06C75",
        "warn": "#E5C07B", "muted": "#5C6370", "bg_toolbar": "#282C34",
        "gradient": ("#61AFEF", "#98C379"),
    },
    "sakura": {
        "primary": "#F78FB3", "accent": "#B39DF8", "error": "#EB445A",
        "warn": "#F7C873", "muted": "#8E7CC3", "bg_toolbar": "#2A1E33",
        "gradient": ("#F78FB3", "#B39DF8"),
    },
    "matrix": {
        "primary": "#4AF626", "accent": "#C8F7C5", "error": "#FF5C5C",
        "warn": "#FFE66D", "muted": "#5D8A5D", "bg_toolbar": "#0B140B",
        "gradient": ("#4AF626", "#C8F7C5"),
    },
}

DEFAULT_THEME = "aurora"


class ConsoleUI:
    """Presentation layer for the ZX agent CLI."""

    def __init__(self, active_theme: str = DEFAULT_THEME):
        self.console = Console(highlight=False, safe_box=True)
        self.model_name = "Unknown"
        self.provider_name = "Unknown"
        self.project_name = "Unknown"
        self.token_count = 0
        self.status_text = "idle"
        self._tool_stack: Dict[str, str] = {}
        self._tokens_history: list = []
        self._theme = DEFAULT_THEME
        self.session: Optional[PromptSession] = None
        self._live: Optional[Live] = None
        self._live_iteration = 0
        self._live_status = ""
        self._live_last_tool = ""
        self.set_theme(active_theme)

    # ------------------------------------------------------------------
    # Theme management
    # ------------------------------------------------------------------
    @property
    def available_themes(self) -> list:
        return list(THEMES.keys())

    def set_theme(self, theme_name: str):
        theme = THEMES.get(theme_name.lower(), THEMES[DEFAULT_THEME])
        self._theme = theme_name.lower()
        self.theme_color = theme["primary"]
        self.accent_color = theme["accent"]
        self.error_color = theme["error"]
        self.warn_color = theme["warn"]
        self.muted_color = theme["muted"]
        self.bg_toolbar = theme.get("bg_toolbar", "#1e1e1e")
        self.gradient = theme.get("gradient", ("#8B9DFF", "#7EF0D4"))

        self.pt_style = PromptStyle.from_dict({
            "bottom-toolbar": f"bg:{self.bg_toolbar} {self.theme_color}",
            "bottom-toolbar.text": "#ffffff",
            "prompt": f"bold {self.accent_color}",
            "placeholder": "#777777 italic",
        })
        if self.session is not None:
            self.session.app.style = self.pt_style

    def set_model_info(self, provider: str, model: str):
        self.provider_name = provider
        self.model_name = model

    def set_project(self, workspace: Path):
        self.project_name = workspace.name if workspace else "Unknown"

    def update_tokens(self, count: int):
        self.token_count = count
        self._tokens_history.append(count)

    # ------------------------------------------------------------------
    # Screen helpers
    # ------------------------------------------------------------------
    def clear_screen(self):
        os.system("clear" if os.name == "posix" else "cls")

    def show_splash(self):
        self.clear_screen()
        badge = Panel(
            Text(f" {APP_NAME} {APP_VERSION} initialized ", style=self.theme_color),
            border_style=self.theme_color,
            expand=False,
            box=box.ROUNDED,
        )
        self.console.print(badge)

        ascii_art = f"""[{self.theme_color}]
███████╗██╗  ██╗     ██████╗ ██████╗ ██████╗ ███████╗
╚══███╔╝╚██╗██╔╝    ██╔════╝██╔═══██╗██╔══██╗██╔════╝
  ███╔╝  ╚███╔╝     ██║     ██║   ██║██║  ██║█████╗  
 ███╔╝   ██╔██╗     ██║     ██║   ██║██║  ██║██╔══╝  
███████╗██╔╝ ██╗    ╚██████╗╚██████╔╝██████╔╝███████╗
╚══════╝╚═╝  ╚═╝     ╚═════╝ ╚═════╝ ╚═════╝ ╚══════╝[/{self.theme_color}]
"""
        self.console.print(ascii_art)
        self.console.print("[dim]Modules loaded. Press[/dim] [white]Enter[/white] [dim]to start.[/dim]")
        try:
            input()
        except (EOFError, KeyboardInterrupt):
            pass
        self.clear_screen()

    def print_header(self, provider: str, model: str, workspace: Path):
        self.set_model_info(provider, model)
        self.set_project(workspace)

        system_info = f"""[white]{APP_NAME} Pro[/white]

[dim]Provider[/dim] : [{self.accent_color}]{escape(provider)}[/{self.accent_color}]
[dim]Model[/dim]    : [{self.theme_color}]{escape(model)}[/{self.theme_color}]
[dim]Path[/dim]     : [white]{escape(workspace.name)}[/white]
[dim]Theme[/dim]    : {self._theme}"""

        controls = f"""[{self.theme_color}]Commands[/{self.theme_color}]
[white]/help[/white]     - Show commands
[white]/model[/white]    - Switch model instantly
[white]/multi[/white]    - Multi-AI routing setup
[white]/skill[/white]    - Load a skill persona
[white]/theme[/white]    - Change UI theme
[white]/install[/white]  - Install a skill/plugin
[white]/setup[/white]    - Reconfigure API
[white]/confirm[/white]  - Disable legacy auto-approval; prompts remain required
[white]/edits[/white]    - Turn native file edits on/off
[white]/resume[/white]   - Reload the saved conversation for this folder
[white]/new[/white]      - Start a fresh conversation
[white]/clear[/white]    - Clear screen"""

        table = Table(show_header=False, show_edge=False, box=box.MINIMAL,
                      padding=(0, 3), expand=True)
        table.add_column(justify="left", ratio=1)
        table.add_column(justify="left", ratio=1)
        table.add_row(system_info, controls)

        panel = Panel(table, border_style=self.theme_color, box=box.ROUNDED, padding=(1, 2))
        self.console.print(panel)
        self.console.print("")

    # ------------------------------------------------------------------
    # Bottom status bar (opencode-style)
    # ------------------------------------------------------------------
    def _render_bottom_toolbar(self) -> Any:
        from html import escape as html_escape
        left = (f" <b>{html_escape(self.model_name)}</b>"
                f"<b>  |  {html_escape(self.provider_name)}</b>")
        right = (f"{html_escape(self.project_name)}"
                 f"  |  {self.token_count} tokens"
                 f"  |  {html_escape(self.status_text)}"
                 f"  |  {APP_NAME} v{APP_VERSION}")
        width = shutil.get_terminal_size().columns
        pad_len = max(width - len(left) - len(right), 2)
        padding = " " * pad_len
        return HTML(f"{left}{padding}{right}")

    def _make_session(self) -> PromptSession:
        bindings = KeyBindings()
        session = PromptSession(
            style=self.pt_style,
            key_bindings=bindings,
            mouse_support=True,
        )
        return session

    async def ask_user(self) -> str:
        """Show the interactive prompt and capture user input (async)."""
        self.status_text = "idle"
        if self.session is None:
            self.session = self._make_session()
        # Re-apply theme in case it changed.
        self.session.app.style = self.pt_style

        self.console.print(f"[{self.muted_color}]{'─' * shutil.get_terminal_size().columns}[/{self.muted_color}]")

        user_input = await self.session.prompt_async(
            HTML("<b>❯ </b>"),
            default="",
            placeholder='Try "buatkan landing page modern..." or type /help',
            bottom_toolbar=self._render_bottom_toolbar,
        )
        self.console.print("")
        result = user_input.strip()
        if result and not result.startswith("/"):
            self.console.print(f"[{self.theme_color}]╭─── {escape(result[:60])}{'...' if len(result) > 60 else ''}[/{self.theme_color}]")
        return result

    # ------------------------------------------------------------------
    # Live activity panel (real-time status while the agent is running)
    # ------------------------------------------------------------------
    def start_activity(self):
        """Begin a live, self-updating activity region for the current run."""
        self.stop_activity()
        if not self.console.is_terminal:
            return
        self._live_iteration = 0
        self._live_status = "working"
        self._live_last_tool = ""
        self._live = Live(
            self._activity_render(),
            console=self.console,
            refresh_per_second=10,
            vertical_overflow="visible",
        )
        self._live.start()

    def update_activity(self, status: str = "", iteration: int = 0,
                        last_tool: str = ""):
        if not self._live:
            return
        if status:
            self._live_status = str(status)[:60]
        if iteration:
            self._live_iteration = iteration
        if last_tool:
            self._live_last_tool = last_tool
        self.status_text = self._live_status
        self._live.update(self._activity_render())

    def stop_activity(self):
        if self._live is not None:
            try:
                self._live.stop()
            except Exception:
                pass
            self._live = None
        self._live_last_tool = ""

    def _activity_render(self) -> Any:
        spinner = Spinner("dots", text=f" {self._live_status}",
                          style=self.accent_color)
        bits = []
        if self._live_iteration:
            bits.append(f"[{self.muted_color}]iter {self._live_iteration}[/{self.muted_color}]")
        if self._live_last_tool:
            bits.append(f"[{self.theme_color}]⚙ {escape(self._live_last_tool)}[/{self.theme_color}]")
        bits.append(f"[{self.muted_color}]{escape(self.model_name)}[/{self.muted_color}]")
        info = Text.from_markup("  |  ".join(bits)) if bits else Text("")
        return Panel(
            Group(spinner, info),
            border_style=self.theme_color,
            box=box.SQUARE,
            title=APP_NAME,
            title_align="left",
        )

    # ------------------------------------------------------------------
    # Error / notification helpers
    # ------------------------------------------------------------------
    def print_error(self, message: str):
        self.console.print(f"[bold {self.error_color}]✖ Error:[/bold {self.error_color}] {escape(str(message))}")

    def print_warning(self, message: str):
        self.console.print(f"[{self.warn_color}]⚠ {escape(str(message))}[/{self.warn_color}]")

    def print_success(self, message: str):
        self.console.print(f"[bold {self.accent_color}]✓ {escape(str(message))}[/bold {self.accent_color}]")

    # ------------------------------------------------------------------
    # Streaming event rendering
    # ------------------------------------------------------------------
    def handle_stream_event(self, event: Dict[str, Any]):
        evt_type = event.get("type")
        content = event.get("content")

        if evt_type == "plan":
            self._render_plan(content)

        elif evt_type == "status":
            self.status_text = str(content)[:40]
            self.update_activity(status=str(content))
            self.console.print(f"[{self.muted_color}]┊ {escape(str(content))}[/{self.muted_color}]")

        elif evt_type == "text":
            self.console.print(escape(str(content)), end="", soft_wrap=True)

        elif evt_type == "tool_call":
            name = content.get("name", "")
            args = content.get("arguments", {})
            preview = self._tool_preview(name, args)
            self.update_activity(last_tool=name)
            self.console.print(f"\n[{self.theme_color}]┌─[/{self.theme_color}] [bold {self.accent_color}]⚙ {escape(name)}[/bold {self.accent_color}] {preview}")
            self.status_text = f"running {name}"

        elif evt_type == "role_result":
            role = str(content.get("role", "role")).replace("_", " ").title()
            model = str(content.get("model", "unknown model"))
            result_text = str(content.get("text", "")).strip()
            if result_text:
                self.console.print(Panel(
                    Text(result_text, style="white"),
                    title=f"{role} · {model}",
                    title_align="left",
                    border_style=self.accent_color,
                    box=box.ROUNDED,
                    padding=(1, 2),
                ))

        elif evt_type == "tool_result":
            name = content.get("name", "")
            status = content.get("status", "ok")
            output = content.get("output_preview", "")
            color = self.accent_color if status == "ok" else self.error_color
            state = "✓" if status == "ok" else ("⏸" if status == "cancelled" else "✖")
            self.console.print(f"[{self.theme_color}]└─[/{self.theme_color}] [{color}]{state} {escape(name)}[/{color}]")
            if output:
                self._render_output(output)

        elif evt_type == "error":
            self.status_text = "error"
            self.print_error(content)

        elif evt_type == "diff_summary":
            self.console.print(Panel(
                Text(str(content), style=self.muted_color),
                title="Workspace diff / manual undo",
                title_align="left",
                border_style=self.warn_color,
                box=box.ROUNDED,
                padding=(1, 2),
            ))

        elif evt_type == "done":
            self.status_text = "done"
            self.stop_activity()
            self.console.print(f"\n[{self.theme_color}]╰{'─' * 55}[/{self.theme_color}]\n")

    def _render_plan(self, steps):
        if not steps:
            return
        self.console.print(f"\n[{self.theme_color}]📋 [bold]Execution Plan[/bold][/{self.theme_color}]")
        for idx, step in enumerate(steps, 1):
            self.console.print(f"  [{self.muted_color}]{idx:>2}.[/{self.muted_color}] {escape(str(step))}")
        self.console.print("")

    def _tool_preview(self, name: str, args: Dict[str, Any]) -> str:
        if name == "read_file":
            return f"[dim]{escape(str(args.get('file_path', '')))}[/dim]"
        if name in ("write_file", "edit_file"):
            return f"[dim]{escape(str(args.get('file_path', '')))}[/dim]"
        if name == "run_command":
            cmd = str(args.get("command", ""))[:70]
            return f"[dim]$ {escape(cmd)}[/dim]"
        if name == "search_files":
            return f"[dim]/{escape(str(args.get('pattern', '')))}[/dim]"
        return ""

    def _render_output(self, output: str):
        lines = output.splitlines()
        if len(lines) > 12:
            shown = "\n".join(lines[:12]) + f"\n... (+{len(lines) - 12} lines)"
        else:
            shown = output
        panel = Panel(
            Text(shown, style=self.muted_color),
            border_style=self.muted_color,
            box=box.SIMPLE,
            padding=(0, 1),
            title="output",
            title_align="left",
        )
        self.console.print(panel)

    # ------------------------------------------------------------------
    # Command menu
    # ------------------------------------------------------------------
    def show_command_menu(self):
        table = Table(title="ZX Code Commands", border_style=self.theme_color,
                      box=box.ROUNDED, expand=False, pad_edge=False)
        table.add_column("Command", style=self.accent_color, no_wrap=True)
        table.add_column("Description", style="white")
        rows = [
            ("/help", "Show this menu"),
            ("/model [name]", "Switch model or list models"),
            ("/provider", "List / switch AI provider"),
            ("/multi", "Configure Multi-AI routing (planner/coder/reviewer)"),
            ("/skill", "Load a skill persona (e.g. UI-UX Pro Max)"),
            ("/skills", "List all installed & built-in skills"),
            ("/install <url>", "Install a skill/plugin from GitHub/GitLab/ZIP"),
            ("/theme [name]", "Change UI theme"),
            ("/setup", "Reconfigure provider / API key / model"),
            ("/edits [on|off]", "Enable/disable native file edits for this session"),
            ("/resume", "Reload the saved conversation for this folder"),
            ("/tasks", "List persisted task checkpoints and recovery state"),
            ("/task-status <task-id>", "Inspect checkpoint, node acceptance, and recovery evidence"),
            ("/resume-task <task-id>", "Continue pending DAG nodes from a safe checkpoint"),
            ("/discard-task <task-id>", "Discard checkpoint metadata after confirmation (workspace unchanged)"),
            ("/new", "Start a fresh conversation (deletes the saved copy)"),
            ("/clear", "Clear screen & keep memory"),
            ("/session", "Show session stats"),
            ("/exit", "Exit ZX Code"),
        ]
        for cmd, desc in rows:
            table.add_row(f"{cmd}", desc)
        self.console.print(table)
        self.console.print("")
