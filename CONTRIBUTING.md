## Running CI tests in a separate Test branch

This repository contains a GitHub Actions workflow that runs `pytest` on an Ubuntu runner for branches matching `test/**`.

Quick steps to create and push a test branch which will trigger the CI run:

```powershell
# create and switch to a test branch
git checkout -b test/ci-pytests

# add workflow or other changes if needed
git add .github/workflows/pytest.yml
git commit -m "ci: add pytest workflow for test branches"

# push branch to origin and set upstream
git push -u origin test/ci-pytests
```

Notes:
- The workflow runs on `ubuntu-latest` so POSIX features (like `fcntl`) are available.
- CI will install dependencies from `requirements.txt` if present; to include test tools explicitly, `requirements-dev.txt` is provided.

To install development dependencies locally:

```bash
python -m pip install -r requirements-dev.txt
```

If tests fail locally on Windows due to platform-specific imports (for example `fcntl`), prefer running tests in WSL or let GitHub Actions run them on Linux.
