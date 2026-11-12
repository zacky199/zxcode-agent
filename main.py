#!/usr/bin/env python3
"""
ZX Agent Pro — professional coding agent CLI.

An opencode-style autonomous coding agent with:
- Native tool calling (files, edits, commands, search, git)
- Multi-AI routing (planner / coder / reviewer roles)
- Built-in skill personas (UI-UX Pro Max, etc.)
- Structured planning + self-reflection + self-repair
"""

import sys
import asyncio
import argparse
import logging
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from rich.prompt import Prompt, Confirm
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from config import (
    SettingsManager, AppConfig, DEFAULT_SKILL_DIR, BUILTIN_SKILL_DIR,
    DEFAULT_CONFIG_DIR, APP_NAME, APP_VERSION,
)
from utils import setup_logger
from ui.console import ConsoleUI, DEFAULT_THEME
from providers import get_provider, PROVIDERS, RoleRouter
from core import ZXAgentPro, AgentConfig
from memory.session_store import SessionStore
from tools.command_risk import assess_command
from plugins import UniversalInstaller, PluginManager

log_file = DEFAULT_CONFIG_DIR / "logs" / "zx_agent.log"
logger = setup_logger("zx_agent", log_file=log_file, level=logging.INFO)


# ---------------------------------------------------------------------------
# Setup wizards
# ---------------------------------------------------------------------------
def run_setup(ui: ConsoleUI, settings_mgr: SettingsManager, current_cfg: AppConfig) -> AppConfig:
    """Interactive provider/API/model setup wizard."""
    ui.console.print(f"\n[{ui.theme_color}]⚙️  Setup {APP_NAME}[/{ui.theme_color}]")

    keys = list(PROVIDERS.keys())
    for i, k in enumerate(keys, 1):
        ui.console.print(f"  {i}. {PROVIDERS[k]['name']} [dim]({k})[/dim]")

    choice = Prompt.ask(f"\nProvider (1-{len(keys)})", default="1")
    try:
        prov = keys[int(choice) - 1]
    except (ValueError, IndexError):
        prov = choice.lower() if choice.lower() in keys else "claude"

    pinfo = PROVIDERS[prov]
    model = Prompt.ask("Model", default=pinfo["default_model"])

    if prov in ("ollama", "9router") or pinfo.get("key_hint") == "bebas/dummy":
        api_key = "dummy"
    else:
        api_key = Prompt.ask("API Key", password=True, default=current_cfg.api_key)

    current_cfg.provider = prov
    current_cfg.model = model
    current_cfg.api_key = api_key
    current_cfg.base_url = pinfo["base_url"]
    settings_mgr.save(current_cfg)
    ui.print_success("Setup complete.")
    return current_cfg


def build_multi_ai_router(cfg: AppConfig, fallback_provider) -> RoleRouter:
    """Instantiate the role router from saved multi_ai config."""
    if not cfg.multi_ai:
        return None
    backends = {}
    for role, spec in cfg.multi_ai.items():
        try:
            prov = get_provider(
                spec.get("provider", cfg.provider),
                spec.get("api_key") or cfg.api_key,
                spec.get("model") or cfg.model,
            )
            backends[role] = prov
        except Exception as e:
            logger.warning(f"Multi-AI role '{role}' failed to init: {e}")
    backends.setdefault("main", fallback_provider)
    backends.setdefault("planner", fallback_provider)
    return RoleRouter(backends) if len(backends) > 1 else None


def confirm_shell_command(ui: ConsoleUI, cmd: str) -> bool:
    """Require an individual, fail-closed confirmation for every shell command."""
    try:
        notes = assess_command(cmd)
    except Exception:
        notes = []
    risk_block = ""
    if notes:
        risk_block = "\n[bold red]Review carefully:[/bold red]\n" + "\n".join(
            f"  [red]•[/red] {escape(note)}" for note in notes)
    try:
        return bool(Confirm.ask(
            f"[{ui.warn_color}]Run this shell command?[/{ui.warn_color}]\n"
            f"[white]{escape(cmd)}[/white]{risk_block}",
            default=False,
        ))
    except (EOFError, KeyboardInterrupt):
        return False


