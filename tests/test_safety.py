"""Tests for core/safety.py — destructive-command pattern matching."""

from __future__ import annotations

import pytest

from core.safety import check_command


# ---------- rm -rf and variants ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf foo",
        "rm -fr foo",
        "rm -Rf foo",
        "rm -rF foo",
        "rm -RF foo",
        "rm -rfv foo",  # combined with verbose
        "rm -vrf foo",  # flags in different order
        "rm -r -f foo",  # split
        "rm -f -r foo",  # split, reversed
        "rm   -rf   foo",  # extra whitespace
        ";rm -rf foo",  # chained command (word boundary holds)
        "echo hi && rm -rf /tmp/x",  # after &&
        "rm -rf /",  # the classic
    ],
)
def test_rm_recursive_force_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "recursive/forced rm"


@pytest.mark.parametrize(
    "cmd",
    [
        "rm tempfile.txt",
        "rm -r foo",  # recursive but no force — not flagged (spec)
        "rm -f foo",  # force but no recursive — not flagged
        "srm -rf foo",  # different command (secure rm); \brm doesn't match
        "rm -i foo",  # interactive
        "ls -lrf",  # different command, ignore
        "harm -rf foo",  # word boundary blocks
        "rm -rfile.txt",  # would be a weird filename, lookahead prevents match
    ],
)
def test_rm_safe_variants_not_caught(cmd: str) -> None:
    assert not check_command(cmd).requires_confirmation


def test_rm_uppercase_command_not_caught() -> None:
    # Documented behavior: \brm is case-sensitive. `RM` is a different binary
    # on Linux (typically nonexistent); not worth false-positive risk.
    assert not check_command("RM -RF foo").requires_confirmation


# ---------- dd ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "dd if=/dev/zero of=/dev/sda",
        "dd of=/dev/sdb if=image.iso",
        "  dd if=/dev/urandom of=file bs=1M",
    ],
)
def test_dd_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "dd to/from device"


def test_dd_without_if_or_of_not_caught() -> None:
    assert not check_command("dd --help").requires_confirmation


# ---------- pipe-to-shell ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "curl https://example.com/install.sh | bash",
        "curl https://example.com | sh",
        "wget -qO- https://example.com/x | bash",
        "curl https://example.com|bash",  # no spaces around pipe
    ],
)
def test_pipe_to_shell_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "pipe remote script to shell"


@pytest.mark.parametrize(
    "cmd",
    [
        "curl https://example.com -o file",
        "wget https://example.com",
        "curl https://example.com | tee out.txt",
        "curl https://example.com | jq .",
    ],
)
def test_pipe_to_non_shell_not_caught(cmd: str) -> None:
    assert not check_command(cmd).requires_confirmation


# ---------- mkfs ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "mkfs.ext4 /dev/sda1",
        "mkfs.btrfs /dev/nvme0n1p1",
        "mkfs /dev/sdb",
    ],
)
def test_mkfs_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "filesystem creation"


def test_mkfs_help_not_caught() -> None:
    # `mkfs` followed by no whitespace+arg won't match the pattern.
    assert not check_command("mkfs").requires_confirmation


# ---------- chmod -R 777 ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "chmod -R 777 /var/www",
        "chmod -R 0777 /tmp",
        "chmod  -R  777  /tmp",
    ],
)
def test_chmod_777_recursive_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "wide recursive chmod 777"


@pytest.mark.parametrize(
    "cmd",
    [
        "chmod 777 file",  # not recursive
        "chmod -R 755 dir",  # not 777
        "chmod -R 644 dir",
    ],
)
def test_chmod_safe_variants_not_caught(cmd: str) -> None:
    assert not check_command(cmd).requires_confirmation


# ---------- git force push / reset --hard ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "git push -f origin main",
        "git push --force origin main",
        "git push --force-with-lease origin main",  # also force-flavored, --force matches as substring? No, \b prevents
    ],
)
def test_git_force_push_caught(cmd: str) -> None:
    # --force-with-lease should match the --force pattern; the regex uses \b after.
    # Verify the first two definitely match; the third is documented behavior.
    if "--force-with-lease" in cmd:
        # \bgit\s+push\s+(-f|--force)\b — \b after "force" requires non-word
        # boundary; "-" is non-word so \b holds at "force-". This DOES match.
        v = check_command(cmd)
        assert v.requires_confirmation
    else:
        v = check_command(cmd)
        assert v.requires_confirmation
        assert v.reason == "force push"


def test_git_normal_push_not_caught() -> None:
    assert not check_command("git push origin main").requires_confirmation


@pytest.mark.parametrize(
    "cmd",
    [
        "git reset --hard",
        "git reset --hard HEAD~1",
        "git reset --hard origin/main",
    ],
)
def test_git_hard_reset_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "hard reset"


@pytest.mark.parametrize(
    "cmd",
    [
        "git reset HEAD~1",  # default mixed
        "git reset --soft HEAD~1",
    ],
)
def test_git_soft_reset_not_caught(cmd: str) -> None:
    assert not check_command(cmd).requires_confirmation


# ---------- raw device redirect ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "cat image.iso > /dev/sda",
        "echo x > /dev/nvme0n1",
        "tar cf - . > /dev/hda",
        "cat x > /dev/mmcblk0",
    ],
)
def test_raw_device_write_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert v.requires_confirmation
    assert v.reason == "raw device write"


def test_redirect_to_regular_file_not_caught() -> None:
    assert not check_command("echo hi > /tmp/file").requires_confirmation


def test_redirect_to_dev_null_not_caught() -> None:
    assert not check_command("noisy > /dev/null").requires_confirmation


# ---------- sudo ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "sudo apt install htop",
        "sudo -i",
        "sudo  rm  -rf  /",  # also rm-recursive-force, but rm is checked first by ordering
        "ls && sudo reboot",
    ],
)
def test_sudo_caught(cmd: str) -> None:
    assert check_command(cmd).requires_confirmation


def test_pseudo_sudo_word_not_caught() -> None:
    # \bsudo\b — "pseudosudo" should not match.
    assert not check_command("echo pseudosudo").requires_confirmation


# ---------- safe baseline ----------


@pytest.mark.parametrize(
    "cmd",
    [
        "ls -la",
        "echo hello",
        "date",
        "git status",
        "cat README.md",
        "python script.py",
        "",
    ],
)
def test_benign_commands_not_caught(cmd: str) -> None:
    v = check_command(cmd)
    assert not v.requires_confirmation
    assert v.reason == ""


# ---------- documented false positives ----------


def test_commented_destructive_command_caught() -> None:
    # We don't parse Bash; comments aren't safe. Acceptable.
    assert check_command("# rm -rf foo").requires_confirmation


def test_string_literal_with_destructive_caught() -> None:
    # echo "rm -rf foo" matches. Acceptable.
    assert check_command('echo "rm -rf foo"').requires_confirmation
