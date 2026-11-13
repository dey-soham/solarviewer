"""How this copy of SolarViewer was installed, what is newer, and how it is
updated. Nothing here needs Qt.

A copy is one of four kinds, and each is updated its own way:

- a **release** from PyPI, installed with pip, uv or conda: the newest release
  on PyPI, installed with the same tool;
- a **git URL** (``pip install git+https://github.com/...``): the newest commit
  of the branch it was installed from, installed from the same URL;
- a **git checkout** (a clone): the commits its branch is behind its remote,
  taken with ``git pull --ff-only``. An editable install then runs the new
  files; one built from the clone (``pip install .``) is built from it again;
- a **folder** of source that is not a git checkout: it cannot update itself.

conda has no SolarViewer package: in a conda environment it was installed
with pip, and pip there updates it.

``look`` says what is installed and what is newer, ``Install.commands`` what
would run, and ``outcome`` whether it did anything: an installer that ends
well without changing what is installed has not updated it. Looking only
reads: a checkout is compared with its remote over HTTPS (GitHub's public
API), or with ``git ls-remote``, never fetched.
"""

import importlib.util
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import sysconfig
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse
from urllib.request import url2pathname

import requests
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

PACKAGE = "solarviewer"
REPOSITORY = "dey-soham/solarviewer"  # where the release notes are read from
CHECK_AT_STARTUP = "updates/check_at_startup"  # the settings: whether to look when SolarViewer starts,
SKIPPED = "updates/skipped"  # and the version the user asked not to be told of again
NOTES_FILE = "RELEASE_NOTES.md"
PYPI_JSON = "https://pypi.org/pypi/{package}/json"
RAW_FILE = "https://raw.githubusercontent.com/{repository}/{ref}/{file}"
COMMITS_API = "https://api.github.com/repos/{repository}/commits"
TIMEOUT = 8  # seconds for one request
GIT_TIMEOUT = 20  # seconds for one git command that asks a remote
MOST_COMMITS = 30  # commits asked of a branch: more than these are "30 or more"

RELEASE, GIT_URL, CHECKOUT, FOLDER = "release", "git url", "checkout", "folder"

# What reads the installed version (or the commit a git URL install is at) in
# a Python of its own: this one keeps the modules it started with
_READ_VERSION = "from importlib.metadata import version; print(version({package!r}))"
_READ_COMMIT = (
    "import json; from importlib.metadata import distribution; "
    "print(json.loads(distribution({package!r}).read_text('direct_url.json') or '{{}}')"
    ".get('vcs_info', {{}}).get('commit_id', ''))"
)
# The list of installed files with their checksums: it changes when, and only
# when, what is installed does
_READ_FILES = (
    "import hashlib; from importlib.metadata import distribution; "
    "print(hashlib.sha256((distribution({package!r}).read_text('RECORD') or '').encode()).hexdigest())"
)


def _short(path) -> str:
    """``path`` with the home folder as ``~``."""
    text = str(path)
    home = os.path.expanduser("~")
    return "~" + text[len(home):] if home and text.startswith(home + os.sep) else text


def github_repository(url: str) -> str:
    """``owner/name`` of a GitHub URL (https, ssh or scp-like), else ""."""
    found = re.search(r"github\.com[:/]+([^/\s]+)/([^/\s]+?)(?:\.git)?/?$", (url or "").strip())
    return f"{found.group(1)}/{found.group(2)}" if found else ""


