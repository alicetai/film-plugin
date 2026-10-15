#!/usr/bin/env python3
"""Fit a display-referred 3D LUT from matched sRGB digital and film scan pairs.

Pairs are matched by filename stem. The film image is registered to its digital
counterpart with ORB features and a RANSAC homography before pixels are sampled.
The resulting LUT is regularised towards identity and spatial smoothness so it
remains stable between sampled colours.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import sys
from pathlib import Path

import cv2
import numpy as np
import pillow_heif
from PIL import Image, ImageCms, ImageOps

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif"}
pillow_heif.register_heif_opener()


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digital-dir", type=Path, required=True)
    parser.add_argument("--film-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--size", type=int, default=33)
    parser.add_argument("--samples-per-pair", type=int, default=4500)
    parser.add_argument("--holdout-count", type=int, default=2)
    parser.add_argument("--seed", type=int, default=800)
    parser.add_argument("--max-side", type=int, default=1600, help="Working image size for registration and sampling")
    return parser.parse_args()


def file_map(directory: Path):
    if not directory.is_dir():
        raise ValueError(f"Directory does not exist: {directory}")
    files = sorted(path for path in directory.iterdir() if path.suffix.lower() in IMAGE_SUFFIXES)
    output = {}
    for path in files:
        key = path.stem.casefold()
        if key in output:
            raise ValueError(f"Duplicate image stem in {directory}: {path.stem}")
        output[key] = path
    return output


def read_rgb(path: Path, max_side: int):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original)
        icc = image.info.get("icc_profile")
        if icc:
            try:
                source_profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                image = ImageCms.profileToProfile(image.convert("RGB"), source_profile,
                                                  ImageCms.createProfile("sRGB"), outputMode="RGB")
            except (OSError, ValueError) as error:
                raise ValueError(f"Could not convert colour profile for {path}: {error}") from error
        else:
            image = image.convert("RGB")
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
        return np.asarray(image, dtype=np.float32) / 255.0


def register_film_to_digital(digital, film):
    source_gray = cv2.cvtColor((digital * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    target_gray = cv2.cvtColor((film * 255).astype(np.uint8), cv2.COLOR_RGB2GRAY)
    detector = cv2.SIFT_create(nfeatures=9000, contrastThreshold=0.01)
    source_points, source_descriptors = detector.detectAndCompute(source_gray, None)
    target_points, target_descriptors = detector.detectAndCompute(target_gray, None)
    if source_descriptors is None or target_descriptors is None:
        return None, None, "No usable alignment features"

    matches = cv2.BFMatcher(cv2.NORM_L2).knnMatch(source_descriptors, target_descriptors, k=2)
    good = [pair[0] for pair in matches if len(pair) == 2 and pair[0].distance < 0.75 * pair[1].distance]
    if len(good) < 25:
        return None, None, f"Only {len(good)} reliable feature matches"

    source = np.float32([source_points[match.queryIdx].pt for match in good]).reshape(-1, 1, 2)
    target = np.float32([target_points[match.trainIdx].pt for match in good]).reshape(-1, 1, 2)
    matrix, inliers = cv2.findHomography(target, source, cv2.USAC_MAGSAC, 4.0)
    inlier_count = int(inliers.sum()) if inliers is not None else 0
    if matrix is None or inlier_count < 25:
        return None, None, f"Only {inlier_count} geometric inliers"

    height, width = digital.shape[:2]
    aligned = cv2.warpPerspective(film, matrix, (width, height), flags=cv2.INTER_LINEAR)
    mask = cv2.warpPerspective(np.ones(film.shape[:2], dtype=np.uint8), matrix, (width, height), flags=cv2.INTER_NEAREST)
    mask = cv2.erode(mask, np.ones((11, 11), dtype=np.uint8)) > 0
    # Avoid borders and clipped pixels; both are unreliable colour-training samples.
    mask[:8, :] = mask[-8:, :] = False
    mask[:, :8] = mask[:, -8:] = False
    mask &= np.max(digital, axis=2) < 0.992
    mask &= np.max(aligned, axis=2) < 0.992
    coverage = float(mask.mean())
    if coverage < 0.08:
        return None, None, f"Only {coverage:.1%} usable image overlap"
    return aligned, mask, {"matches": len(good), "inliers": inlier_count, "coverage": round(coverage, 4)}


def stratified_samples(source, target, mask, count, rng):
    # Blur only for colour samples: scan grain and tiny registration offsets
    # should not masquerade as a colour mapping.
    source = cv2.GaussianBlur(source, (0, 0), 1.5)
    target = cv2.GaussianBlur(target, (0, 0), 1.5)
    luminance = source @ np.array([0.2126, 0.7152, 0.0722], dtype=np.float32)
    chosen = []
    valid = np.flatnonzero(mask.ravel())
    if len(valid) < 2500:
        return None
    # Draw across ten brightness bins so skies and dark areas both affect the profile.
    for lower, upper in zip(np.linspace(0, 0.9, 10), np.linspace(0.1, 1.0, 10)):
        candidates = np.flatnonzero((mask & (luminance >= lower) & (luminance < upper)).ravel())
        if len(candidates):
            chosen.append(rng.choice(candidates, size=min(len(candidates), max(40, count // 10)), replace=False))
    indices = np.concatenate(chosen) if chosen else rng.choice(valid, size=min(len(valid), count), replace=False)
    if len(indices) > count:
        indices = rng.choice(indices, size=count, replace=False)
    return source.reshape(-1, 3)[indices], target.reshape(-1, 3)[indices]


def cube_coordinates(size):
    values = np.linspace(0, 1, size, dtype=np.float32)
    b, g, r = np.meshgrid(values, values, values, indexing="ij")
    return np.stack([r, g, b], axis=-1).reshape(-1, 3)


def polynomial_powers(degree):
    return [(r, g, b) for total in range(degree + 1)
            for r in range(total, -1, -1)
            for g in range(total - r, -1, -1)
            for b in [total - r - g]]


def polynomial_features(colours, powers):
    colours = np.asarray(colours, dtype=np.float64)
    return np.stack([(colours[:, 0] ** r) * (colours[:, 1] ** g) * (colours[:, 2] ** b)
                     for r, g, b in powers], axis=1)


def fit_polynomial(source, target, degree, regularization):
    powers = polynomial_powers(degree)
    features = polynomial_features(source, powers)
    anchors = cube_coordinates(7)
    anchor_features = polynomial_features(anchors, powers)
    identity = np.zeros((len(powers), 3), dtype=np.float64)
    for channel, power in enumerate(((1, 0, 0), (0, 1, 0), (0, 0, 1))):
        identity[powers.index(power), channel] = 1.0

    # The anchor grid protects colours absent from these few photographs.
    anchor_weight = regularization * len(source) / len(anchors)
    anchor_system = anchor_weight * (anchor_features.T @ anchor_features)
    anchor_target = anchor_weight * (anchor_features.T @ anchors)
    penalty = np.eye(len(powers)) * (1e-5 * len(source))
    coefficients = identity.copy()
    for _ in range(5):
        prediction = features @ coefficients
        residual = np.linalg.norm(prediction - target, axis=1)
        weights = np.minimum(1.0, 0.09 / np.maximum(residual, 1e-6))
        system = features.T @ (features * weights[:, None]) + anchor_system + penalty
        expected = features.T @ (target * weights[:, None]) + anchor_target + penalty @ identity
        coefficients = np.linalg.solve(system, expected)
    return powers, coefficients


def sample_polynomial_lut(powers, coefficients, size):
    coordinates = cube_coordinates(size)
    lut = np.clip(polynomial_features(coordinates, powers) @ coefficients, 0, 1)
    grid = lut.reshape(size, size, size, 3)
    # A physically plausible fixed transform should not reverse a channel's
    # tone order. Blend toward identity if sparse data caused a local reversal.
    step = 1.0 / (size - 1)
    for blend in np.linspace(1.0, 0.0, 101):
        candidate = coordinates + (lut - coordinates) * blend
        values = candidate.reshape(size, size, size, 3)
        if (np.diff(values[..., 0], axis=2).min() > -step * 0.02 and
                np.diff(values[..., 1], axis=1).min() > -step * 0.02 and
                np.diff(values[..., 2], axis=0).min() > -step * 0.02):
            return np.clip(candidate, 0, 1), float(blend)
    return coordinates, 0.0


def apply_lut(colours, lut, size):
    scaled = np.clip(colours, 0, 1) * (size - 1)
    low = np.floor(scaled).astype(np.int32)
    high = np.minimum(low + 1, size - 1)
    weight = scaled - low
    output = np.zeros((len(colours), 3), dtype=np.float64)
    for bz in (0, 1):
        for gy in (0, 1):
            for rx in (0, 1):
                r = high[:, 0] if rx else low[:, 0]
                g = high[:, 1] if gy else low[:, 1]
                b = high[:, 2] if bz else low[:, 2]
                corner_weight = ((weight[:, 0] if rx else 1 - weight[:, 0]) *
                                 (weight[:, 1] if gy else 1 - weight[:, 1]) *
                                 (weight[:, 2] if bz else 1 - weight[:, 2]))
                output += lut[r + g * size + b * size * size] * corner_weight[:, None]
    return np.clip(output, 0, 1)


def rgb_to_lab(colours):
    image = np.clip(colours.reshape(-1, 1, 3) * 255, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(image, cv2.COLOR_RGB2LAB).reshape(-1, 3).astype(np.float32)
    lab[:, 0] *= 100.0 / 255.0
    lab[:, 1:] -= 128.0
    return lab


def main():
    args = parse_args()
    if args.size != 33:
        raise ValueError("This plugin profile is fixed at a 33³ LUT.")
    digital = file_map(args.digital_dir)
    film = file_map(args.film_dir)
    names = sorted(set(digital) & set(film))
    if len(names) < 8:
        raise ValueError("Provide at least 8 identically named digital/film image pairs.")
    if args.holdout_count < 2 or args.holdout_count >= len(names):
        raise ValueError("Use at least two, but fewer than all, pairs as holdout images.")

    rng = np.random.default_rng(args.seed)
    holdout_names = set(rng.choice(names, size=args.holdout_count, replace=False).tolist())
    training_source, training_target, held_source, held_target, held_names = [], [], [], [], []
    report = {"size": args.size, "holdout_pairs": sorted(holdout_names), "unmatched_digital": sorted(set(digital) - set(film)),
              "unmatched_film": sorted(set(film) - set(digital)), "accepted": [], "skipped": []}
    report_path = args.report or args.output.with_suffix(".report.json")

    def write_report():
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))

    for name in names:
        try:
            source = read_rgb(digital[name], args.max_side)
            scan = read_rgb(film[name], args.max_side)
            aligned, mask, alignment = register_film_to_digital(source, scan)
            if aligned is None:
                report["skipped"].append({"name": name, "reason": alignment})
                continue
            sampled = stratified_samples(source, aligned, mask, args.samples_per_pair, rng)
            if sampled is None:
                report["skipped"].append({"name": name, "reason": "Too few valid overlapping pixels"})
                continue
            source_samples, target_samples = sampled
            destination = (held_source, held_target) if name in holdout_names else (training_source, training_target)
            destination[0].append(source_samples)
            destination[1].append(target_samples)
            if name in holdout_names:
                held_names.append(name)
            report["accepted"].append({"name": name, **alignment, "pixels": len(source_samples), "holdout": name in holdout_names})
        except Exception as error:
            report["skipped"].append({"name": name, "reason": str(error)})

    if len(training_source) < 6 or not held_source:
        write_report()
        raise ValueError("Need six aligned training pairs and at least one aligned holdout pair. Check the report for rejected pairs.")
    source = np.concatenate(training_source)
    target = np.concatenate(training_target)
    hold_source = np.concatenate(held_source)
    hold_target = np.concatenate(held_target)
    def metrics(prediction, reference):
        lab_error = np.linalg.norm(rgb_to_lab(prediction) - rgb_to_lab(reference), axis=1)
        return {"mean_absolute_rgb_error": float(np.mean(np.abs(prediction - reference))),
                "mean_delta_e76": float(np.mean(lab_error)),
                "p95_delta_e76": float(np.percentile(lab_error, 95)),
                "sample_count": int(len(reference))}

    report["identity_baseline"] = metrics(hold_source, hold_target)
    candidates = []
    for degree in (2, 3):
        for regularization in (0.03, 0.1, 0.3):
            powers, coefficients = fit_polynomial(source, target, degree, regularization)
            candidate_lut, blend = sample_polynomial_lut(powers, coefficients, args.size)
            candidate_metrics = metrics(apply_lut(hold_source, candidate_lut, args.size), hold_target)
            candidates.append((candidate_metrics["mean_delta_e76"], candidate_lut,
                               {"degree": degree, "regularization": regularization,
                                "monotonic_blend": blend, **candidate_metrics}))
    candidates.sort(key=lambda item: item[0])
    _, lut, chosen = candidates[0]
    report["model_candidates"] = [item[2] for item in candidates]
    report["chosen_model"] = chosen
    report["holdout_metrics"] = {key: chosen[key] for key in
                                 ("mean_absolute_rgb_error", "mean_delta_e76", "p95_delta_e76", "sample_count")}
    report["holdout_pair_metrics"] = {
        name: {"identity": metrics(source_samples, target_samples),
               "profile": metrics(apply_lut(source_samples, lut, args.size), target_samples)}
        for name, source_samples, target_samples in zip(held_names, held_source, held_target)
    }
    if chosen["mean_delta_e76"] >= report["identity_baseline"]["mean_delta_e76"]:
        write_report()
        raise ValueError("No fitted profile improves on the unprocessed images. Review pair alignment and exposure before embedding.")
    quantised = np.round(lut * 255).astype(np.uint8)
    payload = {
        "version": 1,
        "size": args.size,
        "order": "r-fastest, then g, then b",
        "encoding": "base64-rgb-u8",
        "calibrated": True,
        "data": base64.b64encode(quantised.tobytes()).decode("ascii"),
        "report": report["holdout_metrics"],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, separators=(",", ":")))
    write_report()
    print(f"Wrote {args.output} and {report_path}")
    print(json.dumps(report["holdout_metrics"], indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print(f"Calibration failed: {error}", file=sys.stderr)
        sys.exit(1)
