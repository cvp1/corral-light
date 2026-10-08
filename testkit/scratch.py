"""Temp dirs the test suites remove when they are done with them.

A bare `tempfile.mkdtemp()` in a test leaks: one run of every suite used to
leave ~900 dirs behind, and on a tmpfs /tmp that ran the box out of inodes.
Every temp dir a test makes should come from here instead.

    tmpdir(self, "x-")      removed after this test (addCleanup)
    tmpdir(cls, "x-")       removed after this class (addClassCleanup)
    process_tmpdir("x-")    removed when the process exits (atexit)
    default_state("x-")     CORRAL_LIGHT_STATE, only if the caller set none

Cleanups registered here run last-in-first-out with the test's others, so
call tmpdir() before registering anything that still writes into the dir
(closing panes, stopping a hub): those then run first.
"""
import atexit
import os
import shutil
import tempfile


def tmpdir(owner, prefix="corral-test-"):
    """A fresh temp dir, removed when `owner` (a TestCase or its class) is done."""
    d = tempfile.mkdtemp(prefix=prefix)
    add = owner.addClassCleanup if isinstance(owner, type) else owner.addCleanup
    add(shutil.rmtree, d, True)
    # Second sweep: a background lane probe can still write into a test's
    # state dir after the test removed it, recreating the dir.
    atexit.register(shutil.rmtree, d, True)
    return d


def process_tmpdir(prefix="corral-test-"):
    """A fresh temp dir, removed when this process exits."""
    d = tempfile.mkdtemp(prefix=prefix)
    atexit.register(shutil.rmtree, d, True)
    return d


def default_state(prefix="corral-light-test-"):
    """Point CORRAL_LIGHT_STATE at a process-scoped temp dir unless one is set,
    and CORRAL_LIGHT_CONFIG_DIR likewise.

    Replaces `os.environ.setdefault(..., tempfile.mkdtemp())`, which made
    (and leaked) a dir even when the variable was already set.

    The config dir holds the module pins. A hub started with a scratch state
    dir but the live config dir found the operator's installed modules
    missing from the scratch state and disabled them in the live pins.
    """
    if "CORRAL_LIGHT_STATE" not in os.environ:
        os.environ["CORRAL_LIGHT_STATE"] = process_tmpdir(prefix)
    if "CORRAL_LIGHT_CONFIG_DIR" not in os.environ:
        os.environ["CORRAL_LIGHT_CONFIG_DIR"] = process_tmpdir(prefix + "config-")
    return os.environ["CORRAL_LIGHT_STATE"]
