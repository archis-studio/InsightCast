import subprocess
import tomllib
from pathlib import Path

from insightcast.core.config import Settings

ROOT = Path(__file__).parents[1]


def test_required_repository_files_and_console_script_exist() -> None:
    for relative_path in [
        ".gitignore",
        ".env.example",
        ".python-version",
        "AGENTS.md",
        "README.md",
        "build_backend.py",
        "pyproject.toml",
        "uv.lock",
        "src",
        "tests",
        "outputs/.gitkeep",
    ]:
        assert (ROOT / relative_path).exists(), relative_path

    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["scripts"]["cast_api"] == "insightcast.api.app:run"
    assert (
        pyproject["project"]["scripts"]["cast_analyze"]
        == "insightcast.cli.analyze:main"
    )
    assert pyproject["project"]["requires-python"] == ">=3.13"
    assert pyproject["tool"]["ruff"]["target-version"] == "py313"
    assert (ROOT / ".python-version").read_text(encoding="utf-8").strip() == "3.13"


def test_agents_documents_canonical_analysis_workflow() -> None:
    instructions = (ROOT / "AGENTS.md").read_text(encoding="utf-8")

    for required_text in [
        "uv run cast_api",
        'uv run cast_analyze "<youtube-url>"',
        "--verbose",
        "WAITING_SELECTION",
        "pipeline.log",
        "Do not start or stop",
        "Do not queue renders",
    ]:
        assert required_text in instructions


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
        "Python 3.13",
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


def test_docker_contract_uses_cpu_python_ffmpeg_non_root_and_documented_volume() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "FROM python:3.13-slim" in dockerfile
    assert "ffmpeg" in dockerfile
    assert "fonts-noto-cjk" in dockerfile
    assert "USER app" in dockerfile
    assert "EXPOSE 8765" in dockerfile
    assert 'CMD ["uv", "run", "--no-sync", "cast_api"]' in dockerfile
    assert ".env" in dockerignore
    assert ".venv" in dockerignore
    assert "outputs" in dockerignore
    assert "docker build -t insightcast ." in readme
    assert "--env-file .env" in readme
    assert '$(pwd)/outputs:/app/outputs' in readme


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


def test_synced_virtualenv_can_import_installed_console_package() -> None:
    python = ROOT / ".venv" / "bin" / "python"
    result = subprocess.run(
        [
            str(python),
            "-c",
            "from insightcast.api.app import run; assert callable(run)",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