def cmd_disable_legacy_auto_confirm(ui: ConsoleUI, settings_mgr: SettingsManager,
                                    cfg: AppConfig) -> None:
    """Persistently disable the obsolete global auto-approval preference."""
    cfg.auto_confirm_dangerous = False
    settings_mgr.save(cfg)
    ui.print_warning("Legacy auto-approval disabled; every shell command needs per-command approval.")


def confirm_enable_file_edits(ui: ConsoleUI, workspace: Path) -> bool:
    """Explicit, fail-closed confirmation before native file edits are enabled."""
    ui.console.print(Panel(
        escape(
            f"The agent will be able to create and modify files inside:\n  {workspace}\n\n"
            "Credential-like files (.env, keys) stay blocked. Every edit is recorded in the "
            "task journal and summarized afterwards, but this is a recovery record, "
            "not an OS sandbox. Shell commands still need per-command approval."),
        title="Enable native file edits", border_style=ui.warn_color))
    try:
        return bool(Confirm.ask("Enable write_file / edit_file for this session?", default=False))
    except (EOFError, KeyboardInterrupt):
        return False


def cmd_edits(ui: ConsoleUI, agent: ZXAgentPro, arg: str) -> None:
    """``/edits [on|off]`` - show or change native file-edit access for this session."""
    engine = agent.tool_engine
    arg = (arg or "").strip().lower()
    if arg in ("", "status"):
        state = "ON" if engine.file_mutations_enabled else "OFF"
        ui.console.print(f"[dim]Native file edits: [bold]{state}[/bold] (change with /edits on|off)[/dim]")
    elif arg == "on":
        if engine.file_mutations_enabled:
            ui.print_warning("Native file edits are already ON.")
        elif confirm_enable_file_edits(ui, agent.workspace):
            engine.set_file_mutations_enabled(True)
            ui.print_warning("Native file edits ON for this session.")
        else:
            ui.console.print("[dim]Native file edits stay OFF.[/dim]")
    elif arg == "off":
        engine.set_file_mutations_enabled(False)
        ui.console.print("[dim]Native file edits OFF.[/dim]")
    else:
        ui.print_warning("Usage: /edits [on|off]")


def cmd_resume(ui: ConsoleUI, agent: ZXAgentPro) -> None:
    """``/resume`` - reload the saved conversation for this workspace."""
    count = agent.restore_session()
    if count:
        ui.console.print(f"[dim]Resumed {count} saved messages for this folder.[/dim]")
    else:
        ui.print_warning("No saved conversation found for this folder.")


def cmd_new(ui: ConsoleUI, agent: ZXAgentPro) -> None:
    """``/new`` - start a fresh conversation and delete the saved copy."""
    agent.reset_conversation()
    ui.console.print("[dim]Started a new conversation (saved copy removed).[/dim]")


def cmd_tasks(ui: ConsoleUI, agent: ZXAgentPro) -> None:
    """List persisted task metadata; this command does not resume execution."""
    tasks = agent.task_state.list_tasks()
    if not tasks:
        ui.console.print("[dim]No persisted task records for this workspace.[/dim]")
        return
    table = Table(title="Persisted Tasks", border_style=ui.theme_color, expand=False)
    table.add_column("Task ID", style=ui.accent_color, no_wrap=True)
    table.add_column("Stage")
    table.add_column("Updated")
    table.add_column("State")
    table.add_column("Recovery")
    for item in tasks[:20]:
        recovery = agent.task_state.assess_recovery(item["task_id"])
        table.add_row(item["task_id"], item["stage"], item["updated_at"][:19],
                      item["status"], recovery.get("action", "unknown"))
    ui.console.print(table)
    ui.console.print("[dim]Recovery is advisory only. In-flight mutations require manual workspace reconciliation; automatic task continuation is not implemented.[/dim]")


