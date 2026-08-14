#!/usr/bin/env python3
"""
Resize and recompress photos before they go into the Hugo site's content/ folder.

Hugo already resizes/WebP-converts images at build time for display, but it
never shrinks the *source* files sitting in git — those stay whatever size
they were uploaded at forever (git keeps every version in history). This
script fixes the source files themselves, so the repo doesn't keep bloating.

Usage:
    python3 compress-images.py                  # processes ./content by default
    python3 compress-images.py content/en/posts/20260810-my-new-post
    python3 compress-images.py path/to/one-photo.jpg

What it does to each JPEG/PNG found:
    - Applies EXIF rotation permanently, then strips EXIF (GPS/camera metadata
      you don't need on the web, and it bloats file size).
    - Resizes so the longest side is at most --max-dim (default 2000px --
      generous headroom over the site's 1600px max display width).
    - Re-saves as JPEG quality --quality (default 85), progressive, optimized.
    - Skips a file if the result wouldn't actually be smaller.
    - Leaves anything already small untouched (--skip-under, default 300KB)
      unless it's still oversized in dimensions.

Also reports (but does NOT delete) exact duplicate images found by content
hash, so you can decide what to do with them yourself.
"""

import argparse
import hashlib
import sys
from pathlib import Path

from PIL import Image, ImageOps

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if abs(n) < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def find_images(paths):
    files = []
    for p in paths:
        p = Path(p)
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            files.append(p)
        elif p.is_dir():
            for ext in IMAGE_EXTS:
                files.extend(p.rglob(f"*{ext}"))
                files.extend(p.rglob(f"*{ext.upper()}"))
    return sorted(set(files))


def compress_one(path: Path, max_dim: int, quality: int, skip_under: int):
    original_size = path.stat().st_size

    if original_size < skip_under:
        with Image.open(path) as im:
            w, h = im.size
        if max(w, h) <= max_dim:
            return None  # already small enough in both bytes and dimensions

    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)  # bake in correct rotation before stripping EXIF
        w, h = im.size

        if max(w, h) > max_dim:
            if w >= h:
                new_w, new_h = max_dim, round(h * max_dim / w)
            else:
                new_h, new_w = max_dim, round(w * max_dim / h)
            im = im.resize((new_w, new_h), Image.LANCZOS)

        ext = path.suffix.lower()
        tmp_path = path.with_suffix(path.suffix + ".tmp")

        if ext == ".png":
            if im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
            im.save(tmp_path, format="PNG", optimize=True)
        else:
            if im.mode not in ("RGB",):
                im = im.convert("RGB")
            im.save(tmp_path, format="JPEG", quality=quality, optimize=True, progressive=True)

    new_size = tmp_path.stat().st_size
    if new_size >= original_size:
        try:
            tmp_path.unlink()
        except OSError:
            pass  # some mounted/synced drives briefly lock just-written files; harmless leftover .tmp
        return None

    tmp_path.replace(path)
    return original_size, new_size


def find_duplicates(files):
    hashes = {}
    for f in files:
        h = hashlib.md5(f.read_bytes()).hexdigest()
        hashes.setdefault(h, []).append(f)
    return {h: paths for h, paths in hashes.items() if len(paths) > 1}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", default=["content"], help="files or folders to process (default: content)")
    ap.add_argument("--max-dim", type=int, default=2000, help="max width/height in px (default 2000)")
    ap.add_argument("--quality", type=int, default=85, help="JPEG quality (default 85)")
    ap.add_argument("--skip-under", type=int, default=300 * 1024, help="skip files already under this many bytes if dimensions are fine (default 300KB)")
    ap.add_argument("--dry-run", action="store_true", help="report what would happen, don't write anything")
    args = ap.parse_args()

    files = find_images(args.paths)
    if not files:
        print("No JPEG/PNG images found.")
        return

    print(f"Found {len(files)} image(s).\n")

    total_before = 0
    total_after = 0
    changed = 0

    for f in files:
        before = f.stat().st_size
        if args.dry_run:
            with Image.open(f) as im:
                w, h = im.size
            needs_work = before >= args.skip_under or max(w, h) > args.max_dim
            if needs_work:
                print(f"[would process] {f}  ({human(before)}, {w}x{h})")
            continue

        try:
            result = compress_one(f, args.max_dim, args.quality, args.skip_under)
        except Exception as e:
            print(f"[skipped, error] {f}  ({e})")
            continue
        if result:
            before, after = result
            total_before += before
            total_after += after
            changed += 1
            print(f"{f}  {human(before)} -> {human(after)}  (-{(1 - after / before) * 100:.0f}%)")

    if not args.dry_run:
        print(f"\nCompressed {changed}/{len(files)} images.")
        if changed:
            print(f"Total: {human(total_before)} -> {human(total_after)}  "
                  f"(saved {human(total_before - total_after)}, "
                  f"-{(1 - total_after / total_before) * 100:.0f}%)")

    dupes = find_duplicates(files)
    if dupes:
        print(f"\n{len(dupes)} set(s) of exact duplicate images found (not deleted, review manually):")
        for group in dupes.values():
            print("  - " + "\n    ".join(str(p) for p in group))


if __name__ == "__main__":
    main()