def _git(folder, *args, timeout=GIT_TIMEOUT) -> Tuple[int, str]:
    """``git`` in ``folder``: its exit code and what it printed. It never
    stops to ask for a password or a passphrase; -1 when git is missing or
    takes too long."""
    env = dict(os.environ, GIT_TERMINAL_PROMPT="0", LC_ALL="C")
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    try:
        done = subprocess.run(
            ["git", "-C", str(folder), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError) as e:
        return -1, str(e)
    return done.returncode, (done.stdout or done.stderr).strip()


# ---------------------------------------------------------------- what is installed


@dataclass
class Install:
    """How this copy was installed, and so how it is updated."""

    kind: str = RELEASE
    version: str = ""
    manager: str = ""  # pip, uv, "uv tool" or conda; "" when none was found
    program: str = ""  # the uv or conda executable
    python: str = field(default_factory=lambda: sys.executable)
    place: str = ""  # the source's folder (a checkout, a folder), else the environment
    environment: str = ""  # the Python environment it is installed in
    conda: bool = False  # which is a conda environment
    copied: bool = False  # a checkout: installed as a copy built from it, not editable
    branch: str = ""  # a checkout's branch
    remote: str = ""  # the remote its branch follows ("origin")
    remote_branch: str = ""  # and the branch there
    url: str = ""  # the git URL: a checkout's remote, or the one installed from
    revision: str = ""  # the branch or tag a git URL install asked for
    commit: str = ""  # the commit checked out, or installed from a git URL
    blocked: str = ""  # why this copy cannot update itself
    by_hand: str = ""  # what to do instead

    def describe(self) -> str:
        """One line on what this copy is: ``PyPI release, pip in ~/.sv``."""
        where = "the conda environment " if self.conda else ""
        where = f"{self.manager or 'no installer'} in {where}{_short(self.environment or self.place)}"
        if self.kind == CHECKOUT:
            checkout = f"git checkout of {self.branch or 'no branch'} in {_short(self.place)}"
            return f"copy of the {checkout}, {where}" if self.copied else f"{checkout} (editable)"
        if self.kind == FOLDER:
            return f"source folder {_short(self.place)} (not a git checkout)"
        if self.kind == GIT_URL:
            return f"{self.spec()}, {where}"
        return f"PyPI release, {where}"

    def spec(self) -> str:
        """What the installer is asked for: the package, its git URL, or the
        folder it is built from."""
        if self.kind == GIT_URL:
            return f"git+{self.url}" + (f"@{self.revision}" if self.revision else "")
        return self.place if self.kind == CHECKOUT else PACKAGE

    def commands(self) -> List[List[str]]:
        """What updating runs, in order; empty when this copy cannot."""
        if self.blocked or self.kind == FOLDER:
            return []
        if self.kind == CHECKOUT:
            pull = ["git", "-C", self.place, "pull", "--ff-only"]
            return [pull] + self._installer() if self.copied else [pull]
        return self._installer()

    def _installer(self) -> List[List[str]]:
        """The installer's part. (Each form was tried against pip 24 and uv 0.13.)"""
        spec, python = self.spec(), self.python
        if self.manager == "pip":
            pip = [python, "-m", "pip", "install"]
            if self.kind == GIT_URL:
                # pip leaves a git URL alone while its version number is the
                # same, whatever the commit: the package is put in again by
                # itself, then anything new that it needs
                return [pip + ["--upgrade", "--force-reinstall", "--no-deps", spec], pip + [spec]]
            if self.kind == CHECKOUT:
                return [pip + [spec]]  # a folder is always built again
            return [pip + ["--upgrade", spec]]
        if self.manager == "uv":
            uv = [self.program, "pip", "install", "--python", python]
            if self.kind == RELEASE:
                return [uv + ["--upgrade", spec]]
            return [uv + ["--reinstall-package", PACKAGE, spec]]
        if self.manager == "uv tool":
            # (it goes by what the tool was installed from: a release, a git URL or a folder)
            return [[self.program, "tool", "upgrade", PACKAGE]]
        if self.manager == "conda":
            return [[self.program, "update", "--yes", "--prefix", self.environment, PACKAGE]]
        return []

    def command_text(self) -> str:
        """The commands as they would be typed, one to a line."""
        return "\n".join(shlex.join(command) for command in self.commands())


def _package_folder() -> Path:
    return Path(__file__).resolve().parents[1]


def _distribution():
    try:
        return metadata.distribution(PACKAGE)
    except metadata.PackageNotFoundError:
        return None


def _direct_url(dist) -> dict:
    """PEP 610's record of where an install came from, {} for a release."""
    try:
        return json.loads(dist.read_text("direct_url.json") or "{}") if dist is not None else {}
    except (ValueError, OSError):
        return {}


def _find_program(*names) -> str:
    """The first of ``names`` on the PATH, beside this Python, or where its
    installer puts it. (uv names itself in ``UV`` for what it starts.)"""
    places = [Path(sys.executable).parent, Path.home() / ".local" / "bin", Path.home() / ".cargo" / "bin"]
    named = os.environ.get("UV", "")
    if "uv" in names and named and os.access(named, os.X_OK):
        return named
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for place in places:
            if (place / name).is_file() and os.access(place / name, os.X_OK):
                return str(place / name)
    return ""


def _container() -> str:
    """The container this runs in ("an Apptainer image", "a Docker container"), else ""."""
    for key in ("APPTAINER_CONTAINER", "SINGULARITY_CONTAINER"):
        if os.environ.get(key):
            return f"an Apptainer image, {os.environ[key]}"
    if os.path.exists("/.dockerenv"):
        return "a Docker container"
    if os.path.exists("/run/.containerenv"):
        return "a Podman container"
    return ""


def _externally_managed() -> bool:
    """Whether this is a system Python that tells pip to keep out (PEP 668)."""
    if sys.prefix != sys.base_prefix:
        return False  # an environment of its own
    return (Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists()


def _set_checkout(install: Install, top: str):
    """A checkout's branch, the remote branch it follows, and its commit."""
    install.kind, install.place = CHECKOUT, top
    install.commit = _git(top, "rev-parse", "HEAD")[1]
    code, branch = _git(top, "symbolic-ref", "--short", "-q", "HEAD")
    if code != 0 or not branch:
        install.blocked = "This checkout is not on a branch"
        install.by_hand = f"Check out a branch in {_short(top)}, then update with git pull."
        return
    install.branch = branch
    code, remote = _git(top, "config", f"branch.{branch}.remote")
    merge = _git(top, "config", f"branch.{branch}.merge")[1]
    if code != 0 or not remote or not merge.startswith("refs/heads/"):
        install.blocked = f"Its branch {branch} does not follow a branch on a remote"
        install.by_hand = f"Set one with git branch --set-upstream-to in {_short(top)}."
        return
    install.remote, install.remote_branch = remote, merge[len("refs/heads/"):]
    install.url = _git(top, "remote", "get-url", remote)[1]
    if not os.access(top, os.W_OK):
        install.blocked = f"You cannot write to {_short(top)}"
        install.by_hand = "Ask whoever owns the checkout to pull."


def _set_manager(install: Install, dist):
    """The tool a release or a git URL was installed with, and what stands in its way."""
    installer = ""
    try:
        installer = (dist.read_text("INSTALLER") or "").strip().lower() if dist is not None else ""
    except OSError:
        pass
    install.environment = sys.prefix
    install.place = install.place or sys.prefix
    install.conda = (Path(sys.prefix) / "conda-meta").is_dir()
    if installer == "conda":
        install.manager = "conda"
        install.program = os.environ.get("CONDA_EXE") or _find_program("conda", "mamba", "micromamba")
    elif (Path(sys.prefix) / "uv-receipt.toml").exists():
        install.manager, install.program = "uv tool", _find_program("uv")
    elif importlib.util.find_spec("pip") is not None:
        install.manager = "pip"
    else:
        install.manager, install.program = "uv", _find_program("uv")
    if install.blocked:
        return  # (a checkout that cannot be pulled: that comes first)
    container = _container()
    packages = Path(str(dist.locate_file(""))) if dist is not None else _package_folder().parent
    if container:
        install.blocked = f"This copy runs in {container}: it is updated by getting a newer image, not from inside"
        install.by_hand = "Pull or build the newer image, and start SolarViewer from it."
    elif not os.access(packages, os.W_OK):
        install.blocked = f"SolarViewer is installed where you cannot write ({_short(packages)})"
        install.by_hand = "Ask whoever installed it to update it, or install a copy of your own (see INSTALLATION.md)."
    elif install.manager != "pip" and not install.program:
        tool = "conda" if install.manager == "conda" else "uv"
        install.blocked = f"This environment has no pip, and {tool} was not found"
        wanted = Install(**{**vars(install), "program": tool, "blocked": "", "by_hand": ""})
        install.by_hand = "Run this where " + tool + " is installed:\n" + wanted.command_text()
    elif install.manager == "pip" and _externally_managed():
        install.blocked = "This is the system's Python, which pip may not change"
        install.by_hand = "Install SolarViewer in an environment of its own (see INSTALLATION.md)."


def installed() -> Install:
    """How the SolarViewer that is running was installed."""
    from ..version import __version__

    install = Install(version=__version__)
    dist = _distribution()
    direct = _direct_url(dist)
    folder = _package_folder()
    in_packages = any(part in ("site-packages", "dist-packages") for part in folder.parts)
    editable = bool(direct.get("dir_info", {}).get("editable")) or not in_packages
    if editable:
        code, top = _git(folder, "rev-parse", "--show-toplevel", timeout=5)
        if code == 0 and top:
            _set_checkout(install, top)
        else:
            install.kind, install.place = FOLDER, str(folder.parent)
            install.blocked = "This copy runs from a folder of source that is not a git checkout"
            install.by_hand = "Replace the folder with a newer copy, or install a release (see INSTALLATION.md)."
        return install
    vcs = direct.get("vcs_info") or {}
    if vcs.get("vcs") == "git" and direct.get("url"):
        install.kind, install.url = GIT_URL, direct["url"]
        install.revision, install.commit = vcs.get("requested_revision") or "", vcs.get("commit_id") or ""
    elif "dir_info" in direct and str(direct.get("url", "")).startswith("file://"):
        # Built from a folder (``pip install .``): if that is a clone and still
        # there, it is updated by pulling and building again. Otherwise it is
        # held against the releases, which is what its version number says
        source = url2pathname(urlparse(direct["url"]).path)
        code, top = _git(source, "rev-parse", "--show-toplevel", timeout=5) if os.path.isdir(source) else (1, "")
        if code == 0 and top:
            _set_checkout(install, top)
            install.copied = True
    _set_manager(install, dist)
    return install


# ---------------------------------------------------------------- what is newer


@dataclass
class Update:
    """What a look found."""

    available: bool = False
    latest: str = ""  # what is there: "1.3.1", or "2 new commits on v1.3 (5957f0e)"
    target: str = ""  # the version or the commit an update should reach
    message: str = ""  # the line that says it
    notes: str = ""  # what is new, as Markdown
    notes_url: str = ""  # where to read more
    blocked: str = ""  # why it cannot be installed here (this Python is too old)
    error: str = ""  # why nothing is known


def _get(url, **params):
    response = requests.get(url, params=params or None, timeout=TIMEOUT, headers={"Accept": "application/vnd.github+json"} if "api.github.com" in url else None)
    if response.status_code in (403, 429) and "api.github.com" in url:
        raise requests.HTTPError("GitHub has had too many unsigned requests from this address for now; try again in an hour")
    response.raise_for_status()
    return response


def notes_between(text: str, current: str, latest: str) -> str:
    """The sections of the release notes for the versions after ``current``
    up to ``latest``, newest first ("## Version 1.3.1" starts one), or ""."""
    try:
        low, high = Version(current), Version(latest)
    except InvalidVersion:
        return ""
    kept, take = [], False
    for line in text.splitlines():
        heading = re.match(r"^##\s+(?:Version\s+)?v?(\d[^\s]*)\s*$", line, re.IGNORECASE)
        if heading:
            try:
                take = low < Version(heading.group(1)) <= high
            except InvalidVersion:
                take = False
        elif re.match(r"^#\s", line):
            take = False
        if take:
            kept.append(line)
    return "\n".join(kept).strip()


def release_notes(current: str, latest: str, repository=REPOSITORY) -> Tuple[str, str]:
    """What is new from ``current`` to ``latest`` (Markdown, "" when the
    notes have no entry or cannot be read), and the page to read them on."""
    page = f"https://github.com/{repository}/blob/HEAD/{NOTES_FILE}"
    for ref in (f"v{latest}", "HEAD"):
        try:
            text = _get(RAW_FILE.format(repository=repository, ref=ref, file=NOTES_FILE)).text
        except requests.RequestException as e:
            logger.info("No release notes at %s: %s", ref, e)
            continue
        notes = notes_between(text, current, latest)
        if notes:
            return notes, f"https://github.com/{repository}/blob/{ref}/{NOTES_FILE}"
    return "", page


def _look_release(install: Install, notes: bool) -> Update:
    data = _get(PYPI_JSON.format(package=PACKAGE)).json()
    latest = data["info"]["version"]
    update = Update(latest=latest, target=latest)
    current, newest = Version(install.version), Version(latest)
    if newest < current:
        update.message = f"This build ({install.version}) is newer than the latest release ({latest})."
        return update
    if newest == current:
        update.message = "You are using the latest version."
        return update
    update.available = True
    update.message = "A new version of SolarViewer is available."
    wants = data["info"].get("requires_python") or ""
    running = ".".join(str(part) for part in sys.version_info[:3])
    try:
        if wants and not SpecifierSet(wants).contains(running, prereleases=True):
            update.blocked = f"Version {latest} needs Python {wants}; this environment has Python {running}"
    except InvalidSpecifier:
        pass
    if notes:
        update.notes, update.notes_url = release_notes(install.version, latest)
    return update


def _remote_commits(install: Install) -> Tuple[List[Tuple[str, str]], str]:
    """The newest commits ``(sha, first line)`` of the branch this copy
    follows, newest first, and the page that lists them. From GitHub's API;
    another host is only asked for the branch's head."""
    branch = install.remote_branch if install.kind == CHECKOUT else install.revision
    repository = github_repository(install.url)
    page = ""
    if repository:
        page = f"https://github.com/{repository}/commits" + (f"/{branch}" if branch else "")
        params = {"per_page": MOST_COMMITS}
        if branch:
            params["sha"] = branch
        try:
            rows = _get(COMMITS_API.format(repository=repository), **params).json()
        except requests.HTTPError as e:
            # GitHub answered, and with a no: a private repository, or too
            # many requests from this address. git may still be let in
            logger.info("GitHub's API did not list the commits (%s): asking with git", e)
        else:
            return [(row["sha"], (row.get("commit", {}).get("message") or "").split("\n")[0]) for row in rows], page
    return [(_remote_head(install, branch), "")], page


def _remote_head(install: Install, branch: str) -> str:
    """The commit ``branch`` is at on the remote, asked with git itself: it
    signs in as git does (a key, a stored password), and never stops to ask."""
    folder = install.place if install.kind == CHECKOUT else os.path.expanduser("~")
    ref = f"refs/heads/{branch}" if install.kind == CHECKOUT else (branch or "HEAD")
    code, out = _git(folder, "ls-remote", install.url, ref)
    if code != 0 or not out:
        raise requests.RequestException(f"git could not ask {install.url}: {out or 'it has no ' + ref}")
    return out.split()[0]


def _look_git(install: Install) -> Update:
    commits, page = _remote_commits(install)
    if not commits:
        raise requests.RequestException("The branch has no commits")
    if install.kind == CHECKOUT:
        # New are the commits this clone does not have, down to the first it has
        def known(sha):
            return _git(install.place, "cat-file", "-e", sha + "^{commit}", timeout=5)[0] == 0
    else:
        def known(sha):
            return sha == install.commit
    new = []
    for sha, subject in commits:
        if known(sha):
            break
        new.append((sha, subject))
    branch = (install.remote_branch if install.kind == CHECKOUT else install.revision) or "the default branch"
    head = commits[0][0]
    update = Update(target=head, notes_url=page)
    if not new:
        update.latest = f"{head[:7]} on {branch}"
        update.message = f"You have the latest commit of {branch}."
        return update
    update.available = True
    update.message = f"{branch} has new commits."
    if not any(subject for _sha, subject in commits):
        # Only the branch's head is known (asked with git): how many, and what, is not
        update.latest = f"new commits on {branch} ({head[:7]})"
        return update
    more = len(new) == len(commits) and len(commits) >= MOST_COMMITS
    count = f"{len(new)} or more" if more else str(len(new))
    update.latest = f"{count} new commit{'' if len(new) == 1 and not more else 's'} on {branch} ({head[:7]})"
    lines = [f"- {subject or 'A new commit'} (`{sha[:7]}`)" for sha, subject in new]
    if more:
        lines.append("- … and earlier ones")
    update.notes = "\n".join(lines)
    return update


def look(notes=True) -> Tuple[Install, Update]:
    """What is installed and what is newer. With ``notes``, what is new in
    it too (a second request for a release)."""
    install = installed()
    try:
        if install.kind == RELEASE:
            update = _look_release(install, notes)
        elif install.kind == FOLDER or (install.kind == CHECKOUT and not install.remote):
            update = Update(message=install.blocked + ".")
        else:
            update = _look_git(install)
    except requests.RequestException as e:
        update = Update(error=f"Network error: {e}")
    except Exception as e:  # an answer that does not read as one
        logger.warning("Could not check for updates: %s", e, exc_info=True)
        update = Update(error=f"Error checking for updates: {e}")
    return install, update


def check_for_updates(current_version_str=None):
    """Whether something newer is there, for the note at startup:
    ``(is_update_available, what is there, error message or None)``."""
    _install, update = look(notes=False)
    return update.available, update.latest or None, update.error or None


# ---------------------------------------------------------------- whether it worked


def state(install: Install) -> str:
    """What is installed now, read afresh: the version of a release, the
    commit of a checkout or of a git URL install, the checksum of the files
    built from a checkout. "" if it cannot be read."""
    if install.kind == CHECKOUT and not install.copied:
        code, out = _git(install.place, "rev-parse", "HEAD", timeout=5)
        return out if code == 0 else ""
    code = {GIT_URL: _READ_COMMIT, CHECKOUT: _READ_FILES}.get(install.kind, _READ_VERSION).format(package=PACKAGE)
    try:
        done = subprocess.run([install.python, "-c", code], capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def _name(install: Install, value: str) -> str:
    """What a state is called: the version, or the commit in short."""
    if install.kind == RELEASE:
        return value
    if install.copied:  # (its state is a checksum: it is named by the commit it was built from)
        return _git(install.place, "rev-parse", "--short=7", "HEAD", timeout=5)[1] or "the commit it was"
    return value[:7]


def outcome(install: Install, update: Update, before: str) -> Tuple[bool, str]:
    """Whether the installer changed what is installed, and the line that
    says so. Ending without an error is not enough: it must be newer."""
    after = state(install)
    if not after:
        return False, "The installer finished, but what is installed now could not be read."
    if after == before:
        return False, f"Nothing was installed: SolarViewer is still {_name(install, before)}. The installer finished without changing it."
    if install.kind == RELEASE:
        try:
            if Version(after) < Version(update.target):
                return True, f"Updated to {after}, not to {update.target}: the installer chose the older one for this environment."
        except InvalidVersion:
            pass
    return True, f"Updated to {_name(install, after)}."


def needs_reinstall(install: Install, before: str, after: str) -> bool:
    """Whether a pull changed what the checkout needs installed (its
    pyproject.toml): the files are new, the environment is not."""
    if install.kind != CHECKOUT or install.copied or not before or not after:
        return False
    code, out = _git(install.place, "diff", "--name-only", before, after, "--", "pyproject.toml", timeout=10)
    return code == 0 and bool(out)


def failure_reason(output: str, exit_code: Optional[int] = None) -> str:
    """Why an installer failed, from what it printed, in one line."""
    low = output.lower()
    known = (
        ("externally-managed-environment", "This Python is managed by the system and may not be changed by pip. Install SolarViewer in an environment of its own."),
        ("no module named pip", "This environment has no pip."),
        ("permission denied", "The files could not be written: permission denied."),
        ("read-only file system", "The files could not be written: the folder is read-only."),
        ("no matching distribution", "No release fits this Python and system."),
        ("could not resolve host", "The network could not be reached."),
        ("temporary failure in name resolution", "The network could not be reached."),
        ("connection timed out", "The network could not be reached."),
        ("not possible to fast-forward", "The checkout has commits of its own that the remote does not: merge or rebase with git."),
        ("would be overwritten by merge", "The checkout has changes that the new commits would overwrite: commit or stash them, then pull."),
        ("permission denied (publickey)", "git could not sign in to the remote without asking: pull in a terminal."),
        ("could not read from remote repository", "git could not read the remote without asking for a password: pull in a terminal."),
    )
    for text, reason in reversed(known):
        if text in low:
            return reason
    last = [line.strip() for line in output.splitlines() if line.strip()]
    code = f"exit code {exit_code}" if exit_code is not None else "an error"
    return f"The installer stopped with {code}" + (f": {last[-1]}" if last else ".")