def cmd_task_status(ui: ConsoleUI, agent: ZXAgentPro, task_id: str) -> None:
    """Show a task checkpoint and per-node acceptance/recovery evidence."""
    try:
        loaded = agent.task_state.load(task_id)
    except ValueError as exc:
        ui.print_warning(str(exc))
        return
    if loaded.get("status") != "stored":
        ui.print_warning(f"Task state unavailable: {loaded.get('status', 'unknown')}")
        return
    state = loaded["state"]
    payload = state.get("payload", {})
    graph = payload.get("task_graph", {})
    cursor = payload.get("execution_cursor", {})
    recovery = agent.task_state.assess_recovery(task_id)
    fingerprint = state.get("workspace_fingerprint", {})
    ui.console.print(Panel(
        f"[bold]Task:[/bold] {escape(task_id)}\n"
        f"[bold]Stage:[/bold] {escape(str(state.get('stage', 'unknown')))}\n"
        f"[bold]Updated:[/bold] {escape(str(state.get('updated_at', '')))}\n"
        f"[bold]Recovery:[/bold] {escape(str(recovery.get('action', 'unknown')))} — {escape(str(recovery.get('reason', '')))}\n"
        f"[bold]Workspace fingerprint:[/bold] {escape(str(fingerprint.get('status', 'missing')))}\n"
        f"[bold]Cursor:[/bold] {escape(str(cursor.get('phase', 'unknown')))} / {escape(str(cursor.get('current_node_id', '-')))}",
        title="Task checkpoint", border_style=ui.theme_color))
    nodes = graph.get("nodes", []) if isinstance(graph, dict) else []
    if nodes:
        table = Table(title="DAG node status", border_style=ui.theme_color)
        table.add_column("Node", no_wrap=True)
        table.add_column("Status")
        table.add_column("Acceptance")
        table.add_column("Evidence")
        for node in nodes:
            if not isinstance(node, dict):
                continue
            acceptance = node.get("acceptance", {})
            table.add_row(escape(str(node.get("id", ""))),
                          escape(str(node.get("status", "unknown"))),
                          ("PASS" if acceptance.get("accepted") else
                           "NOT RECORDED" if not acceptance else "FAIL"),
                          escape(str(acceptance.get("evidence", node.get("error_log", "")))[:100]))
        ui.console.print(table)


def cmd_recovery_doctor(ui: ConsoleUI, agent: ZXAgentPro, task_id: str = "") -> None:
    """Print a read-only recovery report; never repairs or resumes a task."""
    from execution.recovery_doctor import build_recovery_report
    try:
        report = build_recovery_report(agent.task_state, task_id.strip() or None)
        ui.console.print(Panel(
            escape(json.dumps(report, ensure_ascii=False, indent=2)),
            title=f"Recovery Doctor — {report.get('task_id') or 'active task'} (DRY RUN)",
            border_style=ui.warn_color if report.get("overall") != "safe_boundary" else ui.theme_color,
        ))
        ui.console.print("[dim]Read-only diagnostic. No resume, rollback, repair, or workspace mutation was performed.[/dim]")
    except (ValueError, OSError) as exc:
        ui.print_error(f"Recovery diagnostic failed: {exc}")


def cmd_discard_task(ui: ConsoleUI, agent: ZXAgentPro, task_id: str) -> None:
    """Delete only the selected task metadata after explicit confirmation."""
    try:
        loaded = agent.task_state.load(task_id)
        if loaded.get("status") != "stored":
            ui.print_warning(f"Task state unavailable: {loaded.get('status', 'unknown')}")
            return
        if not Confirm.ask(
                f"Discard checkpoint metadata for {task_id}? Workspace files will NOT be changed.",
                default=False):
            ui.console.print("[dim]Task checkpoint retained.[/dim]")
            return
        agent.task_state.clear(task_id)
        ui.print_success(f"Checkpoint metadata discarded for {task_id}. Workspace files were not changed.")
    except (ValueError, OSError) as exc:
        ui.print_error(f"Could not discard task checkpoint: {exc}")


