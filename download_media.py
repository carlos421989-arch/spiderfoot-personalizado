import argparse
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

EXTENSIONS = {
    ".mp3", ".wav", ".flac", ".aac", ".m4a", ".ogg", ".oga",
    ".opus", ".wma", ".aiff", ".aif", ".amr", ".ape", ".alac",
    ".mp4", ".avi", ".mkv", ".mov", ".webm", ".wmv", ".flv",
    ".m4v", ".mpg", ".mpeg", ".3gp", ".3g2", ".m2ts", ".mts",
    ".ts", ".vob", ".rm", ".rmvb", ".asf", ".jpg", ".jpeg",
    ".png", ".gif", ".webp", ".avif", ".bmp", ".tif", ".tiff",
    ".svg", ".heic", ".m3u8", ".mpd"
}

MIME_EXTENSIONS = {
    "application/vnd.apple.mpegurl": ".m3u8",
    "application/x-mpegurl": ".m3u8",
    "application/dash+xml": ".mpd",
    "application/mp4": ".mp4",
    "application/ogg": ".ogg",
    "application/x-matroska": ".mkv",
}

def main():
    parser = argparse.ArgumentParser(
        description="Download one authorized direct media URL."
    )
    parser.add_argument("url", help="Direct HTTP/HTTPS media URL")
    parser.add_argument(
        "--output-dir",
        default=str(Path.home() / "Downloads" / "SpiderFootMedia")
    )
    parser.add_argument(
        "--max-mb", type=int, default=1024,
        help="Maximum download size in MB (default: 1024)"
    )
    parser.add_argument(
        "--tor", action="store_true",
        help="Route the request through Tor at 127.0.0.1:9150"
    )
    args = parser.parse_args()

    parsed = urlsplit(args.url)
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        raise SystemExit("Use uma URL HTTP ou HTTPS valida.")

    if args.max_mb < 1:
        raise SystemExit("--max-mb precisa ser maior que zero.")

    use_tor = args.tor or parsed.hostname.lower().endswith(".onion")
    proxies = None

    if use_tor:
        proxies = {
            "http": "socks5h://127.0.0.1:9150",
            "https": "socks5h://127.0.0.1:9150"
        }

    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    response = None
    temp_path = None

    try:
        response = requests.get(
            args.url,
            stream=True,
            allow_redirects=True,
            timeout=(20, 60),
            headers={"User-Agent": "MediaDownloader/1.0"},
            proxies=proxies
        )
        response.raise_for_status()

        content_type = response.headers.get(
            "Content-Type", ""
        ).split(";")[0].strip().lower()

        final_url = urlsplit(response.url)
        original_name = Path(
            unquote(final_url.path)
        ).name

        name = re.sub(
            r'[<>:"/\\|?*\x00-\x1f]', "_",
            original_name or "media"
        )

        ext = Path(name).suffix.lower()
        if ext not in EXTENSIONS:
            ext = MIME_EXTENSIONS.get(
                content_type,
                mimetypes.guess_extension(content_type) or ""
            )
            if ext not in EXTENSIONS:
                raise RuntimeError(
                    "Nao foi possivel confirmar uma extensao de midia."
                )
            name = Path(name).stem + ext

        is_media = (
            content_type.startswith(("audio/", "video/", "image/"))
            or content_type in MIME_EXTENSIONS
        )
        is_generic = content_type in (
            "", "application/octet-stream", "binary/octet-stream"
        )

        if not is_media and not (is_generic and ext in EXTENSIONS):
            raise RuntimeError(
                "A URL nao retornou um tipo de conteudo de midia. "
                "Pode ser uma pagina HTML, login ou erro."
            )

        limit = args.max_mb * 1024 * 1024
        length = response.headers.get("Content-Length")

        if length and int(length) > limit:
            raise RuntimeError(
                f"Arquivo excede o limite de {args.max_mb} MB."
            )

        destination = output_dir / name
        if destination.exists():
            destination = output_dir / (
                Path(name).stem + "_" +
                time.strftime("%Y%m%d_%H%M%S") +
                Path(name).suffix
            )

        temp_path = destination.with_name(destination.name + ".part")
        total = 0

        with temp_path.open("wb") as output:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue

                total += len(chunk)
                if total > limit:
                    raise RuntimeError(
                        f"Download interrompido: limite de {args.max_mb} MB."
                    )

                output.write(chunk)

        temp_path.replace(destination)
        temp_path = None

        print("Download concluido.")
        print("Tipo:", content_type)
        print("Tamanho:", f"{total / (1024 * 1024):.2f} MB")
        print("Arquivo:", destination)

    except Exception as exc:
        print("ERRO:", type(exc).__name__, str(exc))
        raise SystemExit(1)

    finally:
        if response is not None:
            response.close()
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()

if __name__ == "__main__":
    main()