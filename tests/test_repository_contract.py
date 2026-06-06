import subprocess
import tomllib
from pathlib import Path

from insightcast.core.config import Settings

ROOT = Path(__file__).parents[1]


def test_required_repository_files_and_console_script_exist() -> None:
    for relative_path in [
        ".gitignore",
        ".env.example",
        "README.md",
        "pyproject.toml",
        "uv.lock",
        "src",
        "tests",
        "outputs/.gitkeep",
    ]:
        assert (ROOT / relative_path).exists(), relative_path

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["cast_api"] == "insightcast.api.app:run"


def test_env_example_documents_every_settings_field() -> None:
    env_text = (ROOT / ".env.example").read_text(encoding="utf-8")
    env_keys = {
        line.split("=", 1)[0]
        for line in env_text.splitlines()
        if line and not line.startswith("#") and "=" in line
    }
    expected_keys = {field_name.upper() for field_name in Settings.model_fields}

    assert expected_keys <= env_keys


def test_secrets_generated_outputs_caches_and_worktrees_are_ignored() -> None:
    paths = [
        ".env",
        ".venv/example",
        ".work/temp.mp4",
        ".worktrees/feature/file.py",
        "outputs/generated.mp4",
        "src/insightcast/__pycache__/module.pyc",
    ]
    result = subprocess.run(
        ["git", "check-ignore", *paths],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert set(result.stdout.splitlines()) == set(paths)


def test_readme_documents_local_mvp_operations_without_docker_yet() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    required_text = [
        "Python 3.12",
        "uv sync",
        "uv run pytest",
        "uv run cast_api",
        "http://127.0.0.1:8765/docs",
        "POST /api/v1/analysis-jobs",
        "POST /api/v1/direct-render-jobs",
        "UPLOAD_NOT_IMPLEMENTED",
        "OPENAI_API_KEY",
        "TRANSCRIPTION_PROVIDER",
        "FFmpeg",
        "outputs/",
        "著作權",
    ]
    for text in required_text:
        assert text in readme, text
    assert "docker build" not in readme.lower()


def test_git_does_not_track_secrets_or_generated_media() -> None:
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()

    assert ".env" not in tracked
    assert all(not path.endswith((".mp3", ".mp4", ".ass", ".srt")) for path in tracked)

