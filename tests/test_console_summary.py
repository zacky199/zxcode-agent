from ui.console import ConsoleUI


def test_diff_summary_is_rendered_in_terminal(capsys):
    ui = ConsoleUI()
    ui.handle_stream_event({
        "type": "diff_summary",
        "content": "Workspace diff: 1 file change (+1 added).\nManual undo material: .zx_checkpoints/journals/task/manifest.json",
    })

    output = capsys.readouterr().out
    assert "Workspace diff" in output
    assert "Manual undo material" in output
