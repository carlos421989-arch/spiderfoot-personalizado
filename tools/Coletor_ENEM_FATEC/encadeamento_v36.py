"""V36 - Geracao limitada de pesquisas associadas a arquivos concluídos.

Nenhum acesso à internet neste módulo. O coletor decide onde pesquisar e baixar.
"""
from __future__ import annotations
import re
import unicodedata

CORES = ("azul", "amarelo", "branco", "rosa", "cinza", "verde", "laranja", "roxo")
DOC_KINDS = ("prova", "gabarito")
VIDEO_EXTS = {".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wav", ".flac"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif", ".bmp", ".tif", ".tiff"}


def norm(value):
    return unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode('ascii').lower()


def group_for_extension(extension: str) -> str:
    ext = str(extension).lower()
    if ext in VIDEO_EXTS: return 'video'
    if ext in AUDIO_EXTS: return 'audio'
    if ext in IMAGE_EXTS: return 'imagem'
    if ext in {'.pdf', '.txt', '.doc', '.docx', '.zip', '.ppt', '.pptx', '.xls', '.xlsx', '.odt', '.ods'}:
        return ext
    return 'outros'


def detect_seed(title: str, query: str, extension: str, year_start=1998, year_end=2026):
    """Retorna contexto somente quando o tema foi identificado com evidência.

    Evita usar consultas sem relação para gerar ENEM automaticamente.
    """
    source = f'{title} {query}'
    n = norm(source)
    exam = 'ENEM' if re.search(r'\benem\b', n) else 'FATEC' if re.search(r'\bfatec\b', n) else None
    years = re.findall(r'(?<!\d)(?:19\d\d|20\d\d)(?!\d)', n)
    year = next((int(y) for y in years if int(year_start) <= int(y) <= int(year_end)), None)
    color = next((c for c in CORES if re.search(r'\b' + c + r'\b', n) or (c == 'amarelo' and re.search(r'\bamarela\b', n)) or (c == 'branco' and re.search(r'\bbranca\b', n)) or (c == 'roxo' and re.search(r'\broxa\b', n))), None)
    dm = re.search(r'\b(?:dia|d)\s*[-_:]?\s*([12])\b', n)
    day = int(dm.group(1)) if dm else 1
    kind = 'gabarito' if 'gabarito' in n else 'prova' if re.search(r'\bprovas?\b|\bcaderno\b', n) else None
    return {'exam': exam, 'year': year, 'color': color, 'day': day, 'kind': kind,
            'query': str(query).strip(), 'title': str(title).strip(), 'extension': extension}


def chained_document_queries(title: str, query: str, extension: str,
                             year_start=1998, year_end=2026, cap=960):
    """Gera dia 2 primeiro, seguida de cores e anos. Cap 0 desativa.

    Fontes reais só serão consultadas pelo coletor em modo autorizado.
    """
    max_count = max(0, min(int(cap), 1200))
    if not max_count: return []
    seed = detect_seed(title, query, extension, year_start, year_end)
    results, seen = [], set()
    def add(q, year=None, color=None, day=None, kind=None):
        key = norm(q).strip()
        if key and key not in seen and len(results) < max_count:
            seen.add(key)
            results.append((q, {'exam': seed['exam'], 'year': year, 'color': color, 'day': day, 'kind': kind}))
    exam, seed_year = seed['exam'], seed['year']
    if exam and seed_year is not None:
        # Para ENEM: primeiro completar dia 2 da cor inicial, com prova e gabarito.
        colors = ([seed['color']] if seed['color'] else ['amarelo'])
        colors += [c for c in CORES if c not in colors] if exam == 'ENEM' else []
        if exam != 'ENEM':
            colors = [None]  # FATEC geralmente não separa caderno por cor.
        def emit(y, col, days):
            for d in days:
                for kind in DOC_KINDS:
                    terms = f'{exam} {y} dia {d} '
                    if col: terms += f'caderno {col} '
                    add(terms + kind, y, col, d, kind)
        emit(seed_year, colors[0], [2, 1] if seed['day'] == 1 else [1, 2])
        for c in colors[1:]:
            emit(seed_year, c, [1, 2])
        # Anos seguintes primeiro, depois anteriores, até o ano final fornecido.
        ordered_years = list(range(seed_year + 1, int(year_end) + 1)) + list(range(int(year_start), seed_year))
        for year in ordered_years:
            for c in colors:
                emit(year, c, [1, 2])
                if len(results) >= max_count: return results
        return results

    # Para outros assuntos não inventar disciplinas/anos: variantes do título.
    text = re.sub(r'\s+', ' ', title or query).strip()[:100]
    n = norm(text)
    m = re.search(r'\b(?:aula|aulao|parte|capitulo|modulo|volume|vol|episodio|ep)\s*(\d{1,3})\b', n)
    if m:
        num = int(m.group(1))
        if num < 999:
            add(re.sub(r'(?i)(\b(?:aula|aul[aã]o|parte|cap[ií]tulo|m[oó]dulo|volume|vol|epis[oó]dio|ep)\s*)\d{1,3}\b',
                       lambda x: x.group(1) + str(num + 1), text, count=1))
    if re.search(r'\bprovas?\b', n):
        add(re.sub(r'(?i)\bprovas?\b', 'gabarito', text, count=1))
    elif 'gabarito' in n:
        add(re.sub(r'(?i)\bgabarito\b', 'prova', text, count=1))
    if 'parte 1' in n or 'aula 1' in n:
        add(text + ' parte 2')
    return results[:max_count]


def matches_document_context(title: str, url: str, context: dict | None) -> bool:
    """Rejeita divergência explícita sem obrigar metadados ausentes.

    Não deve confundir nomes gerados a partir da consulta com evidência.
    """
    if not context: return True
    n = norm(str(title) + ' ' + str(url))
    if context.get('exam') and re.search(r'\b(enem|fatec)\b', n):
        if norm(context['exam']) not in n: return False
    years = {int(x) for x in re.findall(r'(?<!\d)(?:19\d\d|20\d\d)(?!\d)', n)}
    if context.get('year') is not None and years and int(context['year']) not in years:
        return False
    seen_colors = [c for c in CORES if re.search(r'\b' + c + r'\b', n) or (c == 'amarelo' and 'amarela' in n) or (c == 'branco' and re.search(r'\bbranca\b', n)) or (c == 'roxo' and re.search(r'\broxa\b', n))]
    if context.get('color') and seen_colors and context['color'] not in seen_colors:
        return False
    days = {int(x) for x in re.findall(r'\b(?:dia|d)\s*[-_:]?\s*([12])\b', n)}
    if context.get('day') and days and context['day'] not in days:
        return False
    return True
