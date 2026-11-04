from pix.cli import main


def test_main_reports_not_implemented(capsys):
    assert main() == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "not implemented" in captured.err
