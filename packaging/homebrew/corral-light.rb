# Homebrew formula for Corral Light (macOS).
#
# The source of truth for the tap's copy: a release copies this file into
# the tap with `url` and `sha256` set to that release's tarball. Linux keeps
# install.sh; this is the macOS path.
#
# What it installs: the code into the keg's libexec, the two npm ACP
# adapters (spike/package-lock.json, npm ci) with the repo's own patches, and
# a `corral-light` command bound to Homebrew's Python and Node. What it does
# not: Claude Code, the Grok CLI, Antigravity or AI-OS Seed. Those are each
# yours to install and sign in to; `corral-light doctor` says which are
# missing.
#
# State (pairings, panes, worktrees, logs of turns) lives in
# ~/.local/share/corral-light, outside the Cellar, so it survives upgrades
# and uninstalls.
class CorralLight < Formula
  desc "Run your AI assistants side by side in a browser, on your own machine"
  homepage "https://github.com/cvp1/corral-light"
  url "https://github.com/cvp1/corral-light/archive/refs/tags/v0.1.0.tar.gz"
  sha256 "RELEASE_SHA256"
  license "MIT"
  head "https://github.com/cvp1/corral-light.git", branch: "master"

  depends_on :macos
  depends_on "node"
  depends_on "python@3.14"

  # The tree is used as it ships: brew's cleaner must not prune its empty
  # folders or touch the adapters' files.
  skip_clean "libexec"

  def python3
    Formula["python@3.14"].opt_bin/"python3.14"
  end

  def install
    libexec.install Dir["*"]
    cd libexec/"spike" do
      system "npm", "ci", "--no-audit", "--no-fund"
    end
    system python3, "-I", "-B", libexec/"adapter_patches.py", "apply", libexec/"spike"

    # How `corral-light update` knows to hand over to brew (install_kind.py).
    (libexec/"install-kind.json").write <<~JSON
      {"kind": "homebrew", "formula": "#{full_name}",
       "opt_root": "#{opt_libexec}", "python": "#{python3}",
       "brew": "#{HOMEBREW_PREFIX}/bin/brew"}
    JSON

    (bin/"corral-light").write_env_script libexec/"corral-light",
      CORRAL_LIGHT_PYTHON: python3,
      PATH:                "#{Formula["node"].opt_bin}:$PATH"
  end

  def caveats
    <<~EOS
      Start the hub now and at every login:
        brew services start #{full_name}
      Then see which assistants can run here, and open the wall paired:
        corral-light doctor
        corral-light launch

      Update with `corral-light update`, not `brew upgrade` alone: it upgrades
      only when no pane is mid-turn, then restarts the hub on the new version.

      If you ran `corral-light install-service` from a clone before, unload
      that agent first, or the two will fight over the port:
        launchctl bootout gui/$(id -u)/com.cvp1.corral-light

      Your settings and history live in ~/.local/share/corral-light.
    EOS
  end

  service do
    run [Formula["python@3.14"].opt_bin/"python3.14", opt_libexec/"hub.py"]
    working_dir opt_libexec
    environment_variables CORRAL_LIGHT_BIND:  "127.0.0.1",
                          CORRAL_LIGHT_PORT:  "8098",
                          CORRAL_OLLAMA_URL:  "http://127.0.0.1:11434",
                          PATH:               std_service_path_env
    keep_alive true
    log_path var/"log/corral-light.log"
    error_log_path var/"log/corral-light.log"
  end

  test do
    assert_match "usage: corral-light", shell_output("#{bin}/corral-light 2>&1", 2)
    assert_path_exists libexec/"spike/node_modules/.bin/claude-agent-acp"
    assert_path_exists libexec/"spike/node_modules/.bin/codex-acp"
    system python3, "-I", "-c",
      "import sys; sys.path.insert(0, '#{libexec}'); import install_kind; " \
      "assert install_kind.is_homebrew(), install_kind.detect()"
  end
end
