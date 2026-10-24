#!/usr/bin/env python3
"""Coletor educacional: pesquisas em lote, documentos publicos e arquivos oficiais.

Uso: python coletar_materiais.py --modo personalizadas
Requer: requests, beautifulsoup4, ddgs (somente para modo pesquisas/todos).
"""
from __future__ import annotations

import argparse
import json
import csv
import hashlib
import uuid
import importlib.util
import logging
import socket
import os
import shutil
import random
import re
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse, urlencode, quote_plus, parse_qs

# v32: habilitar store de certificados do Windows quando truststore estiver instalado.
# NUNCA desativar verificacao TLS (verify=False).
try:
    import truststore
    truststore.inject_into_ssl()   # Antes de importar requests/urllib3.
except ImportError:
    pass
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
from document_intelligence import infer_meta, suggested_filename
from encadeamento_v36 import chained_document_queries, matches_document_context, group_for_extension

BASE = Path(__file__).resolve().parent
OUT = BASE / "resultados"
DOC_EXT = {".pdf", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx", ".odt", ".ods", ".zip", ".txt"}
MEDIA_EXT = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".mpg", ".mpeg", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".tif", ".tiff"}
MEDIA_MIME_EXT = {
    "video/mp4": ".mp4", "video/webm": ".webm", "video/x-matroska": ".mkv", "video/quicktime": ".mov",
    "video/x-msvideo": ".avi", "audio/mpeg": ".mp3", "audio/mp4": ".m4a", "audio/aac": ".aac",
    "audio/ogg": ".ogg", "audio/wav": ".wav", "audio/x-wav": ".wav", "audio/flac": ".flac",
    "image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp",
    "image/avif": ".avif", "image/bmp": ".bmp", "image/tiff": ".tiff",
}
MEDIA_PLATFORM_DOMAINS = {"youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "soundcloud.com", "mixcloud.com", "twitch.tv", "tiktok.com", "archive.org", "rumble.com", "odysee.com", "bilibili.com", "rutube.ru"}
MIME_EXT = {
    "application/pdf": ".pdf",
    "application/msword": ".doc",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/vnd.ms-powerpoint": ".ppt",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/zip": ".zip", "text/plain": ".txt",
}
UA = "Mozilla/5.0 (compatible; ColetorMateriaisEstudo/1.0; +https://www.gov.br/inep/)"
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/pdf,application/octet-stream,*/*;q=0.8"}
RELEVANT = re.compile(r"prova|gabarito|caderno|apostila|simulado|quest[oõ]es|material|download|pdf|resolu[cç]|exerc[ií]ci|resumo|mapa mental", re.I)
VIDEO = MEDIA_PLATFORM_DOMAINS - {"archive.org"}

# Backends nativos suportados pela biblioteca ddgs instalada. Fontes HTML são
# opcionais e podem falhar se o provedor mudar o site, exigir JS, limitar tráfego
# ou não permitir automação. Nenhuma fonte contorna login, CAPTCHA ou bloqueios.
ENGINE_CATALOG = {
    "duckduckgo": {"label": "DuckDuckGo", "kind": "ddgs", "backend": "duckduckgo", "default": True},
    "brave": {"label": "Brave Search", "kind": "ddgs", "backend": "brave", "default": True},
    "yandex": {"label": "Yandex (página web)", "kind": "html", "url": "https://yandex.com/search/?text={query}", "default": True},
    "google": {"label": "Google", "kind": "ddgs", "backend": "google", "default": True},
    "bing": {"label": "Bing", "kind": "html", "url": "https://www.bing.com/search?q={query}", "default": True},
    "yahoo": {"label": "Yahoo", "kind": "ddgs", "backend": "yahoo", "default": True},
    "mojeek": {"label": "Mojeek", "kind": "ddgs", "backend": "mojeek", "default": True},
    "wikipedia": {"label": "Wikipedia", "kind": "ddgs", "backend": "wikipedia", "default": True},
    "startpage": {"label": "Startpage", "kind": "ddgs", "backend": "startpage", "default": True},
    "swisscows": {"label": "Swisscows", "kind": "html", "url": "https://swisscows.com/web?query={query}", "default": True},
    "naver": {"label": "Naver", "kind": "html", "url": "https://search.naver.com/search.naver?query={query}", "default": True},
    "gibiru": {"label": "Gibiru", "kind": "html", "url": "https://gibiru.com/results.html?q={query}", "default": True},
    "yandex_web": {"label": "Yandex (página web duplicada; use yandex)", "kind": "inactive", "note": "Alias duplicado removido da interface; use o mecanismo yandex.", "default": False},
    "ecosia": {"label": "Ecosia", "kind": "html", "url": "https://www.ecosia.org/search?q={query}", "default": True},
    "youtube": {"label": "YouTube (pesquisa; download opcional autorizado)", "kind": "youtube", "url": "https://www.youtube.com/results?search_query={query}", "default": True},
    "telegram_public": {"label": "Telegram (páginas públicas e resultados indexados)", "kind": "telegram", "default": True},
    "boardreader": {"label": "Boardreader (fóruns)", "kind": "html", "url": "https://boardreader.com/s/{query}", "default": True},
    "archive_scholar": {"label": "Internet Archive Scholar", "kind": "html", "url": "https://scholar.archive.org/search?query={query}", "default": True},
    "aol": {"label": "AOL Search", "kind": "html", "url": "https://search.aol.com/aol/search?q={query}", "default": True},
    "searchencrypt": {"label": "Search Encrypt (disponibilidade a confirmar)", "kind": "html", "url": "https://www.searchencrypt.com/search/?q={query}", "default": True},
    "ask": {"label": "Ask.com", "kind": "html", "url": "https://www.ask.com/web?q={query}", "default": True},
    "wiki_com": {"label": "Wiki.com", "kind": "html", "url": "https://www.wiki.com/search?q={query}", "default": True},
    "gigablast": {"label": "Gigablast (disponibilidade a confirmar)", "kind": "html", "url": "https://www.gigablast.com/search?q={query}", "default": True},
    "blackle": {"label": "Blackle", "kind": "html", "url": "https://www.blackle.com/results?query={query}", "default": True},
    "baidu": {"label": "Baidu", "kind": "html", "url": "https://www.baidu.com/s?wd={query}", "default": True},
    "openverse": {"label": "Openverse (substituto do antigo Creative Commons Search; mídia licenciada)", "kind": "openverse", "url": "https://api.openverse.org/v1/images/?q={query}", "default": True},
    "searxng": {"label": "SearXNG (requer URL de uma instância de busca; docs.searxng.org é documentação)", "kind": "searxng", "default": False},
    # Enderecos .onion fornecidos pelo usuario. A identidade/disponibilidade destes servicos nao foi verificada.
    "onion_2fd6": {"label": "Busca .onion - 2fd6cemt4g (Tor; endereco nao verificado)", "kind": "onion", "url": "http://2fd6cemt4gmccflhm6imvdfvli3nf7zn6rfrwpsy7uhxrgbypvwf5fad.onion/", "default": True},
    "onion_tornetup": {"label": "Busca .onion - tornetup (Tor; endereco nao verificado)", "kind": "onion", "url": "http://tornetupfu7gcgidt33ftnungxzyfq2pygui5qdoyss34xbgx2qruzid.onion/", "default": True},
    "onion_goscan24": {"label": "Busca .onion - goscan24 (Tor; endereco nao verificado)", "kind": "onion", "url": "http://goscan24olh2vjmcwqjujgcpyrijjg6rottqzwtogj3f67djgh6c5lid.onion/", "default": True},
    "onion_startpage": {"label": "Busca .onion - startpage (Tor; endereco nao verificado)", "kind": "onion", "url": "http://startpagel6srwcjlue4zgq3zevrujfaow726kjytqbbjyrswwmjzcqd.onion/", "default": True},
    "onion_torsearch": {"label": "Busca .onion - torsearc (Tor; endereco nao verificado)", "kind": "onion", "url": "http://torsearc525abz6aqsqqfteyjqe2x7xrujg55atmu56vk47wixgb44ad.onion/", "default": True},

    # Portais educacionais: busca restrita ao dominio atraves do DDGS; nao sao provedores gerais de busca.
    "fonte_curso_enem_gratuito": {"label": "Curso Enem Gratuito (site)", "kind": "site_source", "site": "cursoenemgratuito.com.br", "index": "https://cursoenemgratuito.com.br/download-das-provas-do-enem/", "default": True},
    "fonte_brasil_escola_vestibular": {"label": "Brasil Escola - Vestibular (site)", "kind": "site_source", "site": "vestibular.brasilescola.uol.com.br", "index": "https://vestibular.brasilescola.uol.com.br/enem/provas-gabaritos-enem.htm", "default": True},
    "fonte_guia_estudante": {"label": "Guia do Estudante - Abril (site)", "kind": "site_source", "site": "guiadoestudante.abril.com.br", "index": "https://guiadoestudante.abril.com.br/", "default": True},
    "fonte_inep_provas": {"label": "INEP - Provas e Gabaritos (oficial)", "kind": "site_source", "site": "gov.br/inep", "allowed_host": "www.gov.br", "allowed_path": "/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enem/", "index": "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enem/provas-e-gabaritos", "official": True, "default": True},
    "searchcode": {"label": "Searchcode (código em repositórios; não é busca web geral)", "kind": "special", "note": "Busca de código requer contexto/repositório público; não é executada como pesquisa web geral.", "default": False},
    "neeva": {"label": "Neeva (descontinuado em 2023)", "kind": "inactive", "note": "Serviço encerrado; mantido apenas como referência histórica.", "default": False},
    "creativecommons": {"label": "Creative Commons Search antigo (substituído por Openverse)", "kind": "inactive", "note": "O antigo portal foi substituído; selecione Openverse para procurar mídia licenciada.", "default": False},
}
DEFAULT_ENGINES = [k for k, v in ENGINE_CATALOG.items() if v.get("default")]
OFFICIAL_ENEM = "https://www.gov.br/inep/pt-br/areas-de-atuacao/avaliacao-e-exames-educacionais/enem/provas-e-gabaritos/{ano}"
OFFICIAL_FATEC = "https://vestibular.fatec.sp.gov.br/provas-gabaritos/detalhe.asp?q={ano}"


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normurl(url: str) -> str:
    # Remove somente fragmentos; mantem querystrings (alguns downloads dependem delas).
    return url.split("#", 1)[0].strip()


def domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def is_video(url: str) -> bool:
    d = domain(url)
    return any(d == suffix or d.endswith("." + suffix) for suffix in VIDEO)


def direct_extension(url: str) -> str:
    try:
        return Path(unquote(urlparse(url).path)).suffix.lower()
    except ValueError:
        return ""


def valid_web(url: str) -> bool:
    try:
        return urlparse(url).scheme in ("http", "https") and bool(domain(url))
    except ValueError:
        return False


def youtube_format_selector(ffmpeg_available=None) -> str:
    """Choose a format selector that does not require MP4-only single-file streams.

    yt-dlp can merge best video/audio streams when FFmpeg is installed; without
    FFmpeg, choose the best single-file format so a merge is not required.
    """
    if ffmpeg_available is None:
        ffmpeg_available = shutil.which("ffmpeg") is not None
    return "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/bv*+ba/best" if ffmpeg_available else "best[ext=mp4]/best"


def ensure_mp4_video(path: Path) -> Path:
    """Safely remux or transcode a completed video. Never destroys source on failure.

    Does not modify running `.part` downloads, and refuses overwriting an existing
    MP4. Requires FFmpeg on PATH; without FFmpeg original stays intact and is logged.
    """
    if path.suffix.lower() == ".mp4" or path.suffix.lower() not in VIDEO_FILES:
        return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        logging.warning("MP4: FFmpeg indisponivel. Mantendo o original %s", path.name)
        return path
    destination = path.with_suffix(".mp4")
    if destination.exists():
        logging.warning("MP4: destino ja existe, original preservado: %s", destination)
        return path
    import subprocess
    temp = path.with_name(path.stem + "_convertendo_" + uuid.uuid4().hex[:10] + ".mp4")
    options = [
        ["-map", "0:v:0", "-map", "0:a?", "-c", "copy", "-movflags", "+faststart"],
        ["-map", "0:v:0", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
         "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
         "-movflags", "+faststart"],
    ]
    try:
        for spec in options:
            temp.unlink(missing_ok=True)
            cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-n", "-i", str(path), *spec, str(temp)]
            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)
            except (OSError, subprocess.TimeoutExpired) as ex:
                logging.warning("MP4: falha ao converter %s: %s", path.name, ex)
                break
            if result.returncode == 0 and temp.exists() and temp.stat().st_size > 0:
                # rename only if target is still absent. Never delete source first.
                if destination.exists():
                    logging.warning("MP4: destino foi criado durante conversao: %s", destination)
                    break
                temp.rename(destination)
                path.unlink()
                logging.info("MP4: video convertido %s -> %s", path.name, destination.name)
                return destination
            logging.warning("MP4: tentativa nao concluida: %s | %s", path.name, (result.stderr or "")[-250:])
    finally:
        temp.unlink(missing_ok=True)
    return path


def safe_filename(name: str, maxlen=130) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = "".join(x for x in name if not unicodedata.combining(x))
    name = re.sub(r"[^\w. -]+", "_", name).strip(" ._-")
    return (name[:maxlen] or "arquivo")


def category(query: str, url: str = "") -> tuple[str, str]:
    """Prioritize document URL year; do not assume query year is document year."""
    text = query + " " + url
    label = "ENEM" if re.search(r"enem|inep", text, re.I) else "FATEC" if re.search(r"fatec|paula.souza", text, re.I) else "OUTROS"
    year = document_year_from_name(url)
    if not year:
        m = re.search(r"(?<!\d)(?:19\d\d|20\d\d)(?!\d)", query)
        year = m.group(0) if m else "sem_ano"
    return label, year


def document_year_from_name(value: str) -> str | None:
    """Extract a year only from recognizable ENEM/FATEC document naming."""
    name = unquote(Path(urlparse(value).path).name) if "://" in value else Path(value).name
    match = re.search(r"(?i)(?:enem|fatec)[_\s-]*(?:prova|gabarito|dia\d+)?[_\s-]*((?:19|20)\d{2})(?!\d)", name)
    if not match:
        match = re.search(r"(?i)(?:enem|fatec)[_\s-]*((?:19|20)\d{2})(?!\d)", name)
    return match.group(1) if match else None


def reconcile_document_year(meta: dict, filename: str) -> dict:
    """Quarantine conflicting nominal years; never silently file in a wrong year."""
    nominal_year = document_year_from_name(filename)
    current_year = str(meta.get("year", "sem_ano"))
    if nominal_year and current_year not in ("sem_ano", "revisar_ano", nominal_year):
        logging.warning("CONFLITO_ANO: classificador=%s, nome=%s, arquivo=%s; encaminhado para revisao",
                        current_year, nominal_year, filename)
        updated = dict(meta)
        updated["year"] = "revisar_ano"
        updated["colors"] = []
        updated["shared"] = False
        return updated
    if nominal_year and current_year == "sem_ano":
        updated = dict(meta)
        updated["year"] = nominal_year
        return updated
    return meta


COLOR_LABELS = {
    "azul": "Azul", "amarela": "Amarela", "amarelo": "Amarela",
    "branca": "Branca", "branco": "Branca", "rosa": "Rosa",
    "verde": "Verde", "laranja": "Laranja", "cinza": "Cinza", "roxo": "Roxo", "roxa": "Roxo",
}
ALL_ENEM_COLORS = ["Azul", "Amarela", "Branca", "Rosa", "Cinza", "Verde", "Laranja", "Roxo"]

def classify_document(query: str, title: str, url: str) -> dict:
    """Classify a result using its own title/URL first, then the search query."""
    result_text = f"{title} {url}".lower()
    text = f"{query} {result_text}".lower()
    exam, year = category(query, url)
    title_year = document_year_from_name(title)
    url_year = document_year_from_name(url)
    if title_year and url_year and title_year != url_year:
        year = "revisar_ano"
    else:
        year = title_year or url_year or year
    # Prefer the result's title/filename over a broad query that may ask for
    # both a proof and an answer key. This prevents a proof being filed as key.
    material = "Outros"
    result_has_gabarito = bool(re.search(r"gabarito|answer\s*key|respostas?\s+corretas", result_text, re.I))
    result_has_prova = bool(re.search(r"prova|caderno\s+de\s+quest|booklet|exam\s+paper", result_text, re.I))
    if result_has_gabarito:
        material = "Gabaritos"
    elif result_has_prova:
        material = "Provas"
    elif re.search(r"gabarito|answer\s*key|respostas?\s+corretas", query, re.I) and not re.search(r"prova|caderno\s+de\s+quest", query, re.I):
        material = "Gabaritos"
    elif re.search(r"prova|caderno\s+de\s+quest|booklet|exam\s+paper", query, re.I) and not re.search(r"gabarito|answer\s*key", query, re.I):
        material = "Provas"
    elif re.search(r"resolu[cç][aã]o|quest[oõ]es?\s+resolvidas|comentad[ao]s?", result_text, re.I) or re.search(r"resolu[cç][aã]o|quest[oõ]es?\s+resolvidas|comentad[ao]s?", query, re.I):
        material = "Resolucoes"
    elif re.search(r"apostila|material\s+did[aá]tico", result_text, re.I) or re.search(r"apostila|material\s+did[aá]tico", query, re.I):
        material = "Apostilas"
    elif re.search(r"simulado", result_text, re.I) or re.search(r"simulado", query, re.I):
        material = "Simulados"
    elif is_video(url) or re.search(r"videoaula|v[ií]deo\s*aula|aula\s+online", text, re.I):
        material = "Videoaulas"
    elif re.search(r"curso|playlist|aulas\s+completas", text, re.I):
        material = "Cursos"
    elif re.search(r"github|gitlab|c[oó]digo|reposit[oó]rio", text, re.I):
        material = "Codigo_e_repositorios"
    elif re.search(r"artigo|paper|research|estudo\s+cient[ií]fico|scholar", text, re.I):
        material = "Artigos_e_pesquisas"

    def colors_in(value):
        found=[]
        for token,label in COLOR_LABELS.items():
            if re.search(rf"(?<![a-z]){re.escape(token)}(?![a-z])", value, re.I) and label not in found:
                found.append(label)
        return found
    result_colors = colors_in(result_text)
    colors = result_colors or colors_in(query)
    all_colors = material == "Gabaritos" and bool(re.search(
        r"todas\s+as\s+cores|todos\s+os\s+cadernos|todas\s+as\s+provas|gabarito(s)?\s+geral|azul.{0,60}amarel[oa].{0,80}branc[oa].{0,80}rosa",
        text, re.I))
    shared = material == "Gabaritos" and (all_colors or len(colors) > 1)
    if all_colors and exam == "ENEM":
        colors = list(ALL_ENEM_COLORS)
    subject = "Geral"
    for label,pat in [("Matematica",r"matem[aá]tica|geometria|estat[ií]stica"),("Portugues",r"portugu[eê]s|gram[aá]tica|literatura"),("Fisica",r"f[ií]sica|mec[aâ]nica|eletricidade"),("Quimica",r"qu[ií]mica|estequiometria"),("Biologia",r"biologia|ecologia|gen[eé]tica"),("Historia",r"hist[oó]ria"),("Geografia",r"geografia|geopol[ií]tica"),("Ingles",r"ingl[eê]s|english"),("Informatica",r"inform[aá]tica|programa[cç][aã]o|tecnologia")]:
        if re.search(pat,text,re.I): subject=label; break
    return {"exam":exam,"year":year,"material":material,"colors":colors,"shared":shared,"subject":subject,"query":query,"title":title,"url":url}

def organized_path(meta: dict, filename: str) -> Path:
    base=OUT / "materiais"; exam=meta["exam"]; year=meta["year"]; kind=meta["material"]
    if exam == "ENEM":
        folder=base / "ENEM" / year / kind
        if kind == "Provas": folder=folder / ("Caderno_"+meta["colors"][0] if meta["colors"] else "Cor_nao_identificada")
        elif kind == "Gabaritos": folder=folder / ("Compartilhado_todas_as_cores" if meta["shared"] and len(meta["colors"])>1 else ("Caderno_"+meta["colors"][0] if meta["colors"] else "Cor_nao_identificada"))
    elif exam == "FATEC": folder=base / "FATEC" / year / kind
    else: folder=base / "OUTROS" / meta["subject"] / year / kind
    folder.mkdir(parents=True,exist_ok=True)
    return folder / filename

def link_shared_gabarito(canonical: Path, meta: dict):
    """Avoid duplicate or misassociated files: preserve only canonical gabaritos.

    Associations should be recorded only after document-level validation of
    every color; query terms and incidental PDF text are insufficient proof.
    """
    return


def catalog_document(path: Path, meta: dict, digest: str, size: int):
    append_csv(OUT/"catalogo_documentos.csv",["data","exame_categoria","ano","tipo_material","cores_identificadas","compartilhado","assunto","pesquisa","titulo","url","arquivo_canonico","bytes","sha256"],[timestamp(),meta["exam"],meta["year"],meta["material"],";".join(meta["colors"]),str(meta["shared"]).lower(),meta["subject"],meta["query"],meta["title"][:250],meta["url"],str(path.relative_to(OUT)),str(size),digest])


# V12: a classificacao da capa tem prioridade para nome e pasta do download.
# Mantem o hash SHA-256 no nome, atualiza o caminho antes de registrar o CSV,
# e nunca sobrescreve um arquivo diferente.
def finalize_download_from_content(path: Path, meta: dict, query: str, title: str, url: str, digest: str):
    try:
        from analisar_conteudo import inspect as inspect_document
        from organizar_resultados import friendly_name, sha256_file
        info = inspect_document(path, {"titulo": title, "url": url, "pesquisa": query}, ocr=False)
        if info.exam not in ("ENEM", "FATEC") or not info.year or info.kind == "08_Outros":
            return path, meta
        if any(flag in (info.reason or "") for flag in ("CONFLITO_", "CORES_AMBIGUAS", "MULTIPLOS_DIAS_IDENTIFICADOS", "MULTIPLAS_VERSOES")):
            logging.warning("Nomeacao automatica requer revisao; mantendo nome atual: %s | %s", path.name, info.reason)
            return path, meta
        kind_map = {
            "01_Provas": "Provas", "02_Gabaritos": "Gabaritos",
            "03_Resolucoes_Comentadas": "Resolucoes", "04_Apostilas_Resumos": "Apostilas",
            "05_Questoes_Simulados": "Simulados", "06_Videoaulas_Cursos": "Videoaulas",
            "07_Editais_Matrizes": "Editais", "08_Outros": "Outros",
        }
        updated = dict(meta)
        updated["exam"] = info.exam
        updated["year"] = info.year
        updated["material"] = kind_map.get(info.kind, meta.get("material", "Outros"))
        color_map = {"Amarelo": "Amarela", "Branco": "Branca"}
        updated["colors"] = [color_map.get(c, c) for c in (info.colors or [])]
        updated["shared"] = bool(info.kind == "02_Gabaritos" and len(info.colors) > 1 and
                                 info.multiple_reason in ("multiplas_cores_comprovadas_no_texto", "correspondencia_manual_documentada"))
        updated["subject"] = info.discipline or updated.get("subject", "Geral")
        final_name = friendly_name(info, path, digest)
        # Preserve the real document suffix even when the organizer returns a
        # temporary or nonstandard extension.
        correct_ext = path.suffix.lower()
        final_name = Path(str(final_name)).name
        if final_name.lower().endswith(".part"):
            final_name = final_name[:-5]
        if Path(final_name).suffix.lower() != correct_ext:
            final_name = Path(final_name).stem + correct_ext
        updated = reconcile_document_year(updated, final_name)
        target = organized_path(updated, final_name)
        if target.exists():
            # Nao sobrescreve nem mesmo em caso de colisao de nome.
            if sha256_file(target) != digest:
                target = target.with_name(target.stem + "_" + digest[10:18] + target.suffix)
            if target.exists():
                target = target.with_name(target.stem + "_copia" + target.suffix)
                index = 2
                while target.exists():
                    target = target.with_name(target.stem.rsplit("_copia", 1)[0] + f"_copia{index}" + target.suffix)
                    index += 1
        if path.resolve() != target.resolve():
            target.parent.mkdir(parents=True, exist_ok=True)
            path.replace(target)
            logging.info("NOME CORRIGIDO PELO CONTEUDO | %s -> %s | evidencias=%s", path.name, target.name, info.evidence)
        return target, updated
    except (ImportError, OSError, ValueError, RuntimeError, KeyError) as exc:
        logging.warning("Nao foi possivel corrigir o nome pelo conteudo; mantendo nome atual %s: %s", path.name, exc)
        return path, meta


def load_queries(path: Path) -> list[str]:
    unique = set()
    queries = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        q = line.strip().lstrip("#").strip()
        if q and q.casefold() not in unique:
            unique.add(q.casefold())
            queries.append(q)
    return queries


def build_query_list(mode: str, standard_queries: list[str], general_queries: list[str], custom_queries: list[str]) -> list[str]:
    """Compose search modes while preserving user-query priority and deduplicating."""
    if mode == "personalizadas":
        sources = custom_queries
    elif mode == "geral":
        sources = custom_queries + general_queries
    elif mode == "pesquisas":
        sources = custom_queries + standard_queries
    elif mode == "todos":
        sources = custom_queries + standard_queries + general_queries
    else:
        return []
    output, seen = [], set()
    for raw in sources:
        query = str(raw).strip()
        key = query.casefold()
        if query and key not in seen:
            seen.add(key)
            output.append(query)
    return output


def append_csv(path: Path, header: list[str], row: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if f.tell() == 0:
            w.writerow(header)
        w.writerow(row)


def validate_download(path: Path, ext: str, size: int, expected: int, content_encoding: str = ""):
    """Reject truncation and malformed PDFs BEFORE any CSV success record.

    HTTP Content-Length describes the encoded body; do not compare byte counts
    when requests transparently decompressed the transfer.
    """
    if size <= 0:
        raise ValueError("Arquivo vazio")
    if expected > 0 and not content_encoding and size != expected:
        raise ValueError(f"Download truncado: {size} recebidos, {expected} esperados")
    if ext != ".pdf":
        return
    with path.open("rb") as fp:
        head = fp.read(1024)
        fp.seek(max(0, size - 131072))
        tail = fp.read()
    if b"%PDF-" not in head[:1024]:
        raise ValueError("Resposta nao contem cabecalho %PDF-")
    if b"%%EOF" not in tail:
        raise ValueError("PDF sem marcador de termino %%EOF (possivel truncamento)")
    # The parser validates xref/page tree. A header and EOF alone are not enough.
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise RuntimeError("Instale pypdf no ambiente do coletor: python -m pip install pypdf") from exc
    try:
        with path.open("rb") as fp:
            reader = PdfReader(fp, strict=False)
            if reader.is_encrypted:
                raise ValueError("PDF criptografado; verificacao de paginas indisponivel")
            if len(reader.pages) == 0:
                raise ValueError("PDF sem paginas")
            for page in reader.pages:
                _ = page.mediabox
    except Exception as exc:
        raise ValueError(f"PDF estruturalmente invalido: {type(exc).__name__}: {exc}") from exc


def completed_file_from_history(path: Path, expected_size: str = "") -> bool:
    """Do not trust a prior 'success' pointing to a .part or a missing file."""
    if not path.is_file() or path.name.lower().endswith((".part", ".ytdl")):
        return False
    try:
        size = path.stat().st_size
        if size == 0 or (expected_size and expected_size.isdigit() and size != int(expected_size)):
            return False
        if path.suffix.lower() == ".pdf":
            with path.open("rb") as fp:
                first = fp.read(1024)
                fp.seek(max(0, size - 131072))
                end = fp.read()
            if b"%PDF-" not in first or b"%%EOF" not in end:
                return False
        return True
    except OSError:
        return False



# Busca encadeada de videoaulas: os títulos geram consultas extras, não
# consultas gerais pré-definidas. Não contorna restrições de direitos/acesso.
VIDEO_SERIES = re.compile(
    r"(?i)\b(?P<kind>aul[aã]o|aulas?|videoaulas?|epis[oó]dio|ep\.?|parte|cap[ií]tulo|m[oó]dulo|li[cç][aã]o)\s*(?:n[.º°]?\s*)?(?P<num>\d{1,3})(?!\d)"
)
VIDEO_FILES = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".mpg", ".mpeg"}

# Historico independe de resultados/: o Telegram pode remover os MP4 locais.
VIDEO_HISTORY = BASE / "historico_videos_permanente.csv"
VIDEO_HISTORY_HEADER = ["data", "chave", "url", "sha256", "arquivo", "titulo", "origem"]
GB_BYTES = 1_000_000_000  # GB decimal, nao GiB.
# V38: comandos apenas para a execução ativa; nunca apaga arquivos ou o histórico.
RESET_REQUESTS_DIR = OUT / "_controle_limites_v38" / "pendentes"
RESET_AUDIT_CSV = BASE / "historico_redefinicoes_limites.csv"
RESET_AUDIT_HEADER = ["data", "execucao", "pedido", "tipo", "ciclo_novo", "limite_gb"]


class DownloadBudgetExceeded(RuntimeError):
    """Interrompe um download sem contabilizar arquivo incompleto."""


def video_identity(url: str) -> str:
    """ID persistente de video (YouTube/algumas plataformas) ou URL completa."""
    try:
        parsed = urlparse(url.strip())
        host = (parsed.hostname or "").lower().removeprefix("www.").removeprefix("m.")
        path = parsed.path.strip("/")
        if host in ("youtube.com", "youtube-nocookie.com", "youtu.be"):
            video_id = path if host == "youtu.be" else (
                (parse_qs(parsed.query).get("v") or [""])[0]
                if path == "watch" else path.split("/", 1)[1] if path.startswith(("shorts/", "live/", "embed/")) else ""
            )
            if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
                return "youtube:" + video_id
        if host.endswith("vimeo.com") and re.fullmatch(r"\d{5,20}", path.split("/")[-1]):
            return "vimeo:" + path.split("/")[-1]
        if host.endswith("dailymotion.com") and path.startswith("video/"):
            return "dailymotion:" + path.split("/")[1].split("_")[0]
        if parsed.scheme in ("https", "http") and host:
            return "url:" + host + "/" + path + ("?" + parsed.query if parsed.query else "")
    except (TypeError, ValueError, IndexError):
        pass
    return "url:" + str(url).strip()



def followup_queries(title: str, cap: int = 4) -> list[tuple[str, int | None, str]]:
    """Generate a bounded list of following episodes with aula/aulão synonyms."""
    clean = re.sub(r"\s+", " ", title).strip()
    match = VIDEO_SERIES.search(clean)
    if match:
        kind = match.group("kind")
        number = int(match.group("num"))
        if number >= 999:
            return []
        prefix = clean[:match.start()].strip(" -_:#|" )
        # Ex.: 'ENEM aulão 1' -> 'ENEM aulão 2' e 'ENEM aula 2'.
        prefix = re.sub(r"\b(v[ií]deo|video)\s*$", "", prefix, flags=re.I).strip()
        prefix = prefix[:95]
        variants = [kind]
        if re.fullmatch(r"aul[aã]o|aulas?", kind, flags=re.I):
            variants += ["aula" if re.search(r"aul[aã]o", kind, re.I) else "aulão"]
        output = []
        for next_number in (number + 1, number + 2):
            for variant in variants:
                q = f"{prefix} {variant} {next_number}".strip()
                if q and q.casefold() not in {x[0].casefold() for x in output}:
                    output.append((q, next_number, prefix))
                if len(output) >= cap:
                    return output
        return output
    # Sem número: procurar conexões sem criar uma árvore infinita de títulos.
    short = clean[:100].strip(" -_:|")
    if len(short) >= 8:
        return [(f"{short} aula 2", 2, short), (f"{short} parte 2", 2, short)][:cap]
    return []


def follows_video_title(candidate: str, context: dict) -> bool:
    """Ignore unrelated videos returned by broad search engines."""
    candidate_norm = unicodedata.normalize("NFKD", candidate).encode("ascii", "ignore").decode("ascii").casefold()
    base_norm = unicodedata.normalize("NFKD", context.get("base", "")).encode("ascii", "ignore").decode("ascii").casefold()
    tokens = [t for t in re.findall(r"[a-z0-9]+", base_norm) if len(t) > 2 and t not in {"video", "aula", "aulao", "parte", "curso"}]
    if tokens and not all(t in candidate_norm for t in tokens[:3]):
        return False
    target = context.get("number")
    if target is None:
        return True
    matches = VIDEO_SERIES.search(candidate)
    return bool(matches and int(matches.group("num")) == int(target))

class Collector:
    def __init__(self, args):
        self.args = args
        self.telegram_channels = []
        channel_file = getattr(args, "telegram_canais_arquivo", "")
        if channel_file:
            try:
                for raw in Path(channel_file).expanduser().read_text(encoding="utf-8-sig").splitlines():
                    value = raw.strip().lstrip("@")
                    value = re.sub(r"^https?://(?:www\.)?t\.me/(?:s/)?", "", value, flags=re.I).strip("/ ")
                    value = value.split("/", 1)[0]
                    if re.fullmatch(r"[A-Za-z0-9_]{5,32}", value) and value not in self.telegram_channels:
                        self.telegram_channels.append(value)
                    if len(self.telegram_channels) >= 100:
                        break
            except OSError as ex:
                logging.warning("Não foi possível ler a lista de canais públicos do Telegram: %s", ex)
        self.s = requests.Session()
        self.s.headers.update(HEADERS)
        # v32: repeticao limitada para conexao e erros 5xx. Nao insiste em 403/429.
        retry_policy = Retry(
            total=3, connect=2, read=0, status=2, other=0,
            backoff_factor=1.0,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset(("GET", "HEAD")),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        self.s.mount("https://", HTTPAdapter(max_retries=retry_policy))
        self.s.mount("http://", HTTPAdapter(max_retries=retry_policy))
        self._tor_proxy_checked = False
        self._tor_proxy_available = False
        self._tor_proxy_message = ""
        self._onion_form_cache = {}
        self.links_csv = OUT / "links_encontrados.csv"
        self.files_csv = OUT / "arquivos_baixados.csv"
        self.errors_csv = OUT / "erros.csv"
        self.done_queries_path = OUT / "pesquisas_concluidas.txt"
        self.done_engine_path = OUT / "pesquisas_mecanismos_concluidas.csv"
        self.done_official_path = OUT / "fontes_oficiais_concluidas.txt"
        self.done_queries = set(self.done_queries_path.read_text(encoding="utf-8").splitlines()) if self.done_queries_path.exists() else set()
        marker_pairs = set()
        if self.done_engine_path.exists():
            try:
                with self.done_engine_path.open(encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        if row.get("pesquisa") and row.get("mecanismo"):
                            marker_pairs.add((row["pesquisa"], row["mecanismo"]))
            except Exception as ex:
                logging.warning("Não foi possível ler o marcador por mecanismo: %s", ex)
        # Older versions marked a source as done even after 403/404/timeouts.
        # Trust a marker only when the latest recorded outcome was a real query
        # completion (results found or a valid empty response); retry errors and
        # HTML layouts that could not be parsed on the next run.
        latest_engine_status = {}
        engine_log = OUT / "mecanismos_busca.csv"
        if engine_log.exists():
            try:
                with engine_log.open(encoding="utf-8-sig", newline="") as f:
                    for row in csv.DictReader(f):
                        query = row.get("pesquisa", "")
                        label = row.get("mecanismo", "")
                        if query and label:
                            latest_engine_status[(query, label)] = row.get("status", "")
            except Exception as ex:
                logging.warning("Não foi possível reconciliar o histórico dos mecanismos: %s", ex)
        self.done_engine_pairs = set()
        for query, engine_id in marker_pairs:
            engine = ENGINE_CATALOG.get(engine_id, {})
            label = engine.get("label", engine_id)
            if latest_engine_status.get((query, label)) in ("ok", "sem_resultados"):
                self.done_engine_pairs.add((query, engine_id))
        self.engine_last_status = {}
        self.done_official = set(self.done_official_path.read_text(encoding="utf-8").splitlines()) if self.done_official_path.exists() else set()
        self.seen_urls = set()
        self.seen_media_urls = set()
        self.media_hashes = set()
        self.url_to_file = {}
        self.hashes = set()
        self.hash_to_file = {}
        self.pages_scanned = set()
        self.downloaded = 0
        # V35: only VIDEO bytes consume the GB ceiling. PDFs, ZIPs,
        # audio and images continue to be downloaded under their own limits.
        self.bytes_downloaded = 0
        self.budget_bytes = int(float(getattr(args, "limite_total_gb", 0) or 0) * GB_BYTES)
        self._budget_hit = False
        self.run_id = os.environ.get("SF_COLETOR_RUN_ID") or uuid.uuid4().hex
        self.video_cycle = 1
        self.type_cycles = {}
        self._applied_reset_ids = set()
        self.video_history_keys = set()
        self.video_history_hashes = set()
        self.video_history_files = {}
        self._load_video_history()
        self._budget_status()
        # V36: limites independentes por formato, sem parar as demais extensoes.
        try:
            limits = json.loads(getattr(args, "limites_por_tipo_gb", "{}") or "{}")
        except (TypeError, ValueError) as exc:
            raise ValueError("limites_por_tipo_gb deve ser um objeto JSON valido") from exc
        if not isinstance(limits, dict):
            raise ValueError("limites_por_tipo_gb exige um objeto JSON")
        self.type_limits = {}
        for key, value in limits.items():
            key = str(key).strip().lower()
            if not (key.startswith(".") or key in ("video", "audio", "imagem", "outros")):
                raise ValueError("Tipo de arquivo desconhecido: " + key)
            number = float(value)
            if not 0 <= number <= 10000:
                raise ValueError("Limite fora de faixa para " + key)
            if number > 0:
                self.type_limits[key] = int(number * GB_BYTES)
        self.type_bytes = {}
        self.type_limited = set()
        self._type_budget_status()
        self._doc_followup_running = False
        self._doc_followup_seen = set()
        self._doc_followup_used = 0
        self._doc_followup_context = None
        self._current_engine_id = None
        self._followup_queue = []
        self._followup_seen = set()
        self._followup_running = False
        self._followup_force = False
        self._followup_context = None
        self._followup_used = 0
        self.known_links = set()
        self._load_saved()

    def _prepare_video_for_telegram(self, path: Path):
        """Gera copia(s) apenas apos o download/historico originais serem salvos.

        Resultados derivados nunca sao contabilizados como novos downloads.
        Falha de FFmpeg nao desfaz o download nem remove o video original.
        """
        mode = getattr(self.args, "telegram_video_modo", "desativado")
        if mode not in ("compactar", "dividir"):
            return
        try:
            from video_telegram_v39 import process_video
            result = process_video(path, mode,
                getattr(self.args, "telegram_video_limite_gb", 4.0),
                getattr(self.args, "telegram_video_saida_gb", 3.5))
            if result.get("status") != "abaixo_limite":
                logging.info("TELEGRAM V39 | modo=%s | fonte=%s | saidas=%s | resultado=%s",
                    mode, path.name, result.get("saida"), result.get("status"))
        except Exception as ex:
            logging.exception("TELEGRAM V39: erro ao preparar copia, ORIGINAL PRESERVADO: %s", path)
            append_csv(OUT / "videos_telegram_falhas_v39.csv",
                ["data", "arquivo", "modo", "erro"],
                [timestamp(), str(path), mode, str(ex)[:1000]])

    def _load_video_history(self):
        """Unifica o historico permanente com CSVs antigos, mesmo sem arquivos locais."""
        if VIDEO_HISTORY.is_file():
            with VIDEO_HISTORY.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    key = row.get("chave") or video_identity(row.get("url", ""))
                    if key:
                        self.video_history_keys.add(key)
                        if row.get("arquivo"):
                            self.video_history_files[key] = row["arquivo"]
                    if row.get("sha256"):
                        self.video_history_hashes.add(row["sha256"])

        for filename in ("midias_baixadas.csv", "videos_youtube_baixados.csv"):
            old = OUT / filename
            if not old.is_file():
                continue
            with old.open(encoding="utf-8-sig", newline="") as handle:
                for row in csv.DictReader(handle):
                    if Path(row.get("arquivo", "")).suffix.lower() not in VIDEO_FILES:
                        continue
                    self._record_video_history(row.get("url", ""), row.get("sha256", ""),
                                               row.get("arquivo", ""), row.get("titulo", ""),
                                               "historico_legado:" + filename)
        archive = OUT / "midias_download_archive.txt"
        if archive.is_file():
            for line in archive.read_text(encoding="utf-8-sig", errors="replace").splitlines():
                chunks = line.strip().split(None, 1)
                if len(chunks) == 2 and chunks[0] in ("youtube", "vimeo", "dailymotion"):
                    url = ("https://www.youtube.com/watch?v=" + chunks[1]
                           if chunks[0] == "youtube" else
                           "https://vimeo.com/" + chunks[1] if chunks[0] == "vimeo" else
                           "https://www.dailymotion.com/video/" + chunks[1])
                    self._record_video_history(url, "", "", "", "archive_legado")
        logging.info("HISTORICO DE VIDEOS: %d IDs/URLs persistentes; %d hashes",
                     len(self.video_history_keys), len(self.video_history_hashes))

    def _record_video_history(self, url: str, sha: str, relative: str, title: str, source="v34"):
        key = video_identity(url)
        if not key or key == "url:":
            return
        if key in self.video_history_keys and (source.startswith("historico_legado") or source == "archive_legado"):
            if sha:
                self.video_history_hashes.add(sha)
            if relative and not self.video_history_files.get(key):
                self.video_history_files[key] = relative
            return
        append_csv(VIDEO_HISTORY, VIDEO_HISTORY_HEADER,
                   [timestamp(), key, url, sha, relative, title[:250], source])
        self.video_history_keys.add(key)
        self.video_history_files[key] = relative
        if sha:
            self.video_history_hashes.add(sha)

    def _history_skips_video(self, url: str) -> bool:
        key = video_identity(url)
        if key not in self.video_history_keys:
            return False
        rel = self.video_history_files.get(key, "")
        # Nao baixar duas vezes um arquivo que ainda existe (mesmo com override).
        if rel:
            saved = (OUT / rel).resolve()
            if saved.is_relative_to(OUT.resolve()) and saved.is_file() and saved.stat().st_size > 0:
                logging.info("VIDEO JA EXISTE LOCALMENTE; IGNORADO | %s", url)
                return True
        if not getattr(self.args, "refazer_downloads_antigos", False):
            logging.info("VIDEO HISTORICO JA BAIXADO (MESMO EXCLUIDO); IGNORADO | %s", url)
            return True
        logging.info("REFAZER DOWNLOAD ANTIGO HABILITADO | %s", url)
        return False

    def _sync_limit_resets(self):
        """Aplica somente pedidos para ESTA execução, entre operações de download.

        O servidor escreve comandos atômicos; o processo coletor é o único que
        modifica seus contadores em memória. Históricos nunca são reiniciados.
        """
        if not RESET_REQUESTS_DIR.is_dir():
            return
        for command_file in sorted(RESET_REQUESTS_DIR.glob("*.json")):
            try:
                data = json.loads(command_file.read_text(encoding="utf-8"))
                if not isinstance(data, dict) or data.get("run_id") != self.run_id:
                    continue  # pedidos antigos não atuam sobre novas execuções
                key = data.get("tipo")
                request_id = data.get("pedido")
                if not isinstance(request_id, str) or len(request_id) != 32 or not re.fullmatch(r"[a-f0-9]{32}", request_id):
                    logging.warning("REDEFINICAO V38: pedido invalido em %s", command_file.name)
                    continue
                if request_id in self._applied_reset_ids:
                    command_file.unlink(missing_ok=True)
                    continue
                if key == "videos" and self.budget_bytes > 0:
                    # Se foi enfileirado durante uma transferência, contabilizar
                    # o arquivo completo antes de aplicar o novo ciclo.
                    self.bytes_downloaded = 0
                    self._budget_hit = False
                    self.video_cycle += 1
                    self._budget_status()
                    limit_gb = self.budget_bytes / GB_BYTES
                    ciclo = self.video_cycle
                elif key in self.type_limits:
                    self.type_bytes[key] = 0
                    self.type_limited.discard(key)
                    self.type_cycles[key] = self.type_cycles.get(key, 1) + 1
                    self._type_budget_status()
                    limit_gb = self.type_limits[key] / GB_BYTES
                    ciclo = self.type_cycles[key]
                else:
                    logging.warning("REDEFINICAO V38: tipo sem limite vigente: %r", key)
                    continue
                self._applied_reset_ids.add(request_id)
                try:
                    append_csv(RESET_AUDIT_CSV, RESET_AUDIT_HEADER,
                               [timestamp(), self.run_id, request_id, key, ciclo, limit_gb])
                except OSError as exc:
                    logging.warning("REDEFINICAO V38: falha no registro de auditoria: %s", exc)
                command_file.unlink()
                logging.info("REDEFINICAO APLICADA: %s | ciclo %d | contador 0 GB; limite %.3f GB | historicos preservados",
                             key, ciclo, limit_gb)
            except (OSError, ValueError, TypeError, KeyError) as exc:
                logging.warning("REDEFINICAO V38: nao foi possivel processar %s: %s", command_file.name, exc)

    def _budget_status(self):
        payload = {"limite_gb": self.budget_bytes / GB_BYTES,
                   "run_id": self.run_id,
                   "ciclo": self.video_cycle,
                   "baixado_gb": round(self.bytes_downloaded / GB_BYTES, 5),
                   "limite_atingido": self._budget_hit,
                   "tipo_limite": "somente_videos",
                   "downloads_outros_continuam": True,
                   "continuar_busca_encadeada": bool(getattr(self.args, "continuar_busca_encadeada", False)),
                   "atualizado_em": timestamp()}
        target = OUT / "limite_downloads_status.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    def _mark_budget_hit(self, context=""):
        if self._budget_hit:
            return
        self._budget_hit = True
        logging.warning("LIMITE DE VÍDEOS ATINGIDO/INSUFICIENTE: %.3f/%.3f GB. "
                        "SOMENTE NOVOS VÍDEOS BLOQUEADOS; PDFs, ZIPs, imagens e áudios CONTINUAM. "
                        "Busca encadeada: %s. %s",
                        self.bytes_downloaded / GB_BYTES, self.budget_bytes / GB_BYTES,
                        "CONTINUA SEM BAIXAR VÍDEOS" if getattr(self.args, "continuar_busca_encadeada", False)
                        else "PARA", context)
        self._budget_status()

    def _budget_check(self, incoming_bytes: int = 0, label=""):
        """Admission check ONLY for a new video. In-flight files are never cancelled by GB."""
        self._sync_limit_resets()
        if not self.budget_bytes:
            return
        if self._budget_hit or self.bytes_downloaded >= self.budget_bytes:
            self._mark_budget_hit(label)
            raise DownloadBudgetExceeded("Limite em GB atingido antes do proximo video: " + label)

    def _budget_add(self, count: int):
        self.bytes_downloaded += count
        if self.budget_bytes and self.bytes_downloaded >= self.budget_bytes:
            self._mark_budget_hit("limite exato")
        self._budget_status()

    def _yt_progress_hooks(self):
        """Progress is informational; NEVER stop an in-flight yt-dlp download at GB ceiling.

        GB budgets count the completed final video; intermediate audio/video fragments
        and FFmpeg merge artifacts are not counted twice.
        """
        def on_progress(data):
            return  # yt-dlp writes its own progress log; quota checks occur between files.
        return [on_progress]

    def _allow_followup_search_after_limit(self):
        return (self._budget_hit and getattr(self.args, "continuar_busca_encadeada", False)
                and self._followup_context is not None)

    def _video_search_allowed(self):
        """After the video ceiling, only an explicitly enabled chained search may look up video links."""
        self._sync_limit_resets()
        return not self._budget_hit or self._allow_followup_search_after_limit()

    def _video_download_allowed(self):
        """A video budget never disables documents or other media."""
        self._sync_limit_resets()
        return not self._budget_hit and not self.reached_limit()

    def _video_followup(self, query: str, title: str, url: str):
        """Run next/related searches immediately after a complete video download.

        A reentrancy guard prevents recursion when a follow-up itself downloads
        another episode. Quotas bound the work even with dozens of engines.
        """
        if not getattr(self.args, "buscar_videos_relacionados", False):
            return
        max_queries = max(0, min(int(getattr(self.args, "max_buscas_relacionadas", 12)), 100))
        if not max_queries:
            return
        for generated, number, base in followup_queries(title):
            key = generated.casefold().strip()
            if key in self._followup_seen:
                continue
            self._followup_seen.add(key)
            if len(self._followup_queue) + self._followup_used >= max_queries:
                continue
            self._followup_queue.append((generated, {"number": number, "base": base}, url))
            append_csv(OUT / "buscas_videos_relacionados.csv",
                       ["data", "origem_video", "consulta_gerada", "status"],
                       [timestamp(), url, generated, "agendada"])
        if self._followup_running:
            return
        self._followup_running = True
        try:
            while self._followup_queue and self._followup_used < max_queries:
                if self.reached_limit() or (self._budget_hit and not getattr(self.args, "continuar_busca_encadeada", False)):
                    logging.info("RELACIONADOS: limite de vídeos/arquivos atingido; encerrando buscas extras")
                    break
                generated, context, seed_url = self._followup_queue.pop(0)
                self._followup_used += 1
                logging.info("BUSCA ENCADEADA IMEDIATA %d/%d | %s | origem=%s",
                             self._followup_used, max_queries, generated, seed_url)
                previous_context, previous_force = self._followup_context, self._followup_force
                self._followup_context, self._followup_force = context, True
                try:
                    # Query not required to exist in the user's manual list;
                    # run against all enabled/compatible sources.
                    self.search_engines(generated)
                    append_csv(OUT / "buscas_videos_relacionados.csv",
                               ["data", "origem_video", "consulta_gerada", "status"],
                               [timestamp(), seed_url, generated, "pesquisada"])
                except Exception as exc:
                    logging.exception("RELACIONADOS: falha na consulta %s: %s", generated, exc)
                finally:
                    self._followup_context, self._followup_force = previous_context, previous_force
        finally:
            self._followup_running = False

    def _type_keys(self, ext):
        ext = str(ext).lower()
        group = group_for_extension(ext)
        return list(dict.fromkeys((ext, group)))

    def _type_budget_status(self):
        payload = {
            "atualizado_em": timestamp(),
            "run_id": self.run_id,
            "tipo_limite": "limites_independentes_por_tipo",
            "formatos": {k: {"limite_gb": round(limit / GB_BYTES, 5),
                              "baixado_gb": round(self.type_bytes.get(k, 0) / GB_BYTES, 5),
                              "limite_atingido": k in self.type_limited,
                              "ciclo": self.type_cycles.get(k, 1)}
                         for k, limit in self.type_limits.items()},
            "outros_formatos_continuam": True,
        }
        target = OUT / "limites_por_tipo_status.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    def _type_budget_check(self, ext, incoming, label=""):
        self._sync_limit_resets()
        for key in self._type_keys(ext):
            limit = self.type_limits.get(key, 0)
            if not limit:
                continue
            if self.type_bytes.get(key, 0) >= limit or self.type_bytes.get(key, 0) + int(incoming) > limit:
                if key not in self.type_limited:
                    self.type_limited.add(key)
                    logging.warning("LIMITE DE %s ALCANCADO/INSUFICIENTE (%.3f/%.3f GB). "
                                    "Este tipo de arquivo sera bloqueado; outros continuam. %s",
                                    key, self.type_bytes.get(key, 0) / GB_BYTES, limit / GB_BYTES, label)
                    self._type_budget_status()
                raise DownloadBudgetExceeded("Limite em GB por tipo: " + key)

    def _type_budget_add(self, ext, count):
        for key in self._type_keys(ext):
            self.type_bytes[key] = self.type_bytes.get(key, 0) + int(count)
            if self.type_limits.get(key) and self.type_bytes[key] >= self.type_limits[key]:
                if key not in self.type_limited:
                    self.type_limited.add(key)
                    logging.warning("LIMITE EXATO ATINGIDO PARA %s: %.3f GB; downloads desse tipo suspensos", key, self.type_bytes[key] / GB_BYTES)
        self._type_budget_status()

    def _document_followup(self, query: str, title: str, url: str, extension: str):
        """Depois do arquivo validado: fonte original -> outras fontes.

        Somente uma geracao de profundidade; cache e cota evitam explosao.
        """
        if not getattr(self.args, "buscar_documentos_relacionados", False):
            return
        if self._doc_followup_running:
            return
        cap = max(0, min(int(getattr(self.args, "max_buscas_documentos", 120)), 1200))
        if cap <= self._doc_followup_used:
            return
        candidates = chained_document_queries(title, query, extension,
                self.args.ano_inicial, self.args.ano_final, cap=cap)
        if not candidates:
            return
        from urllib.parse import urlparse
        host = (urlparse(url).hostname or "").lower()
        same_host = host if host and not any(host.endswith(x) for x in ("google.com", "youtube.com", "youtu.be", "bing.com")) else ""
        preferred = self._current_engine_id
        self._doc_followup_running = True
        try:
            for generated, context in candidates:
                if self._doc_followup_used >= cap:
                    logging.info("DOCUMENTOS ENCADEADOS: quota de %s buscas atingida", cap)
                    break
                key = generated.casefold().strip()
                if key in self._doc_followup_seen:
                    continue
                self._doc_followup_seen.add(key)
                self._doc_followup_used += 1
                num = self._doc_followup_used
                logging.info("DOCUMENTO ENCADEADO IMEDIATO %d/%d | %s | origem=%s | fonte=%s",
                             num, cap, generated, url, preferred or "nao_identificada")
                append_csv(OUT / "buscas_documentos_relacionados.csv",
                    ["data", "arquivo_origem_url", "consulta_gerada", "fonte_prioritaria", "status"],
                    [timestamp(), url, generated, preferred or "", "iniciada"])
                old_ctx, old_force = self._doc_followup_context, self._followup_force
                self._doc_followup_context, self._followup_force = context, True
                try:
                    # Mesma origem primeiro: uma pesquisa por dominio, seguida
                    # pela mesma fonte sem restricao e por todas as demais.
                    if same_host and preferred and ENGINE_CATALOG.get(preferred, {}).get("kind") in ("ddgs", "html", "searxng", "site_source"):
                        self.search_engines(f"site:{same_host} {generated}", only_engine=preferred)
                    self.search_engines(generated, preferred_engine=preferred)
                    append_csv(OUT / "buscas_documentos_relacionados.csv",
                        ["data", "arquivo_origem_url", "consulta_gerada", "fonte_prioritaria", "status"],
                        [timestamp(), url, generated, preferred or "", "pesquisada"])
                except Exception:
                    logging.exception("DOCUMENTOS ENCADEADOS: erro na busca %s", generated)
                finally:
                    self._doc_followup_context, self._followup_force = old_ctx, old_force
        finally:
            self._doc_followup_running = False

    def _load_saved(self):
        if self.files_csv.exists():
            with self.files_csv.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    url = row.get("url", "")
                    digest = row.get("sha256", "")
                    rel = row.get("arquivo", "")
                    if not rel:
                        continue
                    saved_path = (OUT / rel).resolve()
                    # Protect against paths outside the result directory from
                    # malformed/corrupted CSV rows.
                    if not saved_path.is_relative_to(OUT.resolve()):
                        continue
                    if not completed_file_from_history(saved_path, row.get("bytes", "")):
                        logging.warning("Registro historico invalido, liberado para nova tentativa: %s", rel)
                        continue
                    if url:
                        self.seen_urls.add(url)
                        self.url_to_file[url] = saved_path
                    if digest:
                        self.hashes.add(digest)
                        self.hash_to_file[digest] = saved_path
        if self.links_csv.exists():
            with self.links_csv.open(encoding="utf-8-sig", newline="") as f:
                for row in csv.DictReader(f):
                    self.known_links.add((row.get("pesquisa", ""), row.get("url", "")))

    def error(self, context: str, url: str, ex: Exception | str):
        msg = str(ex)[:500]
        logging.warning("%s: %s", context, msg)
        append_csv(self.errors_csv, ["data", "etapa", "url", "erro"], [timestamp(), context, url, msg])

    def link(self, query: str, title: str, url: str, kind: str):
        url = normurl(url)
        if not valid_web(url) or (query, url) in self.known_links:
            return
        self.known_links.add((query, url))
        append_csv(self.links_csv,
                   ["data", "pesquisa", "titulo", "tipo", "url"],
                   [timestamp(), query, title[:250], kind, url])

    def pause(self, small=False):
        t = self.args.pausa * (0.3 if small else 1.0)
        if t > 0:
            time.sleep(t + (random.random() * 0.3 if not small else 0))

    def reached_limit(self):
        # Existing 'max new files' is an independent, global user-selected cap.
        # The video GB ceiling must NOT enter this predicate, otherwise it
        # mistakenly shuts down document discovery and non-video downloads.
        return self.args.limite_arquivos > 0 and self.downloaded >= self.args.limite_arquivos

    def download(self, url: str, query: str, title: str):
        url = normurl(url)
        if not valid_web(url) or is_video(url) or self.reached_limit():
            return
        if self._doc_followup_context is not None and not matches_document_context(title, url, self._doc_followup_context):
            return
        if url in self.seen_urls:
            existing = self.url_to_file.get(url)
            if existing and completed_file_from_history(existing):
                link_shared_gabarito(existing, classify_document(query, title, url))
                return
            # Prior CSV entries ending in .part must never block another try.
            self.seen_urls.discard(url)
            self.url_to_file.pop(url, None)
        response = None
        temp = None
        ready = None
        # Se a extensao do arquivo ja aparece na URL e seu teto foi atingido,
        # nem iniciar a transferencia. URLs sem extensao sao analisadas pelo MIME.
        guessed_ext = direct_extension(url)
        if guessed_ext in DOC_EXT:
            try:
                self._type_budget_check(guessed_ext, 0, "preflight documento:" + url)
            except DownloadBudgetExceeded:
                return
        try:
            response = self.s.get(url, stream=True, timeout=(12, 45), allow_redirects=True)
            response.raise_for_status()
            if response.status_code == 206:
                raise ValueError("Servidor retornou HTTP 206 parcial sem solicitação de intervalo")
            ext = direct_extension(response.url)
            typ = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            if typ in MIME_EXT:
                ext = MIME_EXT[typ]
            if ext not in DOC_EXT:
                return
            if typ.startswith(("text/html", "application/xhtml+xml")) or (typ.startswith("text/plain") and ext != ".txt"):
                return
            limit = self.args.tamanho_max_mb * 1024 * 1024
            length_str = response.headers.get("Content-Length", "")
            length = int(length_str) if length_str.isdigit() else 0
            if length and length > limit:
                self.error("arquivo grande", url, f"Ignorado: {length} bytes (limite {self.args.tamanho_max_mb} MB)")
                return
            self._type_budget_check(ext, 0, "documento:" + url)
            meta = classify_document(query, title, url)
            original = unquote(Path(urlparse(response.url).path).name)
            basename = safe_filename(Path(original).stem or title or "arquivo", 85)
            short_hash = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
            dest = organized_path(meta, f"{basename}_{short_hash}{ext}")
            # V31: temporarios fora da pasta materiais, preservados para auditoria.
            session_id = uuid.uuid4().hex[:12]
            # Repete a hierarquia de categorias em pasta de quarentena separada.
            relative_dir = dest.parent.relative_to(OUT / "materiais")
            pending_dir = OUT / "_downloads_incompletos" / relative_dir
            pending_dir.mkdir(parents=True, exist_ok=True)
            temp = pending_dir / (dest.name + f".{session_id}.part")
            digest = hashlib.sha256()
            total = 0
            with temp.open("xb") as fp:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if total > limit:
                        raise ValueError(f"Arquivo excedeu o limite de {self.args.tamanho_max_mb} MB")
                    digest.update(chunk)
                    fp.write(chunk)
                fp.flush()
                os.fsync(fp.fileno())
            validate_download(temp, ext, total, length, response.headers.get("Content-Encoding", "").strip())
            file_hash = digest.hexdigest()
            canonical = self.hash_to_file.get(file_hash)
            if file_hash in self.hashes and canonical and completed_file_from_history(canonical):
                # Only discard the newly created partial, never older partials.
                temp.unlink()
                temp = None
                self.seen_urls.add(url)
                self.url_to_file[url] = canonical
                link_shared_gabarito(canonical, meta)
                logging.info("Duplicata validada por hash: %s", url)
                return
            # IMPORTANT: classifiers must read a file with its REAL suffix,
            # never a .pdf.part path. Keep it outside published materials.
            staging = OUT / "_validando_downloads"
            staging.mkdir(parents=True, exist_ok=True)
            ready = staging / f"{basename}_{short_hash}_{session_id}{ext}"
            temp.replace(ready)
            temp = None
            meta, internal_text, media_tags = infer_meta(query, title, response.url, ready, meta)
            meta = reconcile_document_year(meta, ready.name)
            suggested = str(suggested_filename(ready, meta, internal_text, media_tags, query, title) or "").strip()
            filename = Path(suggested).name
            if filename.lower().endswith(".part"):
                filename = filename[:-5]
            if Path(filename).suffix.lower() != ext:
                filename = Path(filename).stem + ext
            filename = safe_filename(filename, 180)
            if not filename.lower().endswith(ext):
                filename = filename + ext
            meta = reconcile_document_year(meta, filename)
            final_dest = organized_path(meta, filename)
            if final_dest.exists():
                # Never overwrite previously collected documents.
                if completed_file_from_history(final_dest) and hashlib.sha256(final_dest.read_bytes()).hexdigest() == file_hash:
                    ready.unlink()
                    ready = None
                    self.hashes.add(file_hash)
                    self.hash_to_file[file_hash] = final_dest
                    self.url_to_file[url] = final_dest
                    self.seen_urls.add(url)
                    link_shared_gabarito(final_dest, meta)
                    return
                final_dest = final_dest.with_name(final_dest.stem + "_" + file_hash[:12] + final_dest.suffix)
                index = 2
                while final_dest.exists():
                    final_dest = final_dest.with_name(final_dest.stem + f"_{index}" + final_dest.suffix)
                    index += 1
            ready.replace(final_dest)
            ready = None
            final_dest, meta = finalize_download_from_content(final_dest, meta, query, title, url, file_hash)
            if final_dest.suffix.lower() != ext or not completed_file_from_history(final_dest, str(total)):
                raise RuntimeError(f"Finalizacao gerou caminho invalido: {final_dest}")
            self.hashes.add(file_hash)
            self.hash_to_file[file_hash] = final_dest
            self.url_to_file[url] = final_dest
            self.seen_urls.add(url)
            self.downloaded += 1
            self._type_budget_add(ext, total)
            append_csv(self.files_csv, ["data","pesquisa","titulo","url","arquivo","bytes","sha256"],
                       [timestamp(),query,title[:250],url,str(final_dest.relative_to(OUT)),str(total),file_hash])
            catalog_document(final_dest, meta, file_hash, total)
            link_shared_gabarito(final_dest, meta)
            logging.info("BAIXADO E ANALISADO %s (%d KB) | %s/%s/%s | texto interno: %s", final_dest.relative_to(OUT), round(total / 1024), meta["exam"], meta["year"], meta["material"], "extraído" if internal_text else "não disponível")
            try:
                self._document_followup(query, title or final_dest.stem, url, ext)
            except Exception:
                logging.exception("DOCUMENTOS ENCADEADOS: erro depois de baixar; arquivo ja salvo")
        except Exception as ex:
            if isinstance(ex, DownloadBudgetExceeded) and temp is not None and temp.exists():
                temp.unlink(missing_ok=True)
                temp = None
            # Preserve new .part and staged payloads for later diagnosis.
            if ready is not None and ready.exists():
                logging.warning("Arquivo validado sem finalizacao, preservado em: %s", ready)
            if temp is not None and temp.exists():
                logging.warning("Download nao concluido, .part preservado em: %s", temp)
            self.error("download", url, ex)
        finally:
            if response is not None:
                response.close()

    def download_media(self, url: str, query: str, title: str) -> bool:
        """Download a directly linked public media asset; no DRM/auth bypass."""
        url = normurl(url)
        if not getattr(self.args, "baixar_midias_publicas", False) or not valid_web(url) or self.reached_limit():
            return False
        if url in self.seen_media_urls:
            return False
        ext_from_url = direct_extension(url)
        if ext_from_url in MEDIA_EXT:
            try:
                self._type_budget_check(".mp4" if ext_from_url in VIDEO_FILES and shutil.which("ffmpeg") else ext_from_url, 0, "preflight midia:" + url)
            except DownloadBudgetExceeded:
                return False
        if ext_from_url in VIDEO_FILES and not self._video_download_allowed():
            return False
        if ext_from_url in VIDEO_FILES and self._history_skips_video(url):
            return False
        self.seen_media_urls.add(url)
        response = None
        temp = None
        try:
            response = self.s.get(url, stream=True, timeout=(12, 45), allow_redirects=True)
            response.raise_for_status()
            content_type = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
            ext = MEDIA_MIME_EXT.get(content_type, ext_from_url)
            if ext not in MEDIA_EXT or not content_type.startswith(("image/", "audio/", "video/", "application/octet-stream")):
                return False
            if ext in VIDEO_FILES and not self._video_download_allowed():
                return False
            if ext in VIDEO_FILES and self._history_skips_video(url):
                return False
            if content_type in ("text/html", "application/xhtml+xml"):
                return False
            limit_mb = getattr(self.args, "tamanho_max_midia_mb", 0)
            limit = limit_mb * 1024 * 1024 if limit_mb > 0 else 0
            length = int(response.headers.get("Content-Length") or 0)
            if limit and length and length > limit:
                self.error("midia_grande", url, f"Ignorado: {length} bytes excede o limite de {getattr(self.args, 'tamanho_max_midia_mb', 1024)} MB")
                return False
            if length and ext in VIDEO_FILES:
                self._budget_check(0, "novo video:" + url)
            self._type_budget_check(".mp4" if ext in VIDEO_FILES and shutil.which("ffmpeg") else ext, 0, "midia:" + url)
            category = "videos" if ext in VIDEO_FILES else "audio" if ext in {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"} else "imagens"
            folder = OUT / "midias" / category
            folder.mkdir(parents=True, exist_ok=True)
            name_from_url = unquote(Path(urlparse(response.url).path).name)
            stem = Path(name_from_url).stem if name_from_url else ""
            stem = safe_filename(stem or title or category, 90)
            key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:10]
            dest = folder / f"{stem}_{key}{ext}"
            if dest.is_file():
                logging.info("MIDIA JA EXISTE; IGNORANDO para nao sobrescrever: %s", dest)
                return False
            temp = dest.with_suffix(dest.suffix + ".part")
            if temp.exists():
                logging.warning("MIDIA PARCIAL EXISTENTE: %s. Nao sobrescrever automaticamente.", temp)
                return False
            digest = hashlib.sha256()
            total = 0
            with temp.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=128 * 1024):
                    if not chunk:
                        continue
                    total += len(chunk)
                    if limit and total > limit:
                        raise ValueError(f"Mídia excedeu o limite de {getattr(self.args, 'tamanho_max_midia_mb', 1024)} MB")
                    digest.update(chunk)
                    handle.write(chunk)
            if total == 0:
                raise ValueError("Arquivo de mídia vazio")
            sha = digest.hexdigest()
            if (sha in self.media_hashes or (ext in VIDEO_FILES and sha in self.video_history_hashes)) and not (ext in VIDEO_FILES and getattr(self.args, "refazer_downloads_antigos", False)):
                logging.info("VIDEO/MIDIA DUPLICADO POR HASH; IGNORADO: %s", url)
                if ext in VIDEO_FILES:
                    self._record_video_history(url, sha, "", title, "duplicata_hash")
                temp.unlink(missing_ok=True)
                return False
            temp.replace(dest)
            if ext in VIDEO_FILES:
                dest = ensure_mp4_video(dest)
                ext = dest.suffix.lower()
                total = dest.stat().st_size
                with dest.open("rb") as fp:
                    digest = hashlib.sha256()
                    for piece in iter(lambda: fp.read(1024 * 1024), b""):
                        digest.update(piece)
                sha = digest.hexdigest()
            self.media_hashes.add(sha)
            self.downloaded += 1
            self._type_budget_add(ext, total)
            if ext in VIDEO_FILES:
                self._budget_add(total)
                self._record_video_history(url, sha, str(dest.relative_to(OUT)), title)
            append_csv(OUT / "midias_baixadas.csv",
                       ["data", "pesquisa", "titulo", "url", "arquivo", "tipo_mime", "bytes", "sha256", "nota_uso"],
                       [timestamp(), query, title[:250], url, str(dest.relative_to(OUT)), content_type, str(total), sha,
                        "Baixar/reutilizar apenas se houver autorização, licença ou permissão do titular"])
            logging.info("MÍDIA DIRETA BAIXADA | %s | %d bytes | %s", dest.relative_to(OUT), total, content_type)
            if ext in VIDEO_FILES:
                self._prepare_video_for_telegram(dest)
            if ext in VIDEO_FILES:
                try:
                    self._video_followup(query, title, url)
                except Exception:
                    logging.exception("RELACIONADOS: erro apos download direto; arquivo ja salvo")
            return True
        except Exception as ex:
            if temp and temp.exists():
                temp.unlink(missing_ok=True)
            self.error("download_midia", url, ex)
            return False
        finally:
            if response is not None:
                response.close()

    def download_supported_media_page(self, url: str, query: str, title: str) -> bool:
        """Use yt-dlp only for an explicitly enabled, known public media platform URL."""
        if not getattr(self.args, "baixar_midias_publicas", False) or not valid_web(url) or not self._video_download_allowed():
            return False
        if self._history_skips_video(url):
            return False
        try:
            self._type_budget_check(".mp4", 0, "novo video de plataforma")
        except DownloadBudgetExceeded:
            return False
        host = domain(url)
        if not any(host == d or host.endswith("." + d) for d in MEDIA_PLATFORM_DOMAINS):
            return False
        try:
            from yt_dlp import YoutubeDL
            folder = OUT / "midias" / "videos"
            folder.mkdir(parents=True, exist_ok=True)
            opts = {"quiet": True, "no_warnings": True, "noplaylist": True, "ignoreerrors": False,
                    "continuedl": True, "retries": 2, "socket_timeout": 20,
                    "max_filesize": (self.args.tamanho_max_midia_mb * 1024 * 1024) if self.args.tamanho_max_midia_mb > 0 else None,
                    "format": youtube_format_selector(), "outtmpl": str(folder / "%(title).90B_%(id)s.%(ext)s"),
                    "download_archive": None if getattr(self.args, "refazer_downloads_antigos", False) else str(OUT / "midias_download_archive.txt"), "restrictfilenames": True,
                    "windowsfilenames": True, "progress_hooks": self._yt_progress_hooks()}
            before = {x.resolve() for x in folder.iterdir() if x.is_file()}
            if shutil.which("ffmpeg"):
                opts["merge_output_format"] = "mp4"
            with YoutubeDL(opts) as ydl:
                result = ydl.download([url])
            created = [x for x in folder.iterdir() if x.is_file() and x.resolve() not in before and not x.name.endswith((".part", ".ytdl"))]
            if result == 0 and created:
                created = [ensure_mp4_video(f) if f.suffix.lower() in VIDEO_FILES else f for f in created]
                any_new_video = False
                for file in created:
                    if file.suffix.lower() not in VIDEO_FILES:
                        continue
                    digest_obj = hashlib.sha256()
                    with file.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest_obj.update(chunk)
                    digest = digest_obj.hexdigest()
                    if digest in self.video_history_hashes and not getattr(self.args, "refazer_downloads_antigos", False):
                        logging.info("VIDEO DUPLICADO POR HASH, DESCARTANDO APENAS NOVO ARQUIVO: %s", file)
                        self._record_video_history(url, digest, "", title, "duplicata_hash")
                        file.unlink()
                        continue
                    # Um download iniciado e concluido pode ultrapassar o teto.
                    # Registrar integralmente; bloquear somente a proxima transferencia.
                    self.downloaded += 1
                    any_new_video = True
                    self._type_budget_add(file.suffix.lower(), file.stat().st_size)
                    self._budget_add(file.stat().st_size)
                    self._record_video_history(url, digest, str(file.relative_to(OUT)), title)
                    append_csv(OUT / "midias_baixadas.csv",
                               ["data", "pesquisa", "titulo", "url", "arquivo", "tipo_mime", "bytes", "sha256", "nota_uso"],
                               [timestamp(), query, title[:250], url, str(file.relative_to(OUT)), file.suffix.lower(), str(file.stat().st_size), digest,
                                "Download iniciado pelo usuário; confirmar autorização/licença antes de reutilizar"])
                    logging.info("MÍDIA DE PLATAFORMA BAIXADA | %s", file.relative_to(OUT))
                    self._prepare_video_for_telegram(file)
                if any_new_video:
                    try:
                        self._video_followup(query, title, url)
                    except Exception:
                        logging.exception("RELACIONADOS: erro apos download de plataforma; arquivo ja salvo")
                return True
            self.error("download_plataforma", url, "Nenhum arquivo concluído; confira formato, disponibilidade e autorização")
        except ImportError:
            self.error("download_plataforma", url, "yt-dlp não instalado; instale yt-dlp no ambiente Python do coletor")
        except Exception as ex:
            self.error("download_plataforma", url, ex)
        return False

    def explore_page(self, url: str, query: str, title: str, official=False):
        url = normurl(url)
        if not valid_web(url) or is_video(url) or url in self.pages_scanned or (self.reached_limit() and not self._allow_followup_search_after_limit()):
            return
        self.pages_scanned.add(url)
        try:
            response = self.s.get(url, timeout=(12, 35))
            response.raise_for_status()
            if "html" not in response.headers.get("Content-Type", "").lower():
                return
            if len(response.content) > 3_000_000:
                return
            soup = BeautifulSoup(response.text, "html.parser")
            candidates = []
            for a in soup.find_all("a", href=True):
                target = normurl(urljoin(url, a.get("href", "")))
                if not valid_web(target):
                    continue
                text = a.get_text(" ", strip=True)[:220]
                if official:
                    # Nos portais oficiais: apenas links para prova/gabarito hospedados nos repositorios.
                    d = domain(target)
                    if "inep" in domain(url) or "/inep/" in urlparse(url).path.lower():
                        if d not in ("download.inep.gov.br", "riep.inep.gov.br"):
                            continue
                    else:
                        if d != "fatweb.s3.amazonaws.com":
                            continue
                    if not re.search(r"prova|gabarito|caderno", text, re.I):
                        continue
                else:
                    is_doc = direct_extension(target) in DOC_EXT and RELEVANT.search(text + " " + target)
                    is_media = getattr(self.args, "baixar_midias_publicas", False) and direct_extension(target) in MEDIA_EXT
                    if not (is_doc or is_media):
                        continue
                candidates.append((target, text))
            # Direct media embedded in a public HTML page is eligible only when the
            # user explicitly enabled the media-download option.
            embedded_media_targets = set()
            if not official and getattr(self.args, "baixar_midias_publicas", False):
                for tag in soup.find_all(["video", "audio", "source", "img"]):
                    if tag.name == "video" and not self._video_search_allowed():
                        continue
                    src = tag.get("src") or tag.get("data-src")
                    if not src and tag.name == "video" and tag.get("poster"):
                        src = tag.get("poster")
                    if not src:
                        continue
                    target = normurl(urljoin(response.url, str(src)))
                    if (valid_web(target) and (direct_extension(target) in MEDIA_EXT or tag.name in ("video", "audio", "source", "img"))
                            and (direct_extension(target) not in VIDEO_FILES or self._video_search_allowed())):
                        embedded_media_targets.add(target)
                        candidates.append((target, tag.get("alt") or tag.get("title") or title))
                for tag in soup.find_all("iframe", src=True):
                    embedded = normurl(urljoin(response.url, str(tag.get("src"))))
                    if valid_web(embedded):
                        self.download_supported_media_page(embedded, query, tag.get("title") or title)
            seen = set()
            quota = min(self.args.max_links_pagina, 20) if not official else 200
            for target, text in candidates:
                if self.reached_limit() and not self._allow_followup_search_after_limit():
                    break
                if target in seen:
                    continue
                seen.add(target)
                ext = direct_extension(target)
                if not official and getattr(self.args, "baixar_midias_publicas", False) and (ext in MEDIA_EXT or target in embedded_media_targets):
                    self.link(query, text or title, target, "mídia direta (licença/autorizar antes de reutilizar)")
                    self.download_media(target, query, text or title)
                else:
                    self.link(query, text or title, target, "arquivo")
                    self.download(target, query, text or title)
                self.pause(small=True)
                if len(seen) >= quota:
                    break
        except Exception as ex:
            self.error("pagina", url, ex)

    def collect_official(self, exam: str, year: int):
        key = f"{exam}:{year}"
        if (key in self.done_official and not getattr(self.args, "repetir_concluidas", False)) or self.reached_limit():
            return
        url = (OFFICIAL_ENEM if exam == "ENEM" else OFFICIAL_FATEC).format(ano=year)
        query = f"{exam} {year} provas gabaritos oficiais"
        logging.info("OFICIAL %s %d", exam, year)
        self.link(query, "Repositorio oficial", url, "pagina oficial")
        self.explore_page(url, query, "Provas e gabaritos", official=True)
        # Se ha problemas, a URL oficial fica em erros.csv; pode repetir resetando arquivo de marcadores.
        with self.done_official_path.open("a", encoding="utf-8") as f:
            f.write(key + "\n")
        self.done_official.add(key)
        self.pause(small=True)

    def _record_engine(self, query: str, engine: str, title: str, url: str, status: str, detail: str = ""):
        self.engine_last_status[(query, engine)] = status
        append_csv(OUT / "mecanismos_busca.csv",
                   ["data", "pesquisa", "mecanismo", "titulo", "url", "status", "detalhe"],
                   [timestamp(), query, engine, title[:250], url, status, detail[:400]])

    def _accept_engine_result(self, q: str, engine_label: str, item: dict):
        url = normurl(str(item.get("href") or item.get("url") or ""))
        title = str(item.get("title") or "Pagina")
        if not valid_web(url):
            return False
        if self._followup_context is not None:
            ext = direct_extension(url)
            if not (is_video(url) or ext in VIDEO_FILES) or not follows_video_title(title, self._followup_context):
                return False
        self._record_engine(q, engine_label, title, url, "resultado")
        if is_video(url):
            if not self._video_search_allowed():
                return False
            self.link(q, title, url, f"video/link [{engine_label}]")
            if getattr(self.args, "baixar_midias_publicas", False):
                self.download_supported_media_page(url, q, title)
        elif direct_extension(url) in VIDEO_FILES and not self._video_search_allowed():
            return False
        elif direct_extension(url) in MEDIA_EXT and getattr(self.args, "baixar_midias_publicas", False):
            self.link(q, title, url, f"mídia direta [{engine_label}]")
            self.download_media(url, q, title)
        elif direct_extension(url) in DOC_EXT:
            self.link(q, title, url, f"arquivo [{engine_label}]")
            self.download(url, q, title)
        else:
            self.link(q, title, url, f"pagina/curso [{engine_label}]")
            if not is_video(url):
                self.explore_page(url, q, title)
        return True

    def _search_ddgs_backend(self, q: str, engine_id: str, engine: dict):
        from ddgs import DDGS
        label = engine["label"]
        try:
            results = DDGS(timeout=20).text(q, region="br-pt", max_results=self.args.resultados_busca,
                                            backend=engine["backend"])
            count = 0
            for item in results or []:
                if self._accept_engine_result(q, label, item):
                    count += 1
            self._record_engine(q, label, "", "", "ok" if count else "sem_resultados",
                                f"{count} resultado(s) aceito(s)")
            return count
        except Exception as ex:
            self._record_engine(q, label, "", "", "erro", str(ex))
            self.error(f"pesquisa:{label}", q, ex)
            return 0

    def _search_html_engine(self, q: str, engine_id: str, engine: dict):
        label = engine["label"]
        url = engine["url"].format(query=quote_plus(q))
        try:
            response = self.s.get(url, timeout=(12, 25), allow_redirects=True)
            response.raise_for_status()
            if "html" not in response.headers.get("Content-Type", "").lower():
                raise RuntimeError("O provedor não retornou HTML de resultados")
            if len(response.content) > 5_000_000:
                raise RuntimeError("Página de resultados maior que o limite de segurança")
            soup = BeautifulSoup(response.text, "html.parser")
            count = 0
            seen = set()
            host = domain(response.url)
            # Extrator genérico conservador: links externos com texto descritivo.
            # Sites com HTML dinâmico podem retornar zero; isso é registrado, não contornado.
            for a in soup.find_all("a", href=True):
                href = normurl(urljoin(response.url, a.get("href", "")))
                title = a.get_text(" ", strip=True)
                if not valid_web(href) or not title or len(title) < 5 or href in seen:
                    continue
                d = domain(href)
                if not d or d == host or d.endswith("." + host):
                    continue
                if any(x in d for x in ("google.com", "facebook.com", "instagram.com", "twitter.com")):
                    continue
                if href.lower().startswith(("javascript:", "mailto:")):
                    continue
                seen.add(href)
                self._accept_engine_result(q, label, {"title": title, "href": href})
                count += 1
                if count >= self.args.resultados_busca:
                    break
            status = "ok" if count else "sem_resultados_ou_html_incompativel"
            self._record_engine(q, label, "", response.url, status,
                                f"{count} resultado(s); extração HTML genérica, sujeito a mudanças no site")
            return count
        except Exception as ex:
            self._record_engine(q, label, "", url, "erro_ou_bloqueio", str(ex))
            self.error(f"pesquisa:{label}", url, ex)
            return 0
        finally:
            try:
                response.close()
            except Exception:
                pass

    def _download_youtube_video(self, q: str, title: str, video_url: str, video_id: str) -> bool:
        """Download only videos the user is authorized to download; keep resumable partials."""
        if not (getattr(self.args, "baixar_videos_youtube", False) or getattr(self.args, "baixar_midias_publicas", False)) or not self._video_download_allowed():
            return False
        if self._history_skips_video(video_url):
            return False
        try:
            self._type_budget_check(".mp4", 0, "novo video YouTube")
        except DownloadBudgetExceeded:
            return False
        try:
            from yt_dlp import YoutubeDL
        except ImportError:
            self.error("download YouTube", video_url, "yt-dlp não está instalado. Execute: python -m pip install --upgrade yt-dlp")
            return False

        meta = classify_document(q, title, video_url)
        if getattr(self.args, "baixar_midias_publicas", False):
            folder = OUT / "midias" / "videos"
        else:
            meta["material"] = "Videoaulas"
            folder = organized_path(meta, "placeholder.mp4").parent
        folder.mkdir(parents=True, exist_ok=True)

        # A complete file already present counts as satisfied; a .part file does not.
        complete = [p for p in folder.glob(f"*_{video_id}.*")
                    if p.is_file() and not p.name.endswith((".part", ".ytdl"))]
        if complete:
            logging.info("VÍDEO JÁ EXISTE; download ignorado | %s | %s", video_id, title[:120])
            return True

        # Reuse the sequence/name of an interrupted partial where possible, instead
        # of incrementing the sequence and leaving an old .part permanently stranded.
        partials = sorted(
            [p for p in folder.glob(f"Video_*_{video_id}.*.part") if p.is_file()],
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        if partials:
            partial = partials[0]
            final_path = Path(str(partial)[:-5])  # remove only trailing .part
            prefix = str(final_path.with_suffix(""))
            outtmpl = prefix + ".%(ext)s"
            partial_ext = final_path.suffix.lower().lstrip(".")
            format_selector = f"best[ext={partial_ext}]/best" if partial_ext else youtube_format_selector()
            logging.info("RETOMANDO DOWNLOAD PARCIAL | %s", partial.name)
        else:
            sequence = 1
            while list(folder.glob(f"Video_{sequence:03d}_*")):
                sequence += 1
            outtmpl = str(folder / f"Video_{sequence:03d}_%(title).100B_%(id)s.%(ext)s")
            format_selector = youtube_format_selector()

        opts = {
            "outtmpl": outtmpl,
            "format": format_selector,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": False,
            "continuedl": True,
            "overwrites": False,
            "retries": 3,
            "fragment_retries": 3,
            "extractor_retries": 2,
            "file_access_retries": 2,
            "socket_timeout": 30,
            "max_filesize": (self.args.tamanho_max_midia_mb * 1024 * 1024) if self.args.tamanho_max_midia_mb > 0 else None,
            "restrictfilenames": True,
            "windowsfilenames": True,
            "progress_hooks": self._yt_progress_hooks(),
        }
        if shutil.which("ffmpeg"):
            opts["merge_output_format"] = "mp4"

        before = set(folder.glob(f"*_{video_id}.*"))
        try:
            with YoutubeDL(opts) as ydl:
                result = ydl.download([video_url])
            after = set(folder.glob(f"*_{video_id}.*"))
            created = [f for f in after - before if f.is_file() and not f.name.endswith((".part", ".ytdl"))]
            if result == 0 and created:
                created = [ensure_mp4_video(f) if f.suffix.lower() in VIDEO_FILES else f for f in created]
                total_bytes = sum(f.stat().st_size for f in created)
                digest = hashlib.sha256()
                for f in created:
                    with f.open("rb") as handle:
                        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                            digest.update(chunk)
                sha = digest.hexdigest()
                if sha in self.video_history_hashes and not getattr(self.args, "refazer_downloads_antigos", False):
                    for f in created:
                        f.unlink()
                    logging.info("VIDEO DUPLICADO POR HASH, DESCARTADO: %s", video_url)
                    self._record_video_history(video_url, sha, "", title, "duplicata_hash")
                    return False
                # Contabilizar formatos finais. Nenhum download iniciado e interrompido
                # ao ultrapassar limites de GB; o teto bloqueia apenas o proximo arquivo.
                self.downloaded += len(created)
                for f in created:
                    self._type_budget_add(f.suffix.lower(), f.stat().st_size)
                self._budget_add(total_bytes)
                self._record_video_history(video_url, sha, str(created[0].relative_to(OUT)), title)
                append_csv(OUT / "videos_youtube_baixados.csv",
                           ["data", "pesquisa", "titulo", "url", "arquivo", "bytes", "sha256", "nota_uso"],
                           [timestamp(), q, title[:250], video_url, str(created[0].relative_to(OUT)), str(total_bytes), sha,
                            "Download autorizado pelo usuário; confirme a licença do titular antes de reutilizar"])
                logging.info("VÍDEO BAIXADO | %s | %s", created[0].relative_to(OUT), title[:150])
                for file in created:
                    if file.suffix.lower() in VIDEO_FILES:
                        self._prepare_video_for_telegram(file)
                try:
                    self._video_followup(q, title, video_url)
                except Exception:
                    logging.exception("RELACIONADOS: erro apos download YouTube; arquivo ja salvo")
                return True

            detail = f"yt-dlp retornou código {result}; seletor usado: {format_selector}"
            append_csv(OUT / "youtube_downloads_falhos.csv",
                       ["data", "pesquisa", "titulo", "url", "video_id", "status", "detalhe"],
                       [timestamp(), q, title[:250], video_url, video_id, "download_falhou", detail])
            logging.warning("VÍDEO NÃO BAIXADO | %s | %s", video_url, detail)
        except Exception as ex:
            self.error("download YouTube", video_url, ex)
            append_csv(OUT / "youtube_downloads_falhos.csv",
                       ["data", "pesquisa", "titulo", "url", "video_id", "status", "detalhe"],
                       [timestamp(), q, title[:250], video_url, video_id, "erro", str(ex)[:400]])
        return False

    def _search_youtube(self, q: str, engine: dict):
        label = engine["label"]
        if not self._video_search_allowed():
            logging.info("BUSCA YOUTUBE SUSPENSA: teto de vídeos em GB alcançado; pesquisas de outros materiais continuam")
            return 0
        limit = max(1, min(int(self.args.resultados_busca), 10))
        try:
            from yt_dlp import YoutubeDL
            opts = {"quiet": True, "no_warnings": True, "skip_download": True, "extract_flat": True,
                    "noplaylist": True, "socket_timeout": 20, "retries": 1}
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(f"ytsearch{limit}:{q}", download=False)
            entries = (info or {}).get("entries") or []
            count = 0
            download_failures = 0
            seen = set()
            for item in entries:
                if not item:
                    continue
                video_id = str(item.get("id") or "").strip()
                video_url = item.get("webpage_url") or item.get("original_url") or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
                title = str(item.get("title") or "Vídeo do YouTube")
                if not video_id or not valid_web(video_url) or video_id in seen:
                    continue
                if self._followup_context is not None and not follows_video_title(title, self._followup_context):
                    continue
                seen.add(video_id)
                self.link(q, title, video_url, f"video/YouTube [{label}]")
                self._record_engine(q, label, title, video_url, "resultado", f"ID={video_id}")
                count += 1
                if getattr(self.args, "baixar_videos_youtube", False) or getattr(self.args, "baixar_midias_publicas", False):
                    downloaded_ok = self._download_youtube_video(q, title, video_url, video_id)
                    if not downloaded_ok and self._video_download_allowed():
                        download_failures += 1
                if count >= limit or self.reached_limit() or not self._video_search_allowed():
                    break
            if count and (getattr(self.args, "baixar_videos_youtube", False) or getattr(self.args, "baixar_midias_publicas", False)) and download_failures:
                final_status = "resultado_com_falha_download"
            else:
                final_status = "ok" if count else "sem_resultados"
            self._record_engine(q, label, "", engine.get("url", ""), final_status,
                                f"{count} vídeo(s) encontrado(s); falhas de download={download_failures}; download={'ativado' if (getattr(self.args, 'baixar_videos_youtube', False) or getattr(self.args, 'baixar_midias_publicas', False)) else 'desativado'}")
            return count
        except ImportError:
            # Graceful fallback: still attempt public result-page extraction if yt-dlp is not installed.
            url = engine["url"].format(query=quote_plus(q))
            try:
                response = self.s.get(url, timeout=(12, 25), allow_redirects=True)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, "html.parser")
                count = 0; seen = set()
                for a in soup.find_all("a", href=True):
                    href = urljoin(response.url, a.get("href", "")); title = a.get("title") or a.get_text(" ", strip=True)
                    if "youtube.com/watch?v=" not in href or not title: continue
                    href = normurl(href.split("&", 1)[0])
                    if href in seen: continue
                    seen.add(href); self.link(q, title, href, f"video/YouTube [{label}]")
                    self._record_engine(q, label, title, href, "resultado"); count += 1
                    if count >= limit: break
                self._record_engine(q, label, "", response.url, "ok" if count else "sem_resultados_ou_html_incompativel", f"{count} vídeo(s); instale yt-dlp para pesquisa/download mais robustos")
                return count
            except Exception as ex:
                self._record_engine(q, label, "", url, "erro_ou_bloqueio", str(ex)); self.error(f"pesquisa:{label}", url, ex); return 0
        except Exception as ex:
            self._record_engine(q, label, "", "", "erro", str(ex)); self.error(f"pesquisa:{label}", q, ex); return 0

    def _search_telegram_public(self, q: str, engine: dict):
        """Search public Telegram pages indexed on the web and explicitly supplied public channels only."""
        label = engine["label"]
        # Define the result cap locally. The previous v10 build referenced
        # `limit` below without initializing it, causing NameError and skipping
        # all Telegram results for that query.
        limit = max(1, min(int(self.args.resultados_busca), 20))
        found = {}
        try:
            from ddgs import DDGS
            # Telegram does not expose a universal unauthenticated public search API. Search indexes
            # for public t.me pages using several query shapes; never attempt private/invite-only chats.
            per_pattern = max(2, min(5, int(self.args.resultados_busca)))
            # Try every selected native DDGS backend for public Telegram URLs, then add
            # two alternate query shapes. Provider availability/rate limits are logged.
            backends = []
            # DDGS does not support every search provider listed in the collector UI.
            # Restrict this Telegram-index query to known DDGS text backends so one
            # unsupported provider does not create misleading failures.
            supported_backends = {"brave", "duckduckgo", "google", "mojeek", "startpage", "wikipedia", "yahoo", "grokipedia"}
            for selected_id in self.args.mecanismos:
                selected_engine = ENGINE_CATALOG.get(selected_id, {})
                backend = selected_engine.get("backend")
                if selected_engine.get("kind") == "ddgs" and backend in supported_backends and backend not in backends:
                    backends.append(backend)
            if not backends:
                backends = ["duckduckgo"]
            searches = [(f"site:t.me {q}", backend) for backend in backends]
            searches.extend([(f"site:t.me/s/ {q}", "duckduckgo"), (f"site:telegram.me {q}", "duckduckgo")])
            for pattern, backend in searches:
                try:
                    rows = DDGS(timeout=12).text(pattern, region="br-pt", max_results=per_pattern, backend=backend)
                    for item in rows or []:
                        href = normurl(str(item.get("href") or item.get("url") or ""))
                        host = domain(href)
                        if not valid_web(href) or host not in ("t.me", "www.t.me", "telegram.me", "www.telegram.me"):
                            continue
                        title = str(item.get("title") or item.get("body") or "Página pública do Telegram")
                        found[href] = title
                except Exception as ex:
                    logging.info("Telegram: consulta indexada falhou (%s, backend=%s): %s", pattern, backend, str(ex)[:180])
                self.pause(small=True)
            # Also inspect only the public preview pages of usernames explicitly provided in the UI.
            stop_words = {"para", "com", "sem", "uma", "sobre", "videoaulas", "apostilas", "questoes", "questões", "provas", "gabaritos", "pdf"}
            terms = [t.lower() for t in re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", q) if t.lower() not in stop_words]
            for username in self.telegram_channels:
                if len(found) >= limit: break
                page_url = f"https://t.me/s/{username}"
                try:
                    resp = self.s.get(page_url, timeout=(10, 20), allow_redirects=True)
                    if resp.status_code == 404:
                        self._record_engine(q, label, username, page_url, "canal_nao_encontrado", "Canal público não encontrado ou sem prévia pública")
                        continue
                    resp.raise_for_status()
                    soup = BeautifulSoup(resp.text, "html.parser")
                    for wrap in soup.select(".tgme_widget_message_wrap"):
                        body = wrap.select_one(".tgme_widget_message_text")
                        text = body.get_text(" ", strip=True) if body else ""
                        if not text: continue
                        if terms and not any(term in text.lower() for term in terms): continue
                        post = wrap.select_one("a.tgme_widget_message_date[href]")
                        post_url = urljoin("https://t.me/", post.get("href", "")) if post else page_url
                        found[post_url] = (text[:220] or f"Publicação de {username}")
                        if len(found) >= limit: break
                except Exception as ex:
                    self.error("Telegram canal público", page_url, ex)
            count = 0
            for href, title in list(found.items())[:limit]:
                self.link(q, title, href, "Telegram público (resultado indexado/prévia de canal)")
                self._record_engine(q, label, title, href, "resultado")
                count += 1
            self._record_engine(q, label, "", "https://t.me/", "ok" if count else "sem_resultados",
                                f"{count} link(s) públicos; canais explícitos consultados={len(self.telegram_channels)}; busca não acessa chats privados")
            return count
        except Exception as ex:
            self._record_engine(q, label, "", "https://t.me/", "erro", str(ex))
            self.error("pesquisa:Telegram público", q, ex)
            return 0

    def _search_openverse(self, q: str, engine: dict):
        label = engine["label"]
        url = engine["url"].format(query=quote_plus(q))
        try:
            response = self.s.get(url, timeout=(12, 25))
            response.raise_for_status()
            data = response.json()
            count = 0
            for item in data.get("results", [])[:self.args.resultados_busca]:
                page = item.get("foreign_landing_url") or item.get("url") or ""
                title = item.get("title") or "Mídia licenciada"
                if valid_web(page):
                    self.link(q, title, page, f"mídia/licença verificar [{label}]")
                    self._record_engine(q, label, title, page, "resultado", f"licença informada: {item.get('license') or 'verificar na fonte'}")
                    asset_url = item.get("url") or ""
                    if getattr(self.args, "baixar_midias_publicas", False) and valid_web(asset_url):
                        self.download_media(asset_url, q, title)
                    count += 1
            self._record_engine(q, label, "", url, "ok" if count else "sem_resultados", f"{count} item(ns)")
            return count
        except Exception as ex:
            self._record_engine(q, label, "", url, "erro", str(ex))
            self.error(f"pesquisa:{label}", url, ex)
            return 0

    def _tor_proxy_ready(self):
        """Check the local SOCKS proxy once; never try .onion hosts without Tor."""
        if self._tor_proxy_checked:
            return self._tor_proxy_available
        self._tor_proxy_checked = True
        proxy = str(getattr(self.args, "tor_socks_url", "socks5h://127.0.0.1:9150") or "").strip()
        parsed = urlparse(proxy)
        if parsed.scheme not in ("socks5h", "socks5") or not parsed.hostname:
            self._tor_proxy_message = "Proxy SOCKS invalido. Use socks5h://127.0.0.1:9150 (Tor Browser) ou socks5h://127.0.0.1:9050 (servico Tor)."
            return False
        try:
            port = parsed.port
        except ValueError:
            port = None
        if not port:
            self._tor_proxy_message = "A URL SOCKS precisa informar uma porta, normalmente 9150 ou 9050."
            return False
        try:
            if importlib.util.find_spec("socks") is None:
                self._tor_proxy_message = "Suporte SOCKS ausente. Execute INSTALAR_SUPORTE_TOR_ONION_WINDOWS.bat ou instale requests[socks] no ambiente do SpiderFoot."
                return False
        except (ImportError, ValueError):
            self._tor_proxy_message = "Suporte SOCKS ausente. Instale requests[socks] no ambiente do SpiderFoot."
            return False
        try:
            with socket.create_connection((parsed.hostname, port), timeout=0.8):
                pass
        except OSError as ex:
            self._tor_proxy_message = f"Proxy Tor indisponivel em {parsed.hostname}:{port} ({ex}). Abra o Tor Browser ou inicie o servico Tor antes da coleta."
            return False
        self._tor_proxy_available = True
        self._tor_proxy_message = "Proxy SOCKS local acessivel; a disponibilidade dos servicos .onion ainda precisa ser verificada durante a consulta."
        return True

    def _detect_onion_search_form(self, soup, page_url: str):
        """Find a simple HTML search form without executing JavaScript."""
        query_names = {"q", "query", "search", "text", "term", "keyword", "keywords", "s"}
        excluded_types = {"hidden", "submit", "button", "password", "file", "checkbox", "radio", "reset"}
        for form in soup.find_all("form"):
            query_field = None
            for control in form.find_all(["input", "textarea"]):
                name = str(control.get("name") or "").strip()
                kind = str(control.get("type") or "text").lower()
                if name.lower() in query_names and kind not in excluded_types:
                    query_field = name
                    break
            if not query_field:
                continue
            fields = {}
            for control in form.find_all("input"):
                name = str(control.get("name") or "").strip()
                kind = str(control.get("type") or "text").lower()
                if name and kind == "hidden":
                    fields[name] = str(control.get("value") or "")
            return {
                "method": str(form.get("method") or "get").lower(),
                "action": urljoin(page_url, str(form.get("action") or page_url)),
                "field": query_field,
                "fields": fields,
            }
        return None

    def _extract_onion_results(self, q: str, label: str, response, seen: set[str]):
        """Record search results; never crawl or download a result hosted on .onion."""
        if "html" not in response.headers.get("Content-Type", "").lower():
            raise RuntimeError("O buscador .onion nao retornou HTML")
        if len(response.content) > 5_000_000:
            raise RuntimeError("Pagina .onion maior que o limite de seguranca")
        soup = BeautifulSoup(response.text, "html.parser")
        search_host = domain(response.url)
        count = 0
        ignored_titles = {"home", "inicio", "search", "buscar", "next", "previous", "login", "register", "about", "contact"}
        for anchor in soup.find_all("a", href=True):
            href = normurl(urljoin(response.url, str(anchor.get("href") or "")))
            title = anchor.get_text(" ", strip=True) or str(anchor.get("title") or "")
            if not valid_web(href) or not title or len(title) < 4 or href in seen:
                continue
            host = domain(href)
            if not host or host == search_host or host.endswith("." + search_host):
                continue
            if title.strip().lower() in ignored_titles:
                continue
            if href.lower().startswith(("javascript:", "mailto:", "data:")):
                continue
            seen.add(href)
            if host.endswith(".onion"):
                note = "link .onion encontrado; registrado sem acesso/download automatico"
                self.link(q, title, href, note + f" [{label}]")
                self._record_engine(q, label, title, href, "resultado_onion_registrado", note)
                count += 1
            else:
                if self._accept_engine_result(q, label, {"title": title, "href": href}):
                    count += 1
            if count >= self.args.resultados_busca:
                break
        return count

    def _search_onion_engine(self, q: str, engine_id: str, engine: dict):
        label = engine["label"]
        base = engine["url"]
        if not self._tor_proxy_ready():
            self._record_engine(q, label, "", base, "tor_proxy_indisponivel", self._tor_proxy_message)
            return 0
        proxy = str(getattr(self.args, "tor_socks_url", "socks5h://127.0.0.1:9150")).strip()
        proxies = {"http": proxy, "https": proxy}
        seen = set()
        responses = []
        try:
            config = self._onion_form_cache.get(engine_id)
            if config is None:
                home = None
                try:
                    home = self.s.get(base, proxies=proxies, timeout=(12, 35), allow_redirects=True)
                    responses.append(home)
                    home.raise_for_status()
                    if "html" not in home.headers.get("Content-Type", "").lower():
                        home = None
                    elif len(home.content) > 5_000_000:
                        home = None
                except Exception:
                    home = None
                if home is not None:
                    config = self._detect_onion_search_form(BeautifulSoup(home.text, "html.parser"), home.url)
                if config:
                    self._onion_form_cache[engine_id] = config
                else:
                    # Some sites do not expose a simple HTML form. Try a small set
                    # of common routes once, then cache the usable route for later queries.
                    candidates = [
                        {"method": "get", "action": base, "field": "q", "fields": {}},
                        {"method": "get", "action": urljoin(base, "search"), "field": "q", "fields": {}},
                        {"method": "get", "action": urljoin(base, "search/"), "field": "q", "fields": {}},
                    ]
                    chosen = None
                    last_good_response = None
                    last_count = 0
                    last_error = ""
                    for candidate in candidates:
                        params = dict(candidate["fields"])
                        params[candidate["field"]] = q
                        try:
                            response = self.s.get(candidate["action"], params=params, proxies=proxies,
                                                  timeout=(12, 35), allow_redirects=True)
                            responses.append(response)
                            response.raise_for_status()
                            if "html" not in response.headers.get("Content-Type", "").lower():
                                last_error = "O endpoint nao retornou HTML"
                                continue
                            if len(response.content) > 5_000_000:
                                last_error = "Pagina maior que o limite de seguranca"
                                continue
                            chosen = candidate
                            last_good_response = response
                            last_count = self._extract_onion_results(q, label, response, seen)
                            if last_count:
                                self._onion_form_cache[engine_id] = chosen
                                self._record_engine(q, label, "", response.url, "ok",
                                                    f"{last_count} resultado(s); rota de busca identificada automaticamente")
                                return last_count
                        except Exception as ex:
                            last_error = str(ex)
                            continue
                    if chosen is None or last_good_response is None:
                        raise RuntimeError("Nao foi possivel identificar uma rota de busca HTML. " + last_error)
                    self._onion_form_cache[engine_id] = chosen
                    self._record_engine(q, label, "", last_good_response.url, "sem_resultados",
                                        "Nenhum resultado extraido; rota generica guardada para as proximas consultas")
                    return last_count
            params = dict(config.get("fields", {}))
            params[config["field"]] = q
            if str(config.get("method", "get")).lower() == "post":
                response = self.s.post(config["action"], data=params, proxies=proxies,
                                       timeout=(12, 35), allow_redirects=True)
            else:
                response = self.s.get(config["action"], params=params, proxies=proxies,
                                      timeout=(12, 35), allow_redirects=True)
            responses.append(response)
            response.raise_for_status()
            count = self._extract_onion_results(q, label, response, seen)
            status = "ok" if count else "sem_resultados"
            self._record_engine(q, label, "", response.url, status,
                                f"{count} resultado(s); consulta via proxy Tor; links .onion nao sao abertos automaticamente")
            return count
        except Exception as ex:
            detail = str(ex)
            if "Missing dependencies for SOCKS support" in detail or "SOCKS support" in detail:
                detail += " | Instale requests[socks] no ambiente Python usado pelo SpiderFoot."
            self._record_engine(q, label, "", base, "erro_ou_bloqueio", detail)
            self.error(f"pesquisa:{label}", base, detail)
            return 0
        finally:
            for response in responses:
                try:
                    response.close()
                except Exception:
                    pass

    def _search_site_source(self, q: str, engine_id: str, engine: dict):
        """Busca indexada restrita a um portal; sem burlar login ou bloqueios.

        'site:' e um filtro do mecanismo DDGS, nao uma API publica dos portais.
        Cada resultado e ainda validado contra o host permitido antes de ser aberto.
        """
        label = engine["label"]
        site = engine["site"]
        q_site = f"site:{site} {q}"
        count = 0
        try:
            from ddgs import DDGS
            found = DDGS(timeout=20).text(
                q_site, region="br-pt", max_results=self.args.resultados_busca,
                backend="auto")
            allowed_host = engine.get("allowed_host")
            allowed_path = engine.get("allowed_path", "")
            for item in found or []:
                if not isinstance(item, dict):
                    continue
                url = normurl(str(item.get("href") or item.get("url") or ""))
                if not valid_web(url):
                    continue
                host = domain(url)
                if allowed_host:
                    if host != allowed_host or not urlparse(url).path.startswith(allowed_path):
                        continue
                elif host != site and not host.endswith("." + site):
                    continue
                if self._accept_engine_result(q, label, item):
                    count += 1
            self._record_engine(q, label, "", "", "ok" if count else "sem_resultados",
                                f"{count} resultado(s) restrito(s) ao dominio: {site}")
        except Exception as ex:
            self._record_engine(q, label, "", "", "erro", str(ex))
            self.error(f"pesquisa:{label}", q_site, ex)
        # O repositorio oficial possui paginas anuais navegaveis. Este acesso
        # direto independe de indexacao por buscadores externos; execute uma
        # unica vez por pagina durante a sessao e preserve a deduplicacao.
        if engine.get("official") and not self.reached_limit():
            index = engine["index"]
            years = re.findall(r"(?<!\d)(?:19\d{2}|20\d{2})(?!\d)", q)
            years = [int(y) for y in years if 1998 <= int(y) <= 2025]
            target = f"{index}/{years[0]}" if years else index
            if target not in self.pages_scanned:
                self.link(q, label, target, "catalogo oficial INEP")
                self.explore_page(target, q, label, official=True)
        return count

    def _search_searxng(self, q: str, engine: dict):
        label = engine["label"]
        base = (self.args.searxng_url or "").strip().rstrip("/")
        if not base:
            self._record_engine(q, label, "", "", "configuracao_necessaria", "Informe uma URL de instância SearXNG; docs.searxng.org é documentação, não um buscador.")
            return 0
        if not valid_web(base):
            self._record_engine(q, label, "", base, "configuracao_invalida", "A URL da instância deve começar com http:// ou https://")
            return 0
        url = base + "/search" + "?" + urlencode({"q": q, "format": "json"})
        try:
            response = self.s.get(url, timeout=(12, 25))
            response.raise_for_status()
            data = response.json()
            count = 0
            for item in data.get("results", [])[:self.args.resultados_busca]:
                if self._accept_engine_result(q, label, item):
                    count += 1
            self._record_engine(q, label, "", url, "ok" if count else "sem_resultados", f"{count} resultado(s)")
            return count
        except Exception as ex:
            self._record_engine(q, label, "", url, "erro_ou_json_desabilitado", str(ex))
            self.error(f"pesquisa:{label}", url, ex)
            return 0

    def _mark_engine_done(self, query: str, engine_id: str):
        pair = (query, engine_id)
        if pair in self.done_engine_pairs:
            return
        append_csv(self.done_engine_path, ["data", "pesquisa", "mecanismo"], [timestamp(), query, engine_id])
        self.done_engine_pairs.add(pair)

    def search_engines(self, q: str, preferred_engine=None, only_engine=None):
        total = 0
        available = list(self.args.mecanismos)
        root_engine = self._current_engine_id
        if only_engine:
            available = [only_engine] if only_engine in available else []
        elif preferred_engine in available:
            available.remove(preferred_engine)
            available.insert(0, preferred_engine)
        for engine_id in available:
            previous_engine = self._current_engine_id
            self._current_engine_id = engine_id
            # A opção explícita de repetição ignora o marcador histórico de sucesso.
            # Os resultados anteriores permanecem no CSV; esta opção apenas força
            # uma nova consulta aos mecanismos selecionados.
            if (q, engine_id) in self.done_engine_pairs and not (getattr(self.args, "repetir_concluidas", False) or self._followup_force):
                logging.info("MECANISMO PULADO %s | consulta já concluída com sucesso anteriormente; habilite --repetir-concluidas para tentar novamente", engine_id)
                continue
            engine = ENGINE_CATALOG.get(engine_id)
            if not engine:
                logging.warning("MECANISMO IGNORADO %s | ID não reconhecido", engine_id)
                continue
            kind = engine["kind"]
            label = engine["label"]
            self.engine_last_status[(q, label)] = None
            before = total
            # Atingir o teto em GB desativa apenas as buscas de vídeos,
            # nunca os mecanismos gerais que encontram PDFs/ZIPs/imagens.
            if kind == "youtube" and not self._video_search_allowed():
                logging.info("YOUTUBE IGNORADO APOS TETO DE VÍDEOS; %s permanece para próxima execução", q)
                continue
            if kind == "ddgs":
                total += self._search_ddgs_backend(q, engine_id, engine)
            elif kind == "html":
                total += self._search_html_engine(q, engine_id, engine)
            elif kind == "youtube":
                total += self._search_youtube(q, engine)
            elif kind == "telegram":
                total += self._search_telegram_public(q, engine)
            elif kind == "openverse":
                total += self._search_openverse(q, engine)
            elif kind == "site_source":
                total += self._search_site_source(q, engine_id, engine)
            elif kind == "searxng":
                total += self._search_searxng(q, engine)
            elif kind == "onion":
                total += self._search_onion_engine(q, engine_id, engine)
            elif kind in ("special", "inactive"):
                self._record_engine(q, label, "", "", "nao_executado", engine.get("note", "Fonte requer integração especializada"))
            status = self.engine_last_status.get((q, label), "sem_registro")
            accepted = total - before
            logging.info("MECANISMO %s | status=%s | resultados_aceitos=%d", label, status, accepted)
            # Retry errors, blocked sources, unparseable HTML and sources requiring
            # configuration on a future run. Do not falsely call them completed.
            if status in ("ok", "sem_resultados"):
                self._mark_engine_done(q, engine_id)
            else:
                self.done_engine_pairs.discard((q, engine_id))
            self.pause(small=True)
            self._current_engine_id = previous_engine
        self._current_engine_id = root_engine
        return total

    def collect_query(self, q: str):
        # O limite de arquivos limita downloads, não pesquisas. Todas as fontes
        # selecionadas continuam sendo consultadas mesmo após atingir esse limite.
        logging.info("PESQUISA %s | mecanismos: %s", q, ",".join(self.args.mecanismos))
        try:
            pending = [e for e in self.args.mecanismos if (q, e) not in self.done_engine_pairs]
            count = self.search_engines(q)
            if count == 0 and pending:
                logging.warning("Nenhum resultado válido foi extraído para: %s", q)
            with self.done_queries_path.open("a", encoding="utf-8") as f:
                f.write(q + "\n")
            self.done_queries.add(q)
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            self.error("pesquisa", q, ex)
        self.pause()


def parse():
    p = argparse.ArgumentParser(description="Pesquisa geral e educacional; registra links e baixa documentos/mídias públicas autorizadas")
    p.add_argument("--modo", choices=["personalizadas"], default="personalizadas", help="Apenas consultas personalizadas")
    p.add_argument("--buscar-videos-relacionados", action="store_true", help="Ao terminar um vídeo autorizado, procurar imediatamente próximos episódios e vídeos relacionados")
    p.add_argument("--max-buscas-relacionadas", type=int, default=12, help="Máximo global de consultas extras por execução (0 desativa, 100 máximo)")
    p.add_argument("--refazer-downloads-antigos", action="store_true", help="Permite baixar novamente videos registrados no historico caso ja tenham sido excluidos; nao altera documentos")
    p.add_argument("--buscar-documentos-relacionados", action="store_true", help="Busca encadeada de documentos apos cada download concluido; prioriza fonte original")
    p.add_argument("--max-buscas-documentos", type=int, default=960, help="Maximo de pesquisas de documentos geradas por execucao (0 desativa, 1200 maximo)")
    p.add_argument("--telegram-video-modo", choices=("desativado", "compactar", "dividir"), default="desativado", help="Prepara copias de videos grandes para Telegram sem apagar os originais")
    p.add_argument("--telegram-video-limite-gb", type=float, default=4.0, help="Tamanho a partir do qual a preparacao sera aplicada, GB decimais")
    p.add_argument("--telegram-video-saida-gb", type=float, default=3.5, help="Limite em GB decimais de cada arquivo final gerado")
    p.add_argument("--limites-por-tipo-gb", default="{}", help='JSON de teto independente por extensao/grupo, ex: {".pdf":3,".zip":2,"audio":1}; 0=ilimitado')
    p.add_argument("--limite-total-gb", type=float, default=0.0, help="Limite apenas de downloads de VIDEO por execucao, em GB decimais (0 = ilimitado); documentos/audio/imagens continuam")
    p.add_argument("--continuar-busca-encadeada", action="store_true", help="Ao atingir o limite de videos em GB, continuar APENAS buscas encadeadas, SEM baixar videos; demais arquivos continuam")
    p.add_argument("--limite-pesquisas", type=int, default=0, help="0 = todas as consultas do modo selecionado")
    p.add_argument("--limite-arquivos", type=int, default=0, help="Maximo de novos arquivos por execucao; 0 = sem limite")
    p.add_argument("--tamanho-max-mb", type=int, default=45, help="Tamanho maximo por documento")
    p.add_argument("--tamanho-max-midia-mb", type=int, default=0, help="Tamanho maximo individual de arquivo de midia em MB; 0 = sem limite individual (tetos em GB continuam)")
    p.add_argument("--resultados-busca", type=int, default=6)
    p.add_argument("--paginas-por-pesquisa", type=int, default=2)
    p.add_argument("--max-links-pagina", type=int, default=5)
    p.add_argument("--pausa", type=float, default=2.0, help="Segundos de intervalo entre buscas")
    p.add_argument("--mecanismos", default=",".join(DEFAULT_ENGINES), help="IDs separados por vírgula. Consulte --listar-mecanismos")
    p.add_argument("--searxng-url", default="", help="URL base de uma instância SearXNG; não use docs.searxng.org")
    p.add_argument("--tor-socks-url", default="socks5h://127.0.0.1:9150", help="Proxy SOCKS5 do Tor; use socks5h://127.0.0.1:9150 (Tor Browser) ou porta 9050 para servico Tor")
    p.add_argument("--consultas-arquivo", default="", help="Arquivo UTF-8 com consultas adicionais, uma por linha")
    p.add_argument("--telegram-canais-arquivo", default="", help="Arquivo com usernames de canais públicos do Telegram, um por linha; sem convites privados")
    p.add_argument("--baixar-videos-youtube", action="store_true", help="Baixa vídeos do YouTube apenas quando o usuário confirma ter licença/autorização para baixar")
    p.add_argument("--baixar-midias-publicas", action="store_true", help="Baixa links diretos de vídeo/áudio/imagem e plataformas compatíveis somente de páginas públicas quando o usuário tem autorização/licença; não contorna login, DRM ou paywall")
    p.add_argument("--repetir-concluidas", action="store_true", help="Executa novamente todos os mecanismos selecionados, mesmo para consultas marcadas como concluídas; preserva os resultados anteriores")
    p.add_argument("--listar-mecanismos", action="store_true", help="Lista mecanismos integrados e encerra")
    p.add_argument("--ano-inicial", type=int, default=1998)
    p.add_argument("--ano-final", type=int, default=2026)
    return p.parse_args()


def main():
    args = parse()
    if args.listar_mecanismos:
        for engine_id, info in ENGINE_CATALOG.items():
            print(f"{engine_id}\t{info['label']}\t{info['kind']}\t{'padrão' if info.get('default') else 'opcional'}")
        return
    chosen = [x.strip() for x in args.mecanismos.split(",") if x.strip()]
    unknown = [x for x in chosen if x not in ENGINE_CATALOG]
    if unknown:
        raise SystemExit("Mecanismos desconhecidos: " + ", ".join(unknown))
    # preserve order, remove duplicates
    args.mecanismos = list(dict.fromkeys(chosen))
    if not args.mecanismos:
        raise SystemExit("Selecione ao menos um mecanismo com --mecanismos")
    if args.ano_inicial > args.ano_final or args.tamanho_max_mb <= 0 or args.tamanho_max_midia_mb < 0 or args.tamanho_max_midia_mb > 1048576 or args.limite_arquivos < 0 or args.limite_pesquisas < 0 or not 0 <= args.max_buscas_relacionadas <= 100 or not 0 <= args.max_buscas_documentos <= 1200 or not 0 <= args.limite_total_gb <= 10000:
        raise SystemExit("Parametros invalidos; consulte --help")
    from video_telegram_v39 import size_bytes
    try:
        if size_bytes(args.telegram_video_saida_gb) >= size_bytes(args.telegram_video_limite_gb):
            raise ValueError("tamanho de saida deve ser inferior ao limite de acionamento")
    except ValueError as ex:
        raise SystemExit("Parametros de videos Telegram invalidos: " + str(ex))
    OUT.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s", handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(OUT / "execucao.log", encoding="utf-8")])
    queries = []   # Não carregar pesquisas gerais / biblioteca / portais oficiais.
    general_queries = []
    custom_queries = []
    if args.consultas_arquivo:
        custom_path = Path(args.consultas_arquivo).expanduser().resolve()
        if custom_path.exists():
            custom_queries = load_queries(custom_path)
        else:
            logging.warning("Arquivo de consultas personalizadas não encontrado: %s", custom_path)
    queries = build_query_list("personalizadas", [], [], custom_queries)
    collector = Collector(args)
    logging.info("Iniciando: %s | %d pesquisas unicas (%d personalizadas) | max. arquivos (global): %s | teto somente videos %.3f GB | outros downloads continuam apos teto de video | refazer videos antigos=%s | encadear apos teto=%s", args.modo, len(queries), len(custom_queries), args.limite_arquivos or "sem limite", args.limite_total_gb, args.refazer_downloads_antigos, args.continuar_busca_encadeada)
    try:
        selected = queries[:args.limite_pesquisas] if args.limite_pesquisas else queries
        for i, q in enumerate(selected, 1):
            collector._sync_limit_resets()
            logging.info("Progresso: pesquisa personalizada %d/%d", i, len(selected))
            collector.collect_query(q)
    except KeyboardInterrupt:
        logging.info("Interrompido pelo usuario; progresso salvo. Rode novamente para continuar.")
    logging.info("Terminado. Novos arquivos baixados: %d. Consulte a pasta resultados/", collector.downloaded)


if __name__ == "__main__":
    main()