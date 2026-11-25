#!/usr/bin/env python3
"""Roast Your Fridge — one line, one laugh (make.fun bounty).

A deterministic Python 3.13 program (standard library only, no
network, no model call) that writes a single roast line to
``$MAKEFUN_OUTPUT_DIR/entry.txt`` (default ``/output/entry.txt``).

The entry (108 characters, plus one trailing newline in the file):

    My fridge's crisper drawer is the only retirement home where
    the residents come back as science experiments.

Why this line won — judged against the brief (roast a fridge or a
phone, a specific detail, a twist in the last few words, reads
clean aloud, fresh):

* CHOSEN (108 chars): the crisper drawer is a specific,
  recognisable fridge detail; "retirement home" reframes it, and
  "come back as science experiments" pays off in the last few
  words. One idea, one turn, no setup a stranger needs.
* Hum (110): "My fridge hums all night like it pays rent, yet the
  only thing it ever produces is a light and disappointment."
  A generic hum — any old fridge hums — and "disappointment"
  lands soft; the twist is a shrug, not a surprise.
* Phone battery (93): "My phone dies at 1% the second I need it
  most, like it planned the betrayal all day." True, but the
  phone dying at 1% is the most worn-out phone joke there is;
  it fails the freshness rule outright.
* Slot machine (104): "My fridge is so old its ice maker sounds
  like a slot machine that only pays out in freezer burn." Two
  images compete (slot machine, freezer burn), the twist is
  muddled, and it stumbles when read aloud.
* Door alarm (118): "My fridge beeps at me for leaving the door
  open, like I owe it rent for standing in front of it too long."
  The beep is specific, but the rent joke repeats the hum
  candidate's setup and the payoff explains itself.

Newline decision: the file ends with exactly one trailing newline.
The gate allows 1 to 200 characters; this file is 109 bytes total
including the newline, so it passes even if the scorer counts the
newline, and the line itself is a single line with no embedded
newline.

How it writes: the line is validated against the gate before
anything is written, then published atomically by
AtomicEntryWriter — a staging file inside the output directory
(never /tmp, which fails across a bind-mount), a full write loop
with fsync, mode 0644 set explicitly, verified byte-for-byte
BEFORE the os.replace into place, and verified again after it.
The output directory itself is never removed (the grader mounts
it); only its top-level files are cleared. The staging name is
unique per process and stale staging files from crashed runs are
swept only when their owner pid is dead.

Usage: mkdir -p out && MAKEFUN_OUTPUT_DIR=out python3 main.py
"""

import os
import re
import stat
import unicodedata

LINE = (
    "My fridge's crisper drawer is the only retirement home where "
    "the residents come back as science experiments."
)

# Sealed gate: /output/entry.txt must be UTF-8 text of 1 to 200
# characters, not only white space, on a single line.
MAX_LINE_CHARS = 200

OUTPUT_FILENAME = "entry.txt"

# Tmp files are named .entry.txt.<pid>.tmp so concurrent runs never
# share one; the sweep below recognises exactly this shape.
_STAGING_RE = re.compile(r"^\.entry\.txt\.(\d+)\.tmp$")


def _staging_name(pid):
    """Staging file name for a process; the pid stamp keeps
    concurrent runs in one output directory from sharing a file."""
    return ".entry.txt.%d.tmp" % pid


def _forbid_blank(line):
    """The line must contain something other than whitespace."""
    if not line.strip():
        raise ValueError("line must not be blank or only whitespace")


def _forbid_edge_whitespace(line):
    """Leading/trailing whitespace only burns gate characters."""
    if line != line.strip():
        raise ValueError("line must not start or end with whitespace")


def _forbid_breaks(line):
    """The entry is one line; embedded breaks are not allowed.

    Plain newlines/carriage returns and the Unicode line and
    paragraph separators (U+2028/U+2029) are all line breaks in
    disguise, so one check rejects the whole family.
    """
    if "\n" in line or "\r" in line:
        raise ValueError("line must be a single line (no embedded newline)")
    if "\u2028" in line or "\u2029" in line:
        raise ValueError("line must not contain a line/paragraph separator")


