---
name: managing-environments
description: Best practices for managing development environments including Python venv and conda. Always check environment status before installations and confirm with user before proceeding. Use when installing packages, creating a new environment, or troubleshooting import/dependency errors.
---

# Managing Environments

## Instructions

### Step 1: Check before you install anything

Before running `pip install`, `conda install`, `uv add`, etc., check:

1. Which environment is currently active (`which python`, `python -V`, or
   the shell prompt prefix for conda/venv).
2. Whether the target project already has an environment defined
   (`.venv/`, `environment.yml`, `pyproject.toml`, `requirements*.txt`).
3. Whether the package is already installed (`pip show <pkg>`) before
   assuming it needs adding.

Never install into the system/base Python or a conda `base` environment for
project work — always activate the project's own environment first.

### Step 2: Confirm before proceeding with changes

Installing, upgrading, or removing packages changes the environment for every
future session that uses it. Before running the install command, tell the
user what you're about to install/change and why, and prefer being asked to
proceed for anything beyond adding a single obviously-needed package — this
mirrors the general rule of confirming before actions with lasting effects.

### Step 3: venv workflow

```bash
python -m venv .venv
.venv/Scripts/activate      # Windows
source .venv/bin/activate   # POSIX
pip install -r requirements.txt
```

Keep `requirements.txt` (or `pyproject.toml` dependencies) updated when you
add a package — an environment that works locally but isn't reflected in the
lockfile/requirements breaks for the next person who sets it up.

### Step 4: conda workflow

```bash
conda env create -f environment.yml   # first time
conda activate <env-name>
conda env update -f environment.yml --prune   # after editing environment.yml
```

Prefer `environment.yml` as the source of truth over ad-hoc `conda install`
commands so the environment is reproducible from the file alone.

### Step 5: Diagnosing environment problems

Import errors and version conflicts are usually an environment mismatch, not
a code bug — check `python -c "import sys; print(sys.executable)"` first to
confirm you're actually inside the environment you think you are before
debugging the code itself.
