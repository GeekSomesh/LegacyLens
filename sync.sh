#!/bin/sh
# Copy the working tree to the LinuxONE VM (host alias "linuxone" in ~/.ssh/config).
cd "$(dirname "$0")" && tar czf - --exclude=.git --exclude=build --exclude=__pycache__ --exclude=models/*.onnx . | ssh linuxone 'mkdir -p ~/LegacyLens && tar xzf - -C ~/LegacyLens'