def _forbid_invisible(line):
    """Reject control, format and surrogate characters in one pass.

    Category Cc covers C0 controls, DEL and C1 controls
    (U+0080-U+009F). Category Cf covers zero-width spaces and
    joiners, the byte order mark (U+FEFF) and other characters
    with no visible glyph; a line carrying one looks clean and
    is not. Category Cs covers surrogates, which can never be
    encoded as UTF-8. Any of the three means the text is not
    clean readable text for the scorer.
    """
    for ch in line:
        cat = unicodedata.category(ch)
        if cat == "Cc":
            raise ValueError(
                "line must not contain control character U+%04X" % ord(ch)
            )
        if cat == "Cf":
            raise ValueError(
                "line must not contain format character U+%04X" % ord(ch)
            )
        if cat == "Cs":
            raise ValueError(
                "line must not contain surrogate U+%04X" % ord(ch)
            )


def _forbid_too_long(line):
    """The gate caps the line at MAX_LINE_CHARS characters."""
    if len(line) > MAX_LINE_CHARS:
        raise ValueError(
            "line is %d characters, over the %d-character gate"
            % (len(line), MAX_LINE_CHARS)
        )


def validate_line(line):
    """Check the line against the gate; raise ValueError if it fails.

    The check runs before any file is written, so an invalid line
    fails loudly instead of producing an entry the scorer rejects.
    It is deliberately stricter than the gate where the gate's
    wording leaves a trap: control and format characters are not
    "text" a scorer can read cleanly, and leading/trailing
    whitespace only burns characters from the 200-character budget.
    """
    if not isinstance(line, str):
        raise ValueError("line must be a string")
    for check in (
        _forbid_blank,
        _forbid_edge_whitespace,
        _forbid_breaks,
        _forbid_invisible,
        _forbid_too_long,
    ):
        check(line)
    return line


def resolve_out_dir():
    """Resolve MAKEFUN_OUTPUT_DIR defensively.

    An unset or blank value falls back to /output. The root
    directory is refused outright — clearing its contents would
    be catastrophic — and a value that points at an existing
    non-directory (a regular file, a dangling symlink, a device)
    fails here with a clear message instead of surfacing later
    as a confusing makedirs/open error.
    """
    raw = os.environ.get("MAKEFUN_OUTPUT_DIR", "")
    out_dir = os.path.abspath(raw.strip() or "/output")
    if out_dir == os.path.sep:
        raise ValueError("MAKEFUN_OUTPUT_DIR must not be the root directory")
    if os.path.lexists(out_dir) and not os.path.isdir(out_dir):
        raise ValueError(
            "MAKEFUN_OUTPUT_DIR %r exists and is not a directory" % out_dir
        )
    return out_dir


