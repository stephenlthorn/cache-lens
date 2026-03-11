import pytest
from pathlib import Path
from cachelens.installer import (
    write_env_to_shell_file,
    remove_env_from_shell_file,
    is_port_in_use,
    detect_platform,
)


def test_detect_platform_returns_macos_or_linux():
    platform = detect_platform()
    assert platform in ("macos", "linux")


def test_is_port_in_use_returns_bool():
    # Port 1 is almost certainly not in use
    result = is_port_in_use(1)
    assert isinstance(result, bool)


def test_write_env_to_shell_file_creates_file(tmp_path):
    shell_file = tmp_path / ".zshrc"
    write_env_to_shell_file(shell_file, port=8420)
    content = shell_file.read_text()
    assert "ANTHROPIC_BASE_URL" in content
    assert "localhost:8420" in content


def test_write_env_to_shell_file_sets_all_env_vars(tmp_path):
    shell_file = tmp_path / ".zshrc"
    write_env_to_shell_file(shell_file, port=8420)
    content = shell_file.read_text()
    assert "ANTHROPIC_BASE_URL" in content
    assert "OPENAI_BASE_URL" in content
    assert "GOOGLE_AI_BASE_URL" in content


def test_write_env_backs_up_existing_value(tmp_path):
    shell_file = tmp_path / ".zshrc"
    shell_file.write_text('export ANTHROPIC_BASE_URL="https://old-proxy.example.com"\n')
    write_env_to_shell_file(shell_file, port=8420)
    content = shell_file.read_text()
    # Backup comment present
    assert "old-proxy.example.com" in content
    # New value set
    assert "localhost:8420" in content


def test_write_env_is_idempotent(tmp_path):
    shell_file = tmp_path / ".zshrc"
    write_env_to_shell_file(shell_file, port=8420)
    write_env_to_shell_file(shell_file, port=8420)
    content = shell_file.read_text()
    # ANTHROPIC_BASE_URL should appear exactly once (not duplicated)
    assert content.count("ANTHROPIC_BASE_URL=") == 1


def test_remove_env_restores_backed_up_value(tmp_path):
    shell_file = tmp_path / ".zshrc"
    shell_file.write_text('export ANTHROPIC_BASE_URL="https://old-proxy.example.com"\n')
    write_env_to_shell_file(shell_file, port=8420)
    remove_env_from_shell_file(shell_file)
    content = shell_file.read_text()
    assert "localhost:8420" not in content
    assert "old-proxy.example.com" in content


def test_remove_env_from_file_with_no_cachelens_vars(tmp_path):
    shell_file = tmp_path / ".zshrc"
    shell_file.write_text('export PATH="/usr/local/bin:$PATH"\n')
    remove_env_from_shell_file(shell_file)  # should not raise
    content = shell_file.read_text()
    assert 'PATH="/usr/local/bin:$PATH"' in content


def test_remove_env_removes_cachelens_block(tmp_path):
    shell_file = tmp_path / ".zshrc"
    write_env_to_shell_file(shell_file, port=8420)
    remove_env_from_shell_file(shell_file)
    content = shell_file.read_text()
    assert "ANTHROPIC_BASE_URL" not in content
    assert "OPENAI_BASE_URL" not in content
    assert "GOOGLE_AI_BASE_URL" not in content


def test_write_env_to_nonexistent_file_creates_it(tmp_path):
    shell_file = tmp_path / "new_shell_config"
    assert not shell_file.exists()
    write_env_to_shell_file(shell_file, port=9000)
    assert shell_file.exists()
    content = shell_file.read_text()
    assert "localhost:9000" in content


def test_write_env_uses_specified_port(tmp_path):
    shell_file = tmp_path / ".zshrc"
    write_env_to_shell_file(shell_file, port=9999)
    content = shell_file.read_text()
    assert "localhost:9999" in content
    assert "localhost:8420" not in content


def test_write_env_idempotent_preserves_backup(tmp_path):
    # First write with existing value to create backup
    shell_file = tmp_path / ".zshrc"
    shell_file.write_text('export ANTHROPIC_BASE_URL="https://old.example.com"\n')
    write_env_to_shell_file(shell_file, port=8420)
    # Second write (idempotent) — backup should still be present
    write_env_to_shell_file(shell_file, port=8421)
    content = shell_file.read_text()
    assert "old.example.com" in content  # backup preserved
    assert "localhost:8421" in content   # new port applied
    export_lines = [l for l in content.splitlines() if l.startswith("export ANTHROPIC_BASE_URL=")]
    assert len(export_lines) == 1  # no duplicates


def test_remove_env_on_nonexistent_file(tmp_path):
    shell_file = tmp_path / ".nonexistent"
    remove_env_from_shell_file(shell_file)  # should not raise


def test_write_env_backup_stored_as_comment(tmp_path):
    shell_file = tmp_path / ".zshrc"
    shell_file.write_text('export ANTHROPIC_BASE_URL="https://old.example.com"\n')
    write_env_to_shell_file(shell_file, port=8420)
    content = shell_file.read_text()
    assert "# cachelens-backup:" in content
