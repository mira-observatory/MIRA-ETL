from __future__ import annotations

import gzip
import shutil
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path

import httpx

from mira_etl.config import SourceConfig

ZIP_CONTENT_TYPES = ("application/zip", "application/octet-stream")
GZIP_CONTENT_TYPES = ("application/gzip", "application/x-gzip", "application/octet-stream")


def obtain_zip(
    config: SourceConfig,
    period: str,
    work_dir: Path,
    local_zip: Path | None,
) -> Path:
    downloads_dir = work_dir / "downloads"
    downloads_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    target = (
        downloads_dir
        / f"{config.source}_{period}.zip"
    )

    if local_zip is not None:
        if local_zip.resolve() == target.resolve():
            if not zipfile.is_zipfile(target):
                raise ValueError(f"Local file is not a valid ZIP: {target}")
            return target
        shutil.copyfile(
            local_zip,
            target,
        )
        return target

    url = config.source_url_for_period(period)

    print(f"Downloading: {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "application/zip,"
            "application/octet-stream,"
            "*/*"
        ),
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    try:
        download_with_httpx(
            url=url,
            target=target,
            headers=headers,
            bootstrap_url=config.download.get(
                "bootstrap_url"
            ),
            content_types=ZIP_CONTENT_TYPES,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        print(
            "Cloudflare rejected httpx with HTTP 403; "
            "retrying with curl."
        )
        download_with_curl(
            url=url,
            target=target,
            headers=headers,
        )

    if not zipfile.is_zipfile(target):
        raise ValueError(
            f"Downloaded file is not a valid ZIP: {target}"
        )

    print(
        f"Downloaded ZIP: {target} "
        f"({target.stat().st_size} bytes)"
    )

    return target


def obtain_jsonl_gz(
    config: SourceConfig,
    period: str,
    work_dir: Path,
    local_file: Path | None,
) -> Path:
    """Download the gzipped JSON Lines file that holds the period's year.

    The OCP Data Registry publishes Honduras one file per year, so the same
    download serves the twelve periods of that year; the month filter is
    applied later, while streaming (see pipeline.process_jsonl_records).
    """
    downloads_dir = work_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    year = period[:4]
    target = downloads_dir / f"{config.source}_{year}.jsonl.gz"

    if local_file is not None:
        if local_file.resolve() != target.resolve():
            shutil.copyfile(local_file, target)
        validate_gzip(target)
        return target

    url = config.source_url_for_period(period)

    print(f"Downloading: {url}")

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/150.0.0.0 Safari/537.36"
        ),
        "Accept": "application/gzip,application/octet-stream,*/*",
        "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    }

    try:
        download_with_httpx(
            url=url,
            target=target,
            headers=headers,
            bootstrap_url=config.download.get("bootstrap_url"),
            content_types=GZIP_CONTENT_TYPES,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 403:
            raise
        print("Cloudflare rejected httpx with HTTP 403; retrying with curl.")
        download_with_curl(url=url, target=target, headers=headers)

    validate_gzip(target)

    print(f"Downloaded JSONL.GZ: {target} ({target.stat().st_size} bytes)")

    return target


def validate_gzip(target: Path) -> None:
    try:
        with gzip.open(target, "rb") as fh:
            fh.read(1)
    except (OSError, EOFError) as exc:
        raise ValueError(f"Downloaded file is not a valid GZIP: {target}") from exc


def download_with_httpx(
    *,
    url: str,
    target: Path,
    headers: dict[str, str],
    bootstrap_url: str | None,
    content_types: tuple[str, ...] = ZIP_CONTENT_TYPES,
) -> None:
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(
            connect=30.0,
            read=300.0,
            write=30.0,
            pool=30.0,
        ),
        headers=headers,
    ) as client:
        if bootstrap_url:
            bootstrap_response = client.get(
                bootstrap_url,
                headers={
                    "Accept": (
                        "text/html,application/xhtml+xml"
                    ),
                },
            )
            bootstrap_response.raise_for_status()

        with client.stream(
            "GET",
            url,
            headers={
                "Referer": bootstrap_url,
            } if bootstrap_url else None,
        ) as response:
            response.raise_for_status()
            content_type = response.headers.get(
                "content-type",
                "",
            ).lower()
            print(
                f"HTTP {response.status_code} "
                f"| {content_type}"
            )
            if not any(
                accepted in content_type
                for accepted in content_types
            ):
                raise ValueError(
                    f"Expected {' or '.join(content_types)} "
                    f"from source, but received: {content_type}"
                )
            with target.open("wb") as fh:
                for chunk in response.iter_bytes():
                    fh.write(chunk)


def download_with_curl(
    *,
    url: str,
    target: Path,
    headers: dict[str, str],
) -> None:
    curl = shutil.which("curl")
    if curl is None:
        raise RuntimeError(
            "Cloudflare rejected httpx and curl is not installed."
        )

    command = [
        curl,
        "--silent",
        "--show-error",
        "--fail",
        "--location",
        "--retry",
        "3",
        "--retry-all-errors",
        "--connect-timeout",
        "30",
        "--max-time",
        "300",
    ]
    for name, value in headers.items():
        command.extend(["--header", f"{name}: {value}"])
    command.extend(["--output", str(target), url])
    subprocess.run(command, check=True)


def extract_zip(zip_path: Path, work_dir: Path, source: str, period: str) -> Path:
    run_suffix = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    extract_dir = work_dir / "extracts" / source / period / run_suffix
    extract_dir.mkdir(parents=True, exist_ok=True)
    print(f"Extracting ZIP: {zip_path}")
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(extract_dir)
    print(f"ZIP extracted: {extract_dir}")
    return extract_dir


def resolve_dataset_dir(config: SourceConfig, extract_dir: Path) -> Path:
    """Find the directory that contains all configured required files.

    Some source ZIPs store their files at the archive root while others wrap the
    same dataset in a period directory (for example, ``202401/``).
    """
    required = config.files["required"]
    candidates = [extract_dir]
    candidates.extend(path for path in extract_dir.rglob("*") if path.is_dir())

    for candidate in candidates:
        if all((candidate / name).is_file() for name in required):
            return candidate

    found_names = {
        path.name
        for path in extract_dir.rglob("*")
        if path.is_file()
    }
    missing = [name for name in required if name not in found_names]
    if not missing:
        raise FileNotFoundError(
            "Required files were found, but not in the same directory"
        )
    raise FileNotFoundError(f"Missing required files: {', '.join(missing)}")


def validate_required_files(config: SourceConfig, extract_dir: Path) -> None:
    resolve_dataset_dir(config, extract_dir)
