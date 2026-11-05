"""Rebuild the 395 Smart24Food spoon and ladle inputs with their transparency handled.

The main run converted these palette PNGs straight to RGB (run.py line 37: ImageOps.exif_transpose(image)
.convert('RGB')). That turns every transparent pixel into the palette's key colour, so the models saw each
spoon on flat green (310 photos) or black (85). This script:

1. re-downloads each original (395 GETs to smart24food.uap.edu.ar) and checks it is byte-identical to the
   main run's download (inputs/<id>.json: downloaded_sha256 and downloaded_bytes);
2. runs the main run's exact pipeline on it and checks the result equals the main run's input JPEG, which
   proves nothing else in the pipeline has changed;
3. builds the fixed input: the same steps, with the image composited onto white before the RGB conversion.

Writes the fixed JPEGs to /tmp/meal-vision-benchmark (the main run's working folder, never replacing a file)
and to inputs/ here, with one metadata JSON each, plus manifest_fix.json. Nothing else is written.
"""
import hashlib
import io
import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageOps

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
WORK = Path("/tmp/meal-vision-benchmark")
SEED = "spoon-ladle-fix-2026-11-05"
BACKGROUND = (255, 255, 255)
SPOON_LADLE_UTENSILS = {"11", "12", "13", "14", "15", "16"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def encode(image):
    """run.py from line 37 on: record the size, fit within 1024 px, JPEG quality 95, 4:4:4, no EXIF."""
    original = image.size
    image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95, subsampling=0, exif=b"")
    return buffer.getvalue(), list(original), list(image.size)


def main_run_jpeg(raw):
    return encode(ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB"))


def fixed_jpeg(raw):
    image = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    has_alpha = image.mode in ("P", "PA", "LA", "RGBA") and (image.mode != "P" or "transparency" in image.info)
    transparent_share = None
    if has_alpha:
        rgba = image.convert("RGBA")
        alpha = rgba.getchannel("A")
        transparent_share = sum(alpha.histogram()[:128]) / (rgba.width * rgba.height)
        white = Image.new("RGBA", rgba.size, BACKGROUND + (255,))
        image = Image.alpha_composite(white, rgba)
    data, original, size = encode(image.convert("RGB"))
    return data, original, size, has_alpha, transparent_share


def download(url, attempts=3):
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url), timeout=60) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
            if attempt == attempts:
                raise
            time.sleep(10 * attempt)


def place(path, data):
    """Write data to path unless an identical file is already there; never replace a different file."""
    if path.exists():
        if sha(path.read_bytes()) != sha(data):
            raise RuntimeError("refusing to replace a different file: %s" % path)
        return
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.rename(path)


def main():
    manifest = json.loads((ROOT / "manifest.json").read_text())
    samples = [s for s in manifest["samples"] if s["dataset"] == "smart24food" and s["source_id"].lower().endswith(".png")]
    assert len(samples) == 395, len(samples)
    assert {s["source_id"].split("/")[-1].split("-")[2] for s in samples} == SPOON_LADLE_UTENSILS
    (HERE / "inputs").mkdir(exist_ok=True)
    out = []
    for n, s in enumerate(samples, 1):
        old = s["sample_id"]
        new = sha((SEED + ":" + old).encode())[:20]
        meta_path = HERE / "inputs" / (new + ".json")
        if meta_path.exists():
            out.append(json.loads(meta_path.read_text()))
            continue
        main_meta = json.loads((ROOT / "inputs" / (old + ".json")).read_text())
        raw = download(s["source_url"])
        if sha(raw) != main_meta["downloaded_sha256"] or len(raw) != main_meta["downloaded_bytes"]:
            raise RuntimeError("download differs from the main run's: %s" % s["source_url"])
        flawed, flawed_original, flawed_size = main_run_jpeg(raw)
        if sha(flawed) != main_meta["input_sha256"]:
            raise RuntimeError("main-run pipeline not reproduced for %s" % old)
        data, original, size, has_alpha, transparent_share = fixed_jpeg(raw)
        if not has_alpha:
            raise RuntimeError("expected a transparent PNG: %s" % s["source_url"])
        if original != flawed_original or size != flawed_size:
            raise RuntimeError("dimensions changed for %s" % old)
        place(WORK / (new + ".jpg"), data)
        place(HERE / "inputs" / (new + ".jpg"), data)
        meta = {"sample_id": new, "main_run_sample_id": old, "dataset": "smart24food", "view": s["view"],
                "source_id": s["source_id"], "source_url": s["source_url"], "food_id": s.get("food_id"),
                "composition_id": s.get("composition_id"), "downloaded_sha256": main_meta["downloaded_sha256"],
                "downloaded_bytes": main_meta["downloaded_bytes"], "main_run_input_sha256": main_meta["input_sha256"],
                "main_run_pipeline_reproduced": True, "background": "white (255, 255, 255)",
                "transparent_share": round(transparent_share, 4), "original_dimensions": original,
                "input_dimensions": size, "input_sha256": sha(data), "input_bytes": len(data),
                "staged_path": str(WORK / (new + ".jpg"))}
        tmp = meta_path.with_name(meta_path.name + ".tmp")
        tmp.write_text(json.dumps(meta, indent=1))
        tmp.rename(meta_path)
        out.append(meta)
        if n % 25 == 0:
            print(json.dumps({"prepared": n, "of": len(samples)}), flush=True)
        time.sleep(0.2)
    doc = {"seed": SEED, "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "why": "Main-run inputs for these 395 PNGs dropped their transparency (flat green or black background).",
           "background": "white (255, 255, 255)", "prompt": manifest["prompt"], "schema": manifest["schema"],
           "samples": out}
    (HERE / "manifest_fix.json").write_text(json.dumps(doc, indent=1))
    print(json.dumps({"prepared": len(out), "manifest": "manifest_fix.json"}), flush=True)


if __name__ == "__main__":
    main()
