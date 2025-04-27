#!/usr/bin/env python3
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import tomllib

START_MARKER_RE = re.compile(r"^#\s*nb\.start(.*?)$")
END_MARKER_RE = re.compile(r"^#\s*nb\.end\s*$")


@dataclass(frozen=True)
class Config:
    notebooks_path: str
    jupyter_path: str
    ipython_path: str

    cache_path: str = os.path.expanduser("~/.nb/cache/")
    lock_file_path: str = os.path.expanduser("~/.nb/lock")
    interpreters_mapping_path: str = os.path.expanduser("~/.nb/interpreters.json")

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "Config":
        # Check if required paths are provided
        for key in ["notebooks_path", "jupyter_path", "ipython_path"]:
            if key not in config_dict:
                raise ValueError(f"Required config key '{key}' not found")

        # Create Config with expanded paths
        return cls(
            notebooks_path=os.path.expanduser(config_dict["notebooks_path"]),
            jupyter_path=os.path.expanduser(config_dict["jupyter_path"]),
            ipython_path=os.path.expanduser(config_dict["ipython_path"]),
        )


def load_config(path: str) -> Config:
    try:
        with open(path, "rb") as f:
            config_dict = tomllib.load(f)
            return Config.from_dict(config_dict.get("default", {}))
    except Exception as e:
        print(f"Error loading configuration: {e}")
        sys.exit(1)


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
    """Get interpreter path for notebook, with fallback to config"""
    with lock_file(config.lock_file_path):
        if not os.path.exists(config.interpreters_mapping_path):
            return config.ipython_path

        with open(config.interpreters_mapping_path, "r") as f:
            return json.load(f).get(name, config.ipython_path)


def set_interpreter_path(config: Config, name: str, path: str) -> None:
    """Set interpreter path for notebook"""
    with lock_file(config.lock_file_path):
        mapping = {}

        if os.path.exists(config.interpreters_mapping_path):
            with open(config.interpreters_mapping_path, "r") as f:
                mapping = json.load(f)

        mapping[name] = path

        with open(config.interpreters_mapping_path, "w") as f:
            json.dump(mapping, f)


def parse_file(content: str) -> tuple[str, dict[str, Any]]:
    start_found, end_found = False, False
    parsing_toml = False

    script_lines = []
    toml_lines = []

    for line in content.split("\n"):
        start_match = START_MARKER_RE.match(line)
        end_match = END_MARKER_RE.match(line)

        if start_found and not end_found:
            if start_match:
                raise ValueError("Nested start markers are not supported")
            elif end_match:
                end_found = True
            elif parsing_toml:
                if line.startswith("#"):
                    # Collect TOML lines (remove leading # and space)
                    toml_content = line[1:].strip()
                    # Skip empty lines
                    if toml_content:
                        toml_lines.append(toml_content)
                else:
                    # If we encounter a non-comment, TOML section is over
                    parsing_toml = False
                    script_lines.append(line)
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
                # Start collecting TOML right after the start marker
                parsing_toml = True
            elif end_match:
                raise ValueError("End marker found before start marker")

    # If no markers found, use the entire notebook
    if not start_found:
        script_lines = content.split("\n")

    try:
        config_data = tomllib.loads("\n".join(toml_lines)) if toml_lines else {}
    except tomllib.TOMLDecodeError:
        raise ValueError(
            "Invalid TOML config after start marker, make sure to add empty line after start marker and config lines"
        )

    return "\n".join(script_lines).strip(), config_data


def transform_notebook(
    config: Config, name: str, notebook_path: str, script_path: str
) -> bool:
    """Transform notebook to script, extracting the section between nb.start and nb.end"""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = os.path.join(temp_dir, secrets.token_hex(8))
        os.system(
            f"{config.jupyter_path} nbconvert --log-level ERROR --to script {notebook_path} --output {temp_path}"
        )

        temp_script_path = f"{temp_path}.py"
        if not os.path.exists(temp_script_path):
            return False

        with open(temp_script_path, "r") as f:
            content = f.read()

    script_content, script_config = parse_file(content)

    with open(script_path, "w") as f:
        f.write(script_content)

    if "ipython_path" in script_config:
        set_interpreter_path(config, name, script_config["ipython_path"])

    return True


def cache_python_files(config: Config) -> None:
    """Cache only modified Python files in notebooks folder"""
    for root, _, files in os.walk(config.notebooks_path):
        for file in files:
            if not file.endswith((".py", ".ipynb")):
                continue

            src_path = os.path.join(root, file)
            dest_path = os.path.join(
                config.cache_path, os.path.relpath(src_path, config.notebooks_path)
            )

            # Check if the destination file exists and is up-to-date
            if os.path.exists(dest_path):
                src_mtime = os.path.getmtime(src_path)
                dest_mtime = os.path.getmtime(dest_path)
                if src_mtime <= dest_mtime:
                    continue

            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_path, dest_path)


def build_notebook(config: Config, name: str) -> str:
    """Build notebook script and cache it"""
    name_hashed = hashlib.md5(name.encode()).hexdigest()
    notebook_path = os.path.join(config.notebooks_path, f"{name}.ipynb")
    script_path = os.path.join(config.cache_path, f"{name_hashed}.py")

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


def run_notebook(config: Config, name: str, args: list[str]) -> None:
    script_path = build_notebook(config, name)
    interpreter_path = get_interpreter_path(config, name)

    os.execv(interpreter_path, [os.path.basename(interpreter_path), script_path] + args)


def show_usage():
    print(f"Usage: {os.path.basename(sys.argv[0])} NOTEBOOK_NAME [ARGS]")
    print("Run a Jupyter notebook from the command line")
    print()
    print("NOTEBOOK_NAME: The name of the notebook (without .ipynb extension)")
    print("ARGS: Arguments to pass to the notebook")


def show_example_config():
    print("Example configuration:")
    print("[default]")
    print('notebooks_path = "~/Documents/Notebooks"')
    print('jupyter_path = "/opt/homebrew/Caskroom/miniforge/base/bin/jupyter"')
    print('ipython_path = "/opt/homebrew/Caskroom/miniforge/base/bin/ipython"')


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help"):
        show_usage()
        sys.exit(0)

    config_path = os.path.expanduser("~/.nb/config.toml")

    if not os.path.exists(config_path):
        print(f"Configuration file not found: {config_path}")
        show_example_config()
        sys.exit(1)

    # Load configuration
    config = load_config(config_path)

    # Ensure cache directory exists
    os.makedirs(config.cache_path, exist_ok=True)

    # Run notebook
    name = sys.argv[1]
    args = sys.argv[2:]
    run_notebook(config, name, args)


if __name__ == "__main__":
    main()