def confirm_plugin_activation(ui: ConsoleUI, preview: str) -> bool:
    ui.console.print(Panel(escape(preview), title="Python plugin review", border_style=ui.warn_color))
    try:
        return bool(Confirm.ask("Activate this reviewed plugin for this session?", default=False))
    except (EOFError, KeyboardInterrupt):
        return False


def confirm_skill_installation(ui: ConsoleUI, preview: str) -> bool:
    ui.console.print(Panel(escape(preview), title="Skill installation preview", border_style=ui.warn_color))
    try:
        return bool(Confirm.ask("Install and save this reviewed skill?", default=False))
    except (EOFError, KeyboardInterrupt):
        return False


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------
def cmd_list_models(ui: ConsoleUI, cfg: AppConfig) -> None:
    pinfo = PROVIDERS.get(cfg.provider, {})
    ui.console.print(f"\n[{ui.theme_color}]Available models for [bold]{cfg.provider}[/bold]:[/{ui.theme_color}]")
    for m in pinfo.get("models", []):
        marker = " *" if m == cfg.model else ""
        ui.console.print(f"  [white]{m}[/white]{marker}")
    ui.console.print("")

def cmd_list_providers(ui: ConsoleUI, cfg: AppConfig) -> None:
    ui.console.print(f"\n[{ui.theme_color}]Available providers:[/{ui.theme_color}]")
    for k, info in PROVIDERS.items():
        active = " *" if k == cfg.provider else ""
        ui.console.print(f"  [white]{k:12}[/white] {info['name']}{active}")
    ui.console.print("")

def cmd_multi_ai_config(ui: ConsoleUI, settings_mgr: SettingsManager, cfg: AppConfig) -> AppConfig:
    """Interactive Multi-AI routing configuration."""
    ui.console.print(f"\n[{ui.theme_color}]🧠 Multi-AI Routing Setup[/{ui.theme_color}]")
    ui.console.print("Assign different roles to different models, like professional agent setups.")
    ui.console.print("[dim]Roles: architect, planner, coder, debugger, reviewer, tester, security_auditor[/dim]\n")

    roles = ["architect", "planner", "coder", "debugger", "reviewer", "tester", "security_auditor"]
    cfg.multi_ai = {}
    for role in roles:
        enable = Confirm.ask(f"Use a dedicated model for [{ui.accent_color}]{role}[/{ui.accent_color}]?", default=False)
        if not enable:
            continue
        prov = Prompt.ask(f"  Provider for {role}", default=cfg.provider)
        if prov not in PROVIDERS:
            ui.print_warning(f"Unknown provider '{prov}', skipping.")
            continue
        default_model = PROVIDERS[prov]["default_model"]
        model = Prompt.ask(f"  Model for {role}", default=default_model)
        if prov in ("ollama", "9router"):
            key = "dummy"
        else:
            key = Prompt.ask(f"  API key for {role} (leave blank to reuse main)", password=True,
                             default="")
        cfg.multi_ai[role] = {
            "provider": prov,
            "model": model,
            "api_key": key or cfg.api_key,
        }
    settings_mgr.save(cfg)
    if cfg.multi_ai:
        ui.print_success("Multi-AI routing configured.")
        for role, spec in cfg.multi_ai.items():
            ui.console.print(f"  [{ui.accent_color}]{role}[/{ui.accent_color}] -> {spec['provider']}:{spec['model']}")
    else:
        ui.console.print("[dim]Multi-AI routing disabled.[/dim]")
    return cfg


