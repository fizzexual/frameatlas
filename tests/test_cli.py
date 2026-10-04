import json

from frameatlas.cli import main


def test_cli_read_and_verify(dataset, capsys):
    assert main(["info", str(dataset.root)]) == 0
    assert json.loads(capsys.readouterr().out)["frame_count"] == 17
    assert main(["frame", str(dataset.root), "--index", "16"]) == 0
    assert json.loads(capsys.readouterr().out)["index"] == 16
    assert main(["verify", str(dataset.root)]) == 0
    assert json.loads(capsys.readouterr().out)["frames_verified"] == 17


def test_cli_bad_source_returns_error(tmp_path, capsys):
    source = tmp_path / "bad.mp4"
    source.write_bytes(b"not a video")
    assert main(["extract", str(source), "--output", str(tmp_path / "dataset")]) == 1
    output = capsys.readouterr()
    assert not output.out
    assert "frameatlas:" in output.err
    assert "Traceback" not in output.err


def test_cli_sheet_refuses_dataset_mutation(dataset, capsys):
    assert main(["sheet", str(dataset.root), "--output", str(dataset.root / "sheet.png")]) == 1
    assert "outside the dataset" in capsys.readouterr().err
