# Prerequisites

You need four things installed: **Docker**, **Java 17+**, **Python 3.10+**, and
**Git**. Below is how to check for each and install if missing.

## 1. Check what you already have

Open a terminal (Mac: Terminal app; Windows: PowerShell) and run:

```bash
docker --version
java --version
python3 --version
git --version
```

If any command says "command not found" / "not recognized", install it below.

## 2. Install Docker Desktop

- Mac: download from https://www.docker.com/products/docker-desktop/ →
  drag to Applications → open it once to finish setup.
- Windows: same link. Requires WSL2 — the installer will prompt you to enable
  it if it isn't already; follow its instructions and restart when asked.
- Linux: `curl -fsSL https://get.docker.com | sh` then
  `sudo usermod -aG docker $USER` and log out/in.

Verify: `docker run hello-world` should print a success message.

## 3. Install Java 17+ (needed to run Synthea, which is a Java program)

- Mac (with Homebrew — install Homebrew first from https://brew.sh if needed):
  `brew install openjdk@17`
  then follow the printed instructions to add it to your PATH.
- Windows: download the installer from
  https://adoptium.net/temurin/releases/?version=17 (choose the `.msi`),
  run it, accept defaults.
- Linux: `sudo apt install openjdk-17-jdk` (Debian/Ubuntu) or
  `sudo dnf install java-17-openjdk` (Fedora).

Verify: `java --version` should show `17` or higher.

## 4. Install Python 3.10+

- Mac: `brew install python@3.11`
- Windows: https://www.python.org/downloads/ — **check the box "Add
  python.exe to PATH"** during install, this trips people up otherwise.
- Linux: usually already present; if not, `sudo apt install python3.11`

Verify: `python3 --version`

## 5. Install Git (to clone Synthea and the OMOP CDM DDL repos)

- Mac: `brew install git` (or it prompts you to install Xcode command line
  tools the first time you run `git`)
- Windows: https://git-scm.com/download/win
- Linux: `sudo apt install git`

## Once all four are installed

Come back to the main [README.md](../README.md) Quickstart section and
proceed with `docker compose up -d`.
