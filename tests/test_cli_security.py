from types import SimpleNamespace

import main


def test_dangerous_shell_command_still_requires_per_action_approval(monkeypatch):
    prompts = []
    monkeypatch.setattr(main.Confirm, "ask",
                        lambda prompt, default=False: prompts.append(prompt) or False)
    ui = SimpleNamespace(warn_color="yellow")

    assert main.confirm_shell_command(ui, "rm -rf /tmp/example") is False
    assert len(prompts) == 1
    assert "rm -rf /tmp/example" in prompts[0]


def test_shell_authorization_fails_closed_when_confirmation_is_unavailable(monkeypatch):
    def unavailable(*args, **kwargs):
        raise EOFError()
    monkeypatch.setattr(main.Confirm, "ask", unavailable)

    assert main.confirm_shell_command(SimpleNamespace(warn_color="yellow"), "printf safe") is False


def test_confirm_command_only_disables_legacy_auto_approval():
    cfg = SimpleNamespace(auto_confirm_dangerous=True)
    saved = []
    messages = []
    settings = SimpleNamespace(save=lambda config: saved.append(config.auto_confirm_dangerous))
    ui = SimpleNamespace(print_warning=lambda message: messages.append(message))

    main.cmd_disable_legacy_auto_confirm(ui, settings, cfg)

    assert cfg.auto_confirm_dangerous is False
    assert saved == [False]
    assert any("per-command" in message.lower() for message in messages)