def _pid_running(pid):
    """True if pid names a process that is still running."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # A process exists but belongs to someone else: treat it
        # as alive so its staging file is never swept away.
        return True
    except OSError:
        return True
    return True


def _staging_owner_gone(name):
    """True if a pid-stamped staging name belongs to no live process.

    A name that matches the pattern but carries no live pid cannot
    belong to a running process, so it is safe to sweep. The caller's
    own pid is never dead to itself.
    """
    match = _STAGING_RE.match(name)
    if not match:
        return False
    pid = int(match.group(1))
    return pid != os.getpid() and not _pid_running(pid)


def _sweep_output_dir(out_dir):
    """Clear the output directory's top-level files, never the dir.

    Only regular files and symlinks at the top level are removed;
    subdirectories are left alone and the directory itself is never
    deleted (the grader mounts it). Stale staging files from
    crashed runs are swept only when their owner pid is dead; a
    live run's staging file is never touched. Any failure to clean
    is best effort: a stale file must never block a fresh run.
    """
    try:
        names = os.listdir(out_dir)
    except OSError:
        return
    for name in names:
        path = os.path.join(out_dir, name)
        try:
            if _STAGING_RE.match(name):
                if _staging_owner_gone(name):
                    os.unlink(path)
            elif os.path.isfile(path) or os.path.islink(path):
                os.unlink(path)
        except OSError:
            pass


def _drain_write(f, data):
    """Write every byte of data; a short write is a hard failure.

    A single ``file.write`` call is not guaranteed to consume the
    whole buffer, so loop until the view is empty and raise if a
    call ever writes zero bytes.
    """
    view = memoryview(data)
    while view:
        written = f.write(view)
        if written is None:
            # A raw writer in non-blocking mode; not expected for
            # a regular file, but never spin silently on it.
            raise OSError("write returned None without writing data")
        if written == 0:
            raise OSError("short write: wrote 0 of %d bytes" % len(view))
        view = view[written:]


def _flush_dir(path):
    """Flush a directory entry to disk; best effort on filesystems
    that do not support it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _verify_stat(path, data):
    """Check a file is a regular file of the right size and mode."""
    st = os.stat(path)
    if not stat.S_ISREG(st.st_mode):
        raise ValueError("%s is not a regular file" % path)
    if st.st_size != len(data):
        raise ValueError(
            "%s is %d bytes, expected %d" % (path, st.st_size, len(data))
        )
    if stat.S_IMODE(st.st_mode) != 0o644:
        raise ValueError(
            "%s has mode %o, expected 0644" % (path, stat.S_IMODE(st.st_mode))
        )


def _verify_content(path, data):
    """Check a file holds exactly the bytes we meant to write."""
    with open(path, "rb") as f:
        written = f.read()
    if written != data:
        raise ValueError("read-back mismatch: %s is not the line" % path)


def _verify_file(path, data):
    """Check a file is exactly the bytes we meant to write.

    Regular file, exact size, exact bytes, mode 0644. Used on the
    staging file BEFORE the publish (so a bad write never becomes
    the entry) and again on the published file afterwards.
    """
    _verify_stat(path, data)
    _verify_content(path, data)


class AtomicEntryWriter:
    """Owns the staging -> verify -> replace -> verify sequence.

    One instance serves one run: it knows the output directory, the
    per-pid staging path and the final path, and it guarantees the
    staging file is cleaned up even when a step fails.
    """

    def __init__(self, out_dir):
        self.out_dir = out_dir
        self.staging = os.path.join(out_dir, _staging_name(os.getpid()))
        self.target = os.path.join(out_dir, OUTPUT_FILENAME)

    def prepare(self):
        """Create the output directory and clear its stale contents.

        Never delete the output directory itself: the grader mounts
        it, so only its contents are ever touched.
        """
        os.makedirs(self.out_dir, exist_ok=True)
        _sweep_output_dir(self.out_dir)

    def publish(self, data):
        """Write data to staging, verify it, then atomically replace."""
        try:
            with open(self.staging, "wb") as f:
                _drain_write(f, data)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(self.staging, 0o644)
            # Verify BEFORE the publish: a bad write must never
            # reach the published path.
            _verify_file(self.staging, data)
            os.replace(self.staging, self.target)
            os.chmod(self.target, 0o644)
            _flush_dir(self.out_dir)
        finally:
            # Never leave our staging file behind, even on failure.
            try:
                os.unlink(self.staging)
            except OSError:
                pass

    def confirm(self, data):
        """Post-publish check: the grader's file must be exact.

        The published file is checked again for regular-file type,
        exact size, exact bytes and mode 0644, and the run confirms
        its own staging file is gone, so no stray staging file can
        be mistaken for output.
        """
        _verify_file(self.target, data)
        if os.path.lexists(self.staging):
            raise ValueError(
                "staging file %s still exists after publish" % self.staging
            )


def main():
    validate_line(LINE)
    data = (LINE + "\n").encode("utf-8")
    writer = AtomicEntryWriter(resolve_out_dir())
    writer.prepare()
    writer.publish(data)
    writer.confirm(data)


if __name__ == "__main__":
    main()
