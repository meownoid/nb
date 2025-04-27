# nb

Command line tool to quickly run Python notebooks.

## Installation

You need to have [uv](https://github.com/astral-sh/uv) installed.

```bash
git clone https://github.com/meownoid/nb.git
cd nb
uv tool install --reinstall  .
```
## Usage

To get started with `nb`, create a configuration file at `~/.nb/config.toml`:

```toml
[default]
notebooks_paths = "~/Documents/Notebooks"
jupyter_path = "/opt/homebrew/Caskroom/miniforge/base/bin/jupyter"
ipython_path = "/opt/homebrew/Caskroom/miniforge/base/bin/ipython"
```

Once configured, you can run:

```shell
nb hello-world
```

If the notebook `~/Documents/Notebooks/hello-world.ipynb` exists, it will be executed. By default, the entire notebook is executed. To execute only a specific section, enclose it between special comments in the notebook:

```python
...

# nb.start

print("Hello, world!")

# nb.end

...
```

Only one `nb.start` - `nb.end` section is allowed per notebook. You can override the Python interpreter for a specific notebook by adding a configuration after the `nb.start` comment:

```python
# nb.start
# ipython_path = "/path/to/ipython"
```

### Command Line Arguments

`nb` is a lightweight tool that converts notebooks into Python scripts and executes them with the specified interpreter. It does not modify the code or create CLI interfaces. For handling command-line arguments, consider using a library like `fire` alongside `nb`.

### Imports

All Python files and packages within the notebooks folder are cached along with the notebook script. For external imports, use absolute paths to ensure proper resolution.

### Data Handling

Data is not cached. If you are reading files from the notebooks folder, use absolute paths to avoid issues.

## How It Works

When you run a notebook, `nb` checks if it has already been converted to a script and whether it has changed since the last execution. If it is the first run or the notebook has been modified, `nb` converts it into a script using the configured Jupyter executable. Converted notebooks are stored in a cache. Additionally, all `.ipynb` and `.py` files in the notebooks folder are cached to support imports and `%run` magic commands.