def cmd_skill_select(ui: ConsoleUI, settings_mgr: SettingsManager, cfg: AppConfig,
                     agent: ZXAgentPro) -> None:
    manager = PluginManager(DEFAULT_SKILL_DIR, BUILTIN_SKILL_DIR)
    plugins = manager.list_installed_plugins()
    if not plugins:
        ui.print_error("No skills found. Use /install <url> or check built-in skills.")
        return

    ui.console.print(f"\n[{ui.theme_color}]🧠 Select Skill Persona[/{ui.theme_color}]")
    for i, p in enumerate(plugins, 1):
        p_name = p.get("name", p["_folder_name"])
        desc = p.get("description", "")
        badge = "[dim](built-in)[/dim]" if p.get("_builtin") else ""
        ui.console.print(f"  {i}. [white]{p_name}[/white] {badge} [dim]{desc[:50]}[/dim]")
    ui.console.print("  0. [dim]Reset skill (deactivate)[/dim]")

    choice = Prompt.ask("\nSelect skill number", default="0")
    if choice == "0":
        cfg.active_skill_name = None
        cfg.active_skill_content = None
        settings_mgr.save(cfg)
        agent.skill_content = ""
        ui.console.print("[dim]Skill reset to default.[/dim]\n")
        return
    try:
        idx = int(choice) - 1
        selected = plugins[idx]
    except (ValueError, IndexError):
        ui.print_error("Invalid selection.")
        return

    content = manager.get_plugin_prompt(selected["_folder_name"])
    if not content:
        ui.print_error(f"Failed to read SKILL.md from '{selected['_folder_name']}'.")
        return
    cfg.active_skill_name = selected.get("name", selected["_folder_name"])
    cfg.active_skill_content = content
    settings_mgr.save(cfg)
    agent.skill_content = content
    ui.print_success(f"Skill activated: {cfg.active_skill_name}")


def cmd_list_skills(ui: ConsoleUI) -> None:
    manager = PluginManager(DEFAULT_SKILL_DIR, BUILTIN_SKILL_DIR)
    plugins = manager.list_installed_plugins()
    ui.console.print(f"\n[{ui.theme_color}]Skills catalog ({len(plugins)}):[/{ui.theme_color}]")
    for p in plugins:
        badge = "[dim](built-in)[/dim]" if p.get("_builtin") else "[dim](installed)[/dim]"
        ui.console.print(f"  [white]{p.get('name', p['_folder_name']):24}[/white] {badge} "
                         f"[dim]{p.get('description', '')[:50]}[/dim]")
    ui.console.print("")


def cmd_install_skill(ui: ConsoleUI, url: str) -> None:
    parsed = urlsplit(url)
    safe_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    ui.console.print(f"[dim]Fetching {escape(safe_url)} ...[/dim]")
    try:
        installer = UniversalInstaller(DEFAULT_SKILL_DIR)
        status = installer.install_from_url(
            url, approval_callback=lambda preview: confirm_skill_installation(ui, preview))
        ui.print_success(status)
    except Exception as e:
        ui.print_error(f"Failed to install skill: {e}")


def cmd_session_stats(ui: ConsoleUI, agent: ZXAgentPro) -> None:
    ui.console.print(f"\n[{ui.theme_color}]Session stats:[/{ui.theme_color}]")
    ui.console.print(f"  [white]History messages:[/white] {len(agent.memory.get_chat_history())}")
    ui.console.print(f"  [white]Indexed files:[/white] {len(agent.indexer.indexed_data)}")
    ui.console.print(f"  [white]Workspace:[/white] {agent.workspace}")
    ui.console.print(f"  [white]Model:[/white] {getattr(agent.provider, 'model', '?')}")
    ui.console.print("")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
