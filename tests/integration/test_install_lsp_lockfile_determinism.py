"""Integration regression for repeated installs with unchanged LSP dependencies."""

from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

from apm_cli.cli import cli
from apm_cli.deps.lockfile import LockFile


def _snapshot_tree(root: Path) -> dict[Path, bytes]:
    """Capture every file under a root for a recursive no-write assertion."""
    return {path.relative_to(root): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _write_lsp_manifest(project_root: Path) -> None:
    """Create a project with one root LSP dependency."""
    dep_root = project_root / "packages" / "dep"
    dep_root.mkdir(parents=True)
    (dep_root / "apm.yml").write_text(
        'name: dep\nversion: "1.0.0"\n',
        encoding="utf-8",
    )
    instructions = dep_root / ".apm" / "instructions"
    instructions.mkdir(parents=True)
    (instructions / "dep.instructions.md").write_text("# Dependency\n", encoding="utf-8")
    (project_root / "apm.yml").write_text(
        """
name: lsp-lockfile-determinism
version: "1.0.0"
dependencies:
  apm:
    - ./packages/dep
  lsp:
    - name: pyright
      command: pyright-langserver
      extensionToLanguage:
        .py: python
""".lstrip(),
        encoding="utf-8",
    )
    github_dir = project_root / ".github"
    github_dir.mkdir()
    (github_dir / "copilot-instructions.md").write_text("# Test project\n", encoding="utf-8")


def _write_lsp_only_manifest(project_root: Path) -> None:
    """Create a project whose only dependency is one LSP server."""
    (project_root / "apm.yml").write_text(
        """
name: lsp-only
version: "1.0.0"
dependencies:
  lsp:
    - name: pyright
      command: pyright-langserver
      extensionToLanguage:
        .py: python
""".lstrip(),
        encoding="utf-8",
    )
    github_dir = project_root / ".github"
    github_dir.mkdir()
    (github_dir / "copilot-instructions.md").write_text("# Test project\n", encoding="utf-8")


@patch("apm_cli.commands.install._validate_package_exists", return_value=True)
@patch("apm_cli.commands._helpers.check_for_updates", return_value=None)
def test_global_dry_run_leaves_the_entire_home_tree_unchanged(
    _mock_updates,
    _mock_validate,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A global preview must not bootstrap user config, manifests, or modules."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    before = _snapshot_tree(home)

    result = runner.invoke(
        cli,
        [
            "install",
            "microsoft/apm-sample-package",
            "--global",
            "--dry-run",
            "--target",
            "copilot",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "APM dependencies (1)" in result.output
    assert "microsoft/apm-sample-package" in result.output
    assert _snapshot_tree(home) == before


@patch("apm_cli.commands._helpers.check_for_updates", return_value=None)
def test_lsp_only_install_and_dry_run_are_isolated_and_stable(
    _mock_updates,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """LSP-only previews and installs must not mutate APM or MCP state."""
    _write_lsp_only_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    manifest_path = tmp_path / "apm.yml"
    manifest_bytes = manifest_path.read_bytes()
    lock_path = tmp_path / "apm.lock.yaml"
    lsp_path = tmp_path / ".github" / "lsp.json"

    preview = runner.invoke(cli, ["install", "--dry-run", "--only", "lsp", "--target", "copilot"])

    assert preview.exit_code == 0, preview.output
    assert "LSP dependencies (1)" in preview.output
    assert "pyright" in preview.output
    assert manifest_path.read_bytes() == manifest_bytes
    assert not lock_path.exists()
    assert not lsp_path.exists()
    assert not (tmp_path / "apm_modules").exists()
    assert not (tmp_path / ".vscode" / "mcp.json").exists()

    installed = runner.invoke(cli, ["install", "--only", "lsp", "--target", "copilot"])

    assert installed.exit_code == 0, installed.output
    assert lsp_path.exists()
    assert "pyright" in lsp_path.read_text(encoding="utf-8")
    assert not (tmp_path / "apm_modules").exists()
    assert not (tmp_path / ".vscode" / "mcp.json").exists()

    installed_manifest_bytes = manifest_path.read_bytes()
    installed_lock_bytes = lock_path.read_bytes()
    installed_lsp_bytes = lsp_path.read_bytes()
    stable_preview = runner.invoke(
        cli,
        ["install", "--dry-run", "--only", "lsp", "--target", "copilot"],
    )

    assert stable_preview.exit_code == 0, stable_preview.output
    assert "LSP dependencies (1)" in stable_preview.output
    assert manifest_path.read_bytes() == installed_manifest_bytes
    assert lock_path.read_bytes() == installed_lock_bytes
    assert lsp_path.read_bytes() == installed_lsp_bytes


@patch("apm_cli.commands._helpers.check_for_updates", return_value=None)
def test_repeated_install_with_unchanged_lsp_keeps_lockfile_bytes(
    _mock_updates,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """A second real CLI install must leave the LSP lockfile byte-identical."""
    _write_lsp_manifest(tmp_path)
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()

    first_result = runner.invoke(cli, ["install", "--target", "copilot"])
    assert first_result.exit_code == 0, first_result.output

    lock_path = tmp_path / "apm.lock.yaml"
    first_bytes = lock_path.read_bytes()
    first_lock = LockFile.read(lock_path)
    assert first_lock is not None
    assert first_lock.lsp_servers == ["pyright"]

    second_result = runner.invoke(cli, ["install", "--target", "copilot"])
    assert second_result.exit_code == 0, second_result.output

    second_lock = LockFile.read(lock_path)
    assert second_lock is not None
    assert second_lock.generated_at == first_lock.generated_at
    assert second_lock.lsp_servers == first_lock.lsp_servers
    assert second_lock.lsp_configs == first_lock.lsp_configs
    assert lock_path.read_bytes() == first_bytes
