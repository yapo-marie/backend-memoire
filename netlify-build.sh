#!/usr/bin/env bash
set -e

echo "Installing Rust toolchain..."
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y

# Ensure cargo is on PATH
source "$HOME/.cargo/env"

echo "Upgrading pip and build tools..."
python -m pip install --upgrade pip setuptools wheel

echo "Installing Python dependencies..."
pip install -r requirements.txt

echo "Build completed successfully!"