async def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} — professional coding agent")
    parser.add_argument("--dir", type=str, help="Project folder (workspace)")
    parser.add_argument("--setup", action="store_true", help="Run API configuration")
    parser.add_argument("--provider", type=str, help="Provider id (e.g. deepseek, nararouter)")
    parser.add_argument("--model", type=str, help="Model name override")
    parser.add_argument("--theme", type=str, help=f"UI theme (default {DEFAULT_THEME})")
    parser.add_argument("--skill", type=str, help="Skill folder name to activate on start")
    parser.add_argument("--allow-edits", action="store_true",
                        help="Allow native write_file/edit_file inside the workspace "
                             "(off by default; asks for confirmation)")
    parser.add_argument("-c", "--continue", dest="continue_session", action="store_true",
                        help="Resume the saved conversation for this folder")
    parser.add_argument("--max-iters", type=int,
                        help="Max tool-use iterations per request (default 30, range 1-200)")
    parser.add_argument("--plan", choices=("auto", "always", "never"), default="auto",
                        help="When to run the planner (default auto: skip greetings/short questions)")
    parser.add_argument("--sandbox-mode", choices=("container", "host"), default="container",
                        help="Command execution backend (container is fail-closed default)")
    parser.add_argument("--allow-host-execution", action="store_true",
                        help="Explicitly allow UNSAFE host shell execution when --sandbox-mode host is selected")
    parser.add_argument("--sandbox-runtime", type=str, help="Docker/Podman executable (auto-detected by default)")
    parser.add_argument("--sandbox-image", type=str, default="zxcode-sandbox:latest",
                        help="Container image for command execution")
    parser.add_argument("--sandbox-network", choices=("none", "bridge"), default="none",
                        help="Container network policy (none by default; bridge requires explicit choice)")
    parser.add_argument("--version", action="store_true", help="Show version")
    args = parser.parse_args()
    if args.sandbox_mode == "host" and not args.allow_host_execution:
        parser.error("--sandbox-mode host requires --allow-host-execution (unsafe; no OS isolation)")
    if args.allow_host_execution and args.sandbox_mode != "host":
        parser.error("--allow-host-execution is valid only with --sandbox-mode host")

    if args.version:
        print(f"{APP_NAME} v{APP_VERSION}")
        return

    ui = ConsoleUI()
    settings_mgr = SettingsManager()
    cfg: AppConfig = settings_mgr.load()

    if args.theme:
        cfg.theme = args.theme
    ui.set_theme(cfg.theme or DEFAULT_THEME)

    if args.setup or not cfg.api_key:
        cfg = run_setup(ui, settings_mgr, cfg)
        if not cfg.api_key:
            sys.exit(1)

    # CLI overrides
    if args.provider:
        cfg.provider = args.provider
        cfg.base_url = PROVIDERS.get(cfg.provider, {}).get("base_url", cfg.base_url)
    if args.model:
        cfg.model = args.model

    # Workspace resolution
    workspace = Path.cwd()
    if args.dir:
        workspace = Path(args.dir).expanduser().resolve()
    elif cfg.default_folder:
        workspace = Path(cfg.default_folder).expanduser().resolve()
    workspace.mkdir(parents=True, exist_ok=True)

    # Provider bootstrap
    try:
        provider = get_provider(cfg.provider, cfg.api_key, cfg.model, cfg.base_url)
    except Exception as e:
        ui.print_error(f"Failed to load provider: {e}")
        sys.exit(1)

    # Multi-AI routing
    role_router = build_multi_ai_router(cfg, provider)

    agent_config = AgentConfig(planning=args.plan, sandbox_mode=args.sandbox_mode,
        allow_host_execution=args.allow_host_execution, sandbox_runtime=args.sandbox_runtime,
        sandbox_image=args.sandbox_image, sandbox_network=args.sandbox_network)
    if args.max_iters:
        agent_config.max_iterations = max(1, min(args.max_iters, 200))
    agent = ZXAgentPro(
        provider=provider,
        workspace=workspace,
        skill_content=cfg.active_skill_content or "",
        confirm_callback=lambda cmd: confirm_shell_command(ui, cmd),
        role_router=role_router,
        plugin_approval_callback=lambda preview: confirm_plugin_activation(ui, preview),
        config=agent_config,
        session_store=SessionStore(),
    )
    # Native file edits are opt-in per session; --allow-edits still asks once.
    if args.allow_edits:
        if confirm_enable_file_edits(ui, workspace):
            agent.tool_engine.set_file_mutations_enabled(True)
        else:
            ui.print_warning("Native file edits stay OFF.")
    if args.continue_session:
        cmd_resume(ui, agent)

    # Skill activation from CLI
    if args.skill:
        manager = PluginManager(DEFAULT_SKILL_DIR, BUILTIN_SKILL_DIR)
        content = manager.get_plugin_prompt(args.skill)
        if content:
            cfg.active_skill_name = args.skill
            cfg.active_skill_content = content
            agent.skill_content = content
            settings_mgr.save(cfg)
        else:
            ui.print_warning(f"Skill '{args.skill}' not found.")

    ui.show_splash()
    ui.print_header(provider=PROVIDERS.get(cfg.provider, {}).get("name", cfg.provider),
                    model=cfg.model, workspace=workspace)

    edits_state = "ON" if agent.tool_engine.file_mutations_enabled else "OFF (use --allow-edits or /edits on)"
    ui.console.print(f"[dim]✏️  Native file edits: [bold]{edits_state}[/bold][/dim]\n")

    if cfg.active_skill_name:
        ui.console.print(f"[dim]🧠 Active Skill: [bold]{cfg.active_skill_name}[/bold][/dim]\n")
    if role_router:
        ui.console.print(f"[dim]🧠 Multi-AI routing active: "
                         f"{', '.join(role_router.backends.keys())}[/dim]\n")

    while True:
        try:
            user_input = await ui.ask_user()
            if not user_input:
                continue

            cmd = user_input.lower()

            if cmd in ("/exit", "/quit", "/q"):
                break

            elif cmd == "/clear":
                agent.memory.conversation.history = []
                ui.clear_screen()
                ui.print_header(provider=PROVIDERS.get(cfg.provider, {}).get("name", cfg.provider),
                                model=cfg.model, workspace=workspace)
                if cfg.active_skill_name:
                    ui.console.print(f"[dim]🧠 Active Skill: {cfg.active_skill_name}[/dim]\n")

            elif cmd == "/help":
                ui.show_command_menu()

            elif cmd == "/setup":
                cfg = run_setup(ui, settings_mgr, cfg)
                provider = get_provider(cfg.provider, cfg.api_key, cfg.model, cfg.base_url)
                agent.provider = provider
                role_router = build_multi_ai_router(cfg, provider)
                agent.role_router = role_router
                ui.clear_screen()
                ui.print_header(provider=PROVIDERS.get(cfg.provider, {}).get("name", cfg.provider),
                                model=cfg.model, workspace=workspace)

            elif cmd == "/model":
                parts = user_input.split(" ", 1)
                if len(parts) > 1:
                    model = parts[1].strip()
                    if model in PROVIDERS.get(cfg.provider, {}).get("models", []):
                        cfg.model = model
                        settings_mgr.save(cfg)
                        agent.provider = get_provider(cfg.provider, cfg.api_key, cfg.model, cfg.base_url)
                        ui.print_success(f"Model switched to {model}")
                        ui.update_tokens(0)
                    else:
                        ui.print_warning(f"Model '{model}' not in catalog for {cfg.provider}.")
                else:
                    cmd_list_models(ui, cfg)

            elif cmd == "/provider":
                parts = user_input.split(" ", 1)
                if len(parts) > 1:
                    prov = parts[1].strip().lower()
                    if prov in PROVIDERS:
                        cfg.provider = prov
                        cfg.base_url = PROVIDERS[prov]["base_url"]
                        cfg.model = PROVIDERS[prov]["default_model"]
                        settings_mgr.save(cfg)
                        agent.provider = get_provider(cfg.provider, cfg.api_key, cfg.model, cfg.base_url)
                        ui.print_success(f"Provider switched to {PROVIDERS[prov]['name']}")
                    else:
                        ui.print_warning(f"Unknown provider '{prov}'.")
                else:
                    cmd_list_providers(ui, cfg)

            elif cmd == "/multi":
                cfg = cmd_multi_ai_config(ui, settings_mgr, cfg)
                role_router = build_multi_ai_router(cfg, provider)
                agent.role_router = role_router

            elif cmd == "/skill":
                cmd_skill_select(ui, settings_mgr, cfg, agent)

            elif cmd == "/skills":
                cmd_list_skills(ui)

            elif cmd.startswith("/install "):
                url = user_input.split(" ", 1)[1].strip()
                cmd_install_skill(ui, url)

            elif cmd.startswith("/theme"):
                parts = user_input.split(" ", 1)
                if len(parts) > 1:
                    theme_name = parts[1].strip()
                    if theme_name in ui.available_themes:
                        ui.set_theme(theme_name)
                        cfg.theme = theme_name
                        settings_mgr.save(cfg)
                        ui.clear_screen()
                        ui.print_header(provider=PROVIDERS.get(cfg.provider, {}).get("name", cfg.provider),
                                        model=cfg.model, workspace=workspace)
                        ui.print_success(f"Theme changed to '{theme_name}'")
                    else:
                        ui.print_warning(f"Unknown theme '{theme_name}'. Available: {', '.join(ui.available_themes)}")
                else:
                    ui.console.print(f"\n[{ui.theme_color}]Available themes:[/{ui.theme_color}]")
                    ui.console.print(f"  [white]{', '.join(ui.available_themes)}[/white]\n")

            elif cmd == "/session":
                cmd_session_stats(ui, agent)

            elif cmd == "/confirm":
                cmd_disable_legacy_auto_confirm(ui, settings_mgr, cfg)

            elif cmd == "/edits" or cmd.startswith("/edits "):
                cmd_edits(ui, agent, cmd[len("/edits"):])

            elif cmd == "/resume":
                cmd_resume(ui, agent)

            elif cmd == "/tasks":
                cmd_tasks(ui, agent)

            elif cmd == "/recovery-doctor" or cmd.startswith("/recovery-doctor "):
                parts = user_input.split(" ", 1)
                cmd_recovery_doctor(ui, agent, parts[1] if len(parts) > 1 else "")

            elif cmd.startswith("/task-status"):
                parts = user_input.split(" ", 1)
                if len(parts) != 2 or not parts[1].strip():
                    ui.print_warning("Usage: /task-status <task-id>")
                else:
                    cmd_task_status(ui, agent, parts[1].strip())

            elif cmd.startswith("/discard-task"):
                parts = user_input.split(" ", 1)
                if len(parts) != 2 or not parts[1].strip():
                    ui.print_warning("Usage: /discard-task <task-id>")
                else:
                    cmd_discard_task(ui, agent, parts[1].strip())

            elif cmd.startswith("/resume-task"):
                parts = user_input.split(" ", 1)
                if len(parts) != 2 or not parts[1].strip():
                    ui.print_warning("Usage: /resume-task <task-id>")
                else:
                    ui.start_activity()
                    try:
                        async for event in agent.resume_task_stream(parts[1].strip()):
                            ui.handle_stream_event(event)
                    finally:
                        ui.stop_activity()

            elif cmd == "/new":
                cmd_new(ui, agent)

            else:
                # Normal agent execution.
                ui.update_tokens(len(agent.memory.get_chat_history()))
                ui.start_activity()
                try:
                    async for event in agent.chat_stream(user_input):
                        ui.handle_stream_event(event)
                finally:
                    ui.stop_activity()

        except KeyboardInterrupt:
            ui.console.print("\n[dim]Use /exit to quit.[/dim]")
        except EOFError:
            break
        except Exception as e:
            ui.print_error(str(e))
            logger.error(f"Runtime error: {e}", exc_info=True)


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
