"""Create a local configuration without overwriting an existing .env."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
template = (root / ".env.example").read_text()
for key in ("COOKIE_SECRET", "CAPTURE_TOKEN_SECRET", "MEDIA_TOKEN_SECRET"):
    template = template.replace(f"{key}=\n", f"{key}={secrets.token_urlsafe(48)}\n")
fd = os.open(root / ".env", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, "w") as output:
    output.write(template)
print("Created .env (development only).")
