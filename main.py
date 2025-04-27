#!/usr/bin/env python3
import fcntl
import json
import os
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict

import tomllib


@dataclass
class Config:
    """Configuration for nb tool."""

    notebooks_paths: str
    jupyter_path: str
    ipython_path: str

    @property
    def expanded_notebooks_path(self) -> str:
        """Expand the notebooks path to absolute path."""
        return os.path.expanduser(self.notebooks_paths)

    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "Config":
        """Create Config from dictionary."""
        return cls(
            notebooks_paths=config_dict.get("notebooks_paths", "~/Documents/Notebooks"),
            jupyter_path=config_dict.get(
                "jupyter_path", "/opt/homebrew/Caskroom/miniforge/base/bin/jupyter"
            ),
            ipython_path=config_dict.get(
                "ipython_path", "/opt/homebrew/Caskroom/miniforge/base/bin/ipython"
            ),
        )


def load_config() -> Config:
    """Load configuration from ~/.nb/config.toml."""
    config_path = os.path.expanduser("~/.nb/config.toml")

    if not os.path.exists(config_path):
        print(f"Configuration file not found: {config_path}")
        print("Please create it with the following content:")
        print("""[default]
notebooks_paths = "~/Documents/Notebooks"
jupyter_path = "/opt/homebrew/Caskroom/miniforge/base/bin/jupyter"
ipython_path = "/opt/homebrew/Caskroom/miniforge/base/bin/ipython"
""")
        sys.exit(1)

    try:
        with open(config_path, "rb") as f:
            config_dict = tomllib.load(f)
            return Config.from_dict(config_dict.get("default", {}))
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)


# Constants
CACHE_PATH = os.path.expanduser("~/.cache/nb/")
LOCK_FILE_PATH = os.path.join(CACHE_PATH, "lock")
INTERPRETERS_MAPPING_PATH = os.path.join(CACHE_PATH, "interpreters.json")

START_MARKER_RE = re.compile(r"^#\s*nb\.start(.*?)$")
INTERPRETER_RE = re.compile(r"^#\s*ipython_path\s*=\s*\"(.+?)\".*$")
END_MARKER_RE = re.compile(r"^#\s*nb\.end\s*$")


@contextmanager
def lock_file(path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as lock_file:
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def get_interpreter_path(config: Config, name: str) -> str:
    """Get interpreter path for notebook, with fallback to config."""
    with lock_file(LOCK_FILE_PATH):
        if not os.path.exists(INTERPRETERS_MAPPING_PATH):
            return config.ipython_path

        with open(INTERPRETERS_MAPPING_PATH, "r") as f:
            return json.load(f).get(name, config.ipython_path)


def set_interpreter_path(name: str, path: str) -> None:
    """Set interpreter path for notebook."""
    with lock_file(LOCK_FILE_PATH):
        mapping = {}

        if os.path.exists(INTERPRETERS_MAPPING_PATH):
            with open(INTERPRETERS_MAPPING_PATH, "r") as f:
                mapping = json.load(f)

        mapping[name] = path

        with open(INTERPRETERS_MAPPING_PATH, "w") as f:
            json.dump(mapping, f)


def transform_notebook(
    config: Config, name: str, notebook_path: str, script_path: str
) -> bool:
    """Transform notebook to script, extracting the section between nb.start and nb.end."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_file = os.path.join(temp_dir, "temp")
        os.system(
            f"{config.jupyter_path} nbconvert --log-level ERROR --to script {notebook_path} --output {temp_file}"
        )

        temp_script_path = f"{temp_file}.py"
        if not os.path.exists(temp_script_path):
            return False

        with open(temp_script_path, "r") as f:
            content = f.read()

    start_found, end_found = False, False
    custom_interpreter = None
    script_lines = []

    for line in content.split("\n"):
        if line.startswith("# In"):
            continue

        start_match = START_MARKER_RE.match(line)
        end_match = END_MARKER_RE.match(line)
        interpreter_match = INTERPRETER_RE.match(line)

        if start_found and not end_found:
            if start_match:
                raise ValueError("Nested start markers are not supported")
            elif end_match:
                end_found = True
            elif interpreter_match:
                custom_interpreter = interpreter_match.group(1)
            else:
                script_lines.append(line)
        elif start_found and end_found:
            if start_match:
                raise ValueError("Start marker found after end marker")
            elif end_match:
                raise ValueError("Nested end markers are not supported")
        else:
            if start_match:
                start_found = True
            elif end_match:
                raise ValueError("End marker found before start marker")

    # If no start/end markers found, use the entire notebook
    if not start_found:
        script_lines = [
            line for line in content.split("\n") if not line.startswith("# In")
        ]

    with open(script_path, "w") as f:
        f.write("\n".join(script_lines).strip())

    if custom_interpreter:
        set_interpreter_path(name, custom_interpreter)

    return True


def cache_python_files(config: Config) -> None:
    """Cache all Python files in notebooks folder."""
    notebooks_dir = config.expanded_notebooks_path
    cache_dir = CACHE_PATH

    for root, _, files in os.walk(notebooks_dir):
        for file in files:
            if file.endswith((".py", ".ipynb")):
                src_path = os.path.join(root, file)
                rel_path = os.path.relpath(src_path, notebooks_dir)
                dest_path = os.path.join(cache_dir, rel_path)

                if file.endswith(".py"):
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with open(src_path, "r") as src, open(dest_path, "w") as dest:
                        dest.write(src.read())
                elif file.endswith(".ipynb") and not file.endswith(".nbconvert.ipynb"):
                    notebook_name = os.path.splitext(rel_path)[0]
                    script_path = os.path.join(cache_dir, f"{notebook_name}.py")
                    os.makedirs(os.path.dirname(script_path), exist_ok=True)
                    transform_notebook(config, notebook_name, src_path, script_path)


def build_notebook(config: Config, name: str) -> str:
    """Build notebook script and cache it."""
    notebook_path = os.path.join(config.expanded_notebooks_path, f"{name}.ipynb")
    script_path = os.path.join(CACHE_PATH, f"{name}.py")

    if not os.path.exists(notebook_path):
        print(f"Notebook not found: {notebook_path}")
        sys.exit(2)

    notebook_mtime = os.path.getmtime(notebook_path)
    script_mtime = os.path.getmtime(script_path) if os.path.exists(script_path) else 0

    if notebook_mtime > script_mtime:
        if not transform_notebook(config, name, notebook_path, script_path):
            print(f"Error transforming notebook: {notebook_path}")
            sys.exit(3)

    # Cache other Python files
    cache_python_files(config)

    return script_path


def usage():
    """Show usage information."""
    print(f"Usage: {os.path.basename(sys.argv[0])} NOTEBOOK_NAME [ARGS]")
    print("Run a Jupyter notebook from the command line")
    print()
    print("NOTEBOOK_NAME: The name of the notebook (without .ipynb extension)")
    print("ARGS: Arguments to pass to the notebook")


def run_notebook(config: Config, name: str, args: list[str]) -> None:
    """Run a notebook."""
    script_path = build_notebook(config, name)
    interpreter_path = get_interpreter_path(config, name)

    os.execv(interpreter_path, [os.path.basename(interpreter_path), script_path] + args)


def main():
    """Main entry point."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        usage()
        sys.exit(0)

    # Ensure cache directory exists
    os.makedirs(CACHE_PATH, exist_ok=True)

    # Load configuration
    config = load_config()

    # Run notebook
    name = sys.argv[1]
    args = sys.argv[2:]
    run_notebook(config, name, args)


if __name__ == "__main__":
    main()
