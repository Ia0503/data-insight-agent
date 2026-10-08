"""Download only required official model files, pin revision, and verify weight checksum."""

import hashlib
import json
import os

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

from app.core.config import get_settings
from huggingface_hub import HfApi, hf_hub_download


def main():
    settings = get_settings()
    info = HfApi().model_info(
        settings.embedding_model, revision=settings.embedding_revision, files_metadata=True
    )
    if info.sha != settings.embedding_revision:
        raise RuntimeError("Use an exact model commit hash, not a moving branch.")
    directory = settings.embedding_dir
    directory.mkdir(parents=True, exist_ok=True)
    required = {
        "config.json",
        "tokenizer.json",
        "tokenizer_config.json",
        "special_tokens_map.json",
        "vocab.txt",
    }
    names = {entry.rfilename: entry for entry in info.siblings}
    weight = "model.safetensors" if "model.safetensors" in names else "pytorch_model.bin"
    required.add(weight)
    checksums = {}
    for name in sorted(required & names.keys()):
        path = hf_hub_download(
            settings.embedding_model, name, revision=info.sha, local_dir=directory
        )
        with open(path, "rb") as file:
            digest = hashlib.file_digest(file, "sha256").hexdigest()
        if names[name].lfs and digest != names[name].lfs.sha256:
            raise RuntimeError("Official model checksum differs.")
        checksums[name] = digest
    manifest = {"model": settings.embedding_model, "revision": info.sha, "files": checksums}
    temporary = directory / "manifest.tmp"
    temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    temporary.replace(directory / "manifest.json")
    print(f"Model ready: {settings.embedding_model} @ {info.sha}; directory={directory}")


if __name__ == "__main__":
    main()
