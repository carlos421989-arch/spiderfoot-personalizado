#!/usr/bin/env python3
"""Leitor conservador de documentos educacionais baixados.

Prioriza texto do documento, nao depende da pesquisa para atribuir cor/caderno.
PDF: pypdf/PyMuPDF; OCR opcional via PyMuPDF + Tesseract somente quando necessario.
Arquivos binarios legados nao suportados sao marcados para revisao, nao adivinhados.
"""
from __future__ import annotations
import io
import os
import re
import shutil
import subprocess
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree as ET

MAX_CHARS = 24000
MAX_ZIP_XML = 3_000_000
COLORS = ("Azul", "Amarelo", "Branco", "Verde", "Rosa", "Cinza", "Laranja", "Roxo")
# Os cadernos podem aparecer como "AMARELO" e as provas historicas como "AMARELA".
# Guardamos a forma canonica, mas reconhecemos as duas grafias no conteudo.
COLOR_ALIASES = {
    'azul': 'Azul', 'azuis': 'Azul',
    'amarelo': 'Amarelo', 'amarela': 'Amarelo',
    'branco': 'Branco', 'branca': 'Branco',
    'verde': 'Verde', 'verdes': 'Verde',
    'rosa': 'Rosa', 'rosado': 'Rosa',
    'cinza': 'Cinza', 'cinzento': 'Cinza', 'cinzenta': 'Cinza',
    'laranja': 'Laranja',
    'roxo': 'Roxo', 'roxa': 'Roxo',
}
COLOR_PATTERN = '|'.join(sorted(COLOR_ALIASES, key=len, reverse=True))

def canonical_color(value: str) -> str:
    return COLOR_ALIASES.get(fold(value).strip(), '')

KINDS = ("01_Provas", "02_Gabaritos", "03_Resolucoes_Comentadas", "04_Apostilas_Resumos",
         "05_Questoes_Simulados", "06_Videoaulas_Cursos", "07_Editais_Matrizes", "08_Outros")
DISCIPLINES = [
 ("Matematica", r"matematica|geometria|algebra|trigonometria|estatistica|probabilidade|funcoes"),
 ("Portugues_Redacao", r"lingua portuguesa|portugues|redacao|gramatica|literatura"),
 ("Biologia", r"biologia|genetica|ecologia|citologia"),
 ("Quimica", r"quimica|estequiometria"),
 ("Fisica", r"fisica|cinematica|termodinamica"),
 ("Historia", r"historia"),
 ("Geografia", r"geografia|geopolitica"),
 ("Filosofia_Sociologia", r"filosofia|sociologia"),
 ("Ingles_Espanhol", r"ingles|espanhol"),
 ("Interdisciplinares", r"interdisciplinar"),
 ("Tecnicas_de_Estudo", r"cronograma|tecnica de estudo|organizacao de estudo"),
]
TOPICS = [
 ("Funcoes", r"\bfuncoes\b|funcao quadratica|funcao afim"),
 ("Geometria", r"geometria"), ("Estatistica", r"estatistica"),
 ("Probabilidade", r"probabilidade"), ("Redacao", r"redacao"),
 ("Gramatica", r"gramatica"), ("Literatura", r"literatura"),
 ("Genetica", r"genetica"), ("Ecologia", r"ecologia"),
 ("Mecanica", r"mecanica|cinematica"), ("Eletricidade", r"eletricidade"),
 ("Quimica_Organica", r"quimica organica"),
]


def fold(value: str) -> str:
    return unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()


def clean_filename(value: str, maxlen: int = 135) -> str:
    s = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', s)
    s = re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip(" ._-")
    s = re.sub(r"_+", "_", s)
    return s[:maxlen].strip("._-") or "Documento"


def _xml_text(data: bytes) -> str:
    try:
        root = ET.fromstring(data)
        text = [x.text for x in root.iter() if x.tag.rsplit('}',1)[-1] in ('t','v','p','text') and x.text]
        return ' '.join(text)
    except (ET.ParseError, ValueError):
        return ''


def read_openxml(file: Path) -> tuple[str, str]:
    output = []
    try:
        with zipfile.ZipFile(file) as z:
            entries = z.infolist()
            if file.suffix.lower() == '.docx':
                candidates = [a for a in entries if re.fullmatch(r'word/(?:document|header\d*|footer\d*)\.xml', a.filename)]
            elif file.suffix.lower() == '.pptx':
                candidates = [a for a in entries if re.fullmatch(r'ppt/slides/slide\d+\.xml', a.filename)]
            elif file.suffix.lower() == '.xlsx':
                candidates = [a for a in entries if a.filename == 'xl/sharedStrings.xml' or re.fullmatch(r'xl/worksheets/sheet\d+\.xml', a.filename)]
            else:
                candidates = [a for a in entries if a.filename in ('content.xml','meta.xml')]
            for entry in candidates[:16]:
                if entry.file_size > MAX_ZIP_XML: continue
                output.append(_xml_text(z.read(entry)))
                if sum(map(len,output)) >= MAX_CHARS: break
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        return '', f'erro_office:{type(exc).__name__}'
    return ' '.join(output)[:MAX_CHARS], 'xml_office'


def read_plain(file: Path) -> tuple[str,str]:
    try:
        data = file.read_bytes()[:MAX_CHARS*3]
        for enc in ('utf-8-sig', 'cp1252', 'latin-1'):
            try:
                return data.decode(enc)[:MAX_CHARS], f'texto:{enc}'
            except UnicodeDecodeError:
                pass
    except OSError:
        pass
    return '', 'sem_leitura'


def read_legacy(file: Path) -> tuple[str, str]:
    cmd = {'doc':'antiword','xls':'xls2csv','ppt':'catppt'}.get(file.suffix.lstrip('.').lower())
    if cmd and shutil.which(cmd):
        try:
            out = subprocess.run([cmd,str(file)], timeout=18, capture_output=True, check=False)
            if out.returncode == 0:
                return out.stdout.decode('utf-8',errors='replace')[:MAX_CHARS], f'legado:{cmd}'
        except (OSError, subprocess.TimeoutExpired):
            pass
    return '', 'legado_sem_extrator'


def read_zip(file:Path) -> tuple[str,str]:
    """Lista somente nomes/pequenos textos; nao extrai nem executa ZIPs."""
    try:
        with zipfile.ZipFile(file) as z:
            entries = [i for i in z.infolist() if not i.is_dir()][:100]
            names = ' '.join(i.filename for i in entries)[:15000]
            small = []
            for i in entries:
                if i.filename.lower().endswith(('.txt','.md','.csv')) and 0 < i.file_size < 30_000:
                    small.append(z.read(i).decode('utf-8', errors='replace')[:1500])
            return (names + ' ' + ' '.join(small))[:MAX_CHARS], 'zip_listagem'
    except (OSError, RuntimeError, zipfile.BadZipFile):
        return '', 'zip_ilegivel'


def _summarize_pdf_pages(page_reader, pages: int) -> str:
    """Inspeciona uma amostra limitada: capa, paginas proximas, amostras internas e final.

    Evita que PDFs muito longos tornem a simulacao impraticavel. Para arquivos com
    ate 40 paginas, todas as paginas sao verificadas; acima disso, limita a amostra.
    """
    sections=[]
    indicator=re.compile(r'\b(enem|fatec|vestibular|gabarit|caderno|prova|semestre|reaplic|ppl|\d\s*[ºo]?\s*dia|aplicacao)\b',re.I)
    if pages <= 40:
        indices=list(range(pages))
    else:
        indices=set(range(min(4,pages)))
        indices.update(range(max(0,pages-2),pages))
        # Amostra interna distribuida; total de paginas lidas fica <= 28.
        step=max(1, pages // 22)
        indices.update(range(4, max(4,pages-2), step))
        indices=sorted(indices)[:28]
    remaining=MAX_CHARS
    for i in indices:
        try: txt=page_reader(i) or ''
        except Exception: txt=''
        if i<3 or i>=pages-2:
            excerpt=txt[:4800]
        else:
            lines=txt.splitlines()
            excerpt='\n'.join(line[:200] for line in lines if indicator.search(fold(line)))[:850]
        if remaining>0 and excerpt:
            segment=excerpt[:remaining]
            sections.append(segment)
            remaining-=len(segment)
        if remaining <= 0: break
    return '\n'.join(sections)[:MAX_CHARS]


def pdf_text(file: Path, ocr: bool=True) -> tuple[str, str, int]:
    """Percorre as paginas digitais; OCR parcial quando o PDF e imagem."""
    try:
        import fitz
        with fitz.open(file) as doc:
            pages=len(doc)
            if pages==0: return '', 'pdf_vazio',0
            txt=_summarize_pdf_pages(lambda i: doc[i].get_text('text',sort=True),pages)
            if len(re.sub(r'\W','',txt))>25 or not ocr:
                return txt,'pdf_texto_paginas_inspecionadas',pages
            if shutil.which('tesseract'):
                results=[]
                indices=list(dict.fromkeys([0,1,2,pages-1]))
                for i in [x for x in indices if x<pages]:
                    try:
                        pix=doc[i].get_pixmap(matrix=fitz.Matrix(1.7,1.7),alpha=False)
                        b=subprocess.run(['tesseract','stdin','stdout','-l','por+eng'],input=pix.tobytes('png'),
                                         timeout=40,capture_output=True,check=False)
                        if b.returncode:
                            b=subprocess.run(['tesseract','stdin','stdout','-l','eng'],input=pix.tobytes('png'),
                                             timeout=40,capture_output=True,check=False)
                        if b.returncode==0: results.append(b.stdout.decode('utf-8',errors='replace'))
                    except (subprocess.TimeoutExpired,OSError,RuntimeError): continue
                if results:
                    return ' '.join(results)[:MAX_CHARS],'pdf_ocr_amostrado',pages
            return txt,'pdf_sem_texto_ocr_indisponivel',pages
    except (ImportError,OSError,ValueError,RuntimeError):
        pass
    try:
        from pypdf import PdfReader
        r=PdfReader(str(file),strict=False)
        pages=len(r.pages)
        txt=_summarize_pdf_pages(lambda i:r.pages[i].extract_text() or '',pages)
        if len(re.sub(r'\W','',txt))<25:
            return txt,'pdf_sem_texto_ocr_indisponivel',pages
        return txt,'pdf_pypdf_paginas_inspecionadas',pages
    except Exception as exc:
        return '',f'pdf_erro:{type(exc).__name__}',0


def extract_text(file: Path, ocr=True) -> tuple[str,str,int]:
    ext = file.suffix.lower()
    if ext == '.pdf': return pdf_text(file, ocr)
    if ext in ('.docx','.pptx','.xlsx','.odt','.ods'):
        t,m = read_openxml(file); return t,m,0
    if ext in ('.txt','.md','.csv','.json','.html','.htm'):
        t,m=read_plain(file); return t,m,0
    if ext in ('.doc','.ppt','.xls'):
        t,m=read_legacy(file); return t,m,0
    if ext=='.zip':
        t,m=read_zip(file); return t,m,0
    return '', 'formato_nao_suportado',0


def _first(pattern: str, s: str) -> str:
    m=re.search(pattern,s,flags=re.I)
    return m.group(1) if m else ''


def extract_year(*texts: str) -> str:
    for s in texts:
        if not s: continue
        # Preferencias com contexto: ano no cabecalho junto ao exame.
        patterns=(r'\b(?:enem|fatec|vestibular|exame nacional)[^\n]{0,75}?\b(20\d{2})\b',
                  r'\b(20\d{2})\s*[/_-]?\s*(?:[12]\s*[sºo]?\s*)?(?:enem|fatec|vestibular)\b',
                  r'\b(20\d{2})\b')
        for pat in patterns:
            ys=re.findall(pat,fold(s), re.I)
            ys=[y for y in ys if 2000<=int(y)<=2035]
            if ys: return ys[0]
    return ''


def detect_kind(header: str, filename: str, title: str) -> str:
    """Classifica o tipo sem deixar menções incidentais na capa sobrepor o nome explícito."""
    patterns=[
        ('02_Gabaritos',r'\bgabarit|answer[ -]?key|respostas oficiais'),
        ('03_Resolucoes_Comentadas',r'resolucao comentad|resolucao passo a passo|questoes comentad|comentarios das questoes'),
        ('07_Editais_Matrizes',r'\bedital\b|matriz de referencia'),
        ('04_Apostilas_Resumos',r'\bapostila\b|\bresumo\b|mapa mental'),
        ('05_Questoes_Simulados',r'\bsimulado\b|lista de exercicios|banco de questoes'),
        ('06_Videoaulas_Cursos',r'\bvideoaula|\bcurso\b|\baula\b'),
        ('01_Provas',r'\bprova\b|caderno de questoes|\bcaderno\s*(?:\d+|[a-z]+)\b'),
    ]
    header=fold(header[:1800]); filename=fold(filename); title=fold(title)

    def first_hit(value):
        hits=[]
        for kind,pat in patterns:
            for m in re.finditer(pat,value):
                hits.append((m.start(),kind,m.group(0)))
        return min(hits,key=lambda x:x[0]) if hits else None

    # Nome real do arquivo é evidência forte: GABARITO_* nunca deve virar PROVA
    # só porque o PDF menciona "PROVA 1 - AMARELA" em uma referência interna.
    file_hit=first_hit(filename)
    if file_hit:
        return file_hit[1]

    # No título, só sobrepor o conteúdo quando houver uma indicação inequívoca.
    title_fold=title.strip()
    if re.search(r'\b(gabarito|gabaritos|answer[ -]?key|respostas oficiais)\b',title_fold) and not re.search(r'\b(provas?\s+e\s+gabaritos?|materiais?)\b',title_fold):
        return '02_Gabaritos'
    if re.search(r'\b(edital|matriz de referencia)\b',title_fold):
        return '07_Editais_Matrizes'
    if re.search(r'\b( apostila|resumo|mapa mental)\b', ' '+title_fold):
        return '04_Apostilas_Resumos'

    # Compare as evidências da capa. Uma menção a gabarito depois da identificação
    # explícita da prova não deve mudar o tipo; se gabarito aparece primeiro, prevalece.
    header_hit=first_hit(header)
    title_hit=first_hit(title)
    if header_hit:
        return header_hit[1]
    if title_hit:
        return title_hit[1]
    return '08_Outros'


def detect_colors(text: str, kind: str) -> tuple[list[str], str]:
    """Identifica cores da CAPA (inclusive genero feminino), sem extrapolar gabaritos."""
    s=fold(text[:13000]); group=COLOR_PATTERN
    explicit=[]
    # PROVA 1 - AMARELA; CADERNO 2 - AMARELO; GABARITO - BRANCO.
    pat=rf'\b(?:caderno(?:\s+de\s+questoes)?|prova|gabarito)\s*(?:n[.oº°]*\s*)?\d{{0,2}}\s*[-–—:/ ]{{0,8}}\s*({group})\b'
    for match in re.finditer(pat,s):
        val=canonical_color(match.group(1))
        if val and val not in explicit: explicit.append(val)
    # Variantes de texto: "cor da prova: AMARELA", "cor do caderno AMARELO".
    pat_color=rf'\bcor\s+d[ao]\s+(?:prova|caderno)\s*[:=-]?\s*({group})\b'
    for match in re.finditer(pat_color,s):
        val=canonical_color(match.group(1))
        if val and val not in explicit: explicit.append(val)
    # Conjunto explicito de gabaritos, cada cor especificada no proprio texto.
    pairs = re.search(r'\b(?:valido para|aplica.se a|correspondente a|gabarito unico(?: para)?|contem gabaritos de|gabaritos dos cadernos)\b(.{0,170})',s)
    if pairs and kind=='02_Gabaritos':
        for match in re.finditer(r'\b(?:'+group+r')\b',pairs.group(1)):
            val=canonical_color(match.group())
            if val and val not in explicit: explicit.append(val)
    if len(explicit)>1 and kind=='02_Gabaritos':
        count_headers=len(re.findall(pat,s))
        if count_headers>=2 or pairs:
            return explicit,'multiplas_cores_comprovadas_no_texto'
        return [],'cores_ambiguas'
    if len(explicit)>1: return [], 'cores_ambiguas'
    if len(explicit)==1: return explicit,'cor_no_conteudo'
    m=re.search(r'\b(?:caderno|prova)\s*(?:\d{1,2})?\s*\n\s*('+group+r')\b',s)
    if m:
        return [canonical_color(m.group(1))],'cor_no_cabecalho'
    return [],''


def detect_day(text: str) -> str:
    s=fold(text[:10000])
    # Nomes historicos como ENEM-2009-1-cancelado codificam o dia sem a palavra 'dia'.
    m_cancel=re.search(r'\benem[-_/ ]?20\d{2}[-_/ ]([12])[-_/ ]?(?:cancelad[oa]s?|anulad[oa]s?)(?![a-z])',s)
    if m_cancel:
        return m_cancel.group(1)
    m=re.search(r'\b(?:([12])\s*(?:o|º|\.)?\s*dia|dia\s*([12])|(?:primeiro|segundo)\s+dia)\b',s)
    if not m:
        # OCR frequentemente interpreta '1o DIA' como '10 DIA' (e 2o como 20).
        possible=re.search(r'\b([12])0\s+dia\b',s)
        return possible.group(1) if possible else ''
    if m.group(1) or m.group(2): return m.group(1) or m.group(2)
    return '1' if 'primeiro' in m.group(0) else '2'


def detect_semester(text: str) -> str:
    s=fold(text[:2000])
    m=re.search(r'\b(?:([12])\s*[ºo]?\s*(?:semestre|sem)\b|([12])\s*[ºo]?\s*sem\b|([12])\s*[ºo]?\s*[Ss]\b|semestre\s*([12])\b|(?:primeiro|segundo)\s+semestre\b|20\d\d[/_-]([12])\b)',s,re.I)
    if m:
        hits = [x for x in m.groups() if x]
        if hits: return hits[0]
        return '1' if 'primeiro' in m.group(0) else '2'
    return ''


def has_explicit_cancellation(front_text: str, filename: str = '') -> bool:
    """True only when cancellation is explicitly supported by the file.

    A historical year, color, proof number, search query, title metadata, or
    parent folder is not evidence that an exam was cancelled.
    """
    name = fold(Path(filename).name)
    if re.search(r'(?<![a-z])(?:cancelad[oa]s?|anulad[oa]s?)(?![a-z])', name):
        return True
    s = fold(front_text[:1800])
    has_exam = bool(re.search(r'\benem\b|exame nacional do ensino medio|\bfatec\b', s))
    has_cancel_phrase = bool(
        re.search(r'\b(?:prova|aplicacao|exame)\b.{0,55}\b(?:cancelad[oa]s?|anulad[oa]s?)\b', s)
        or re.search(r'\b(?:cancelad[oa]s?|anulad[oa]s?)\b.{0,55}\b(?:prova|aplicacao|exame)\b', s)
    )
    return has_exam and has_cancel_phrase


def detect_edition(text: str, filename: str = '', front_text: str | None = None) -> str:
    s=fold(text[:3500])
    # O rótulo de cancelamento exige nome de arquivo explícito ou declaração
    # clara na capa. Nunca inferir a partir do ano ou de uma menção incidental.
    evidence_text = front_text if front_text is not None else text
    if has_explicit_cancellation(evidence_text, filename): return 'Aplicacao_Cancelada'
    if re.search(r'belem.{0,40}ananindeua|aplicacao.{0,25}belem',s): return 'Aplicacao_Belem'
    if re.search(r'\breaplicacao\b|\bppl\b|pessoas privadas de liberdade',s): return 'Reaplicacao_PPL'
    if re.search(r'\benem digital\b|aplicacao digital',s): return 'Aplicacao_Digital'
    if re.search(r'aplicacao especial|aplicacao extraordinaria',s): return 'Aplicacao_Especial'
    return 'Aplicacao_Regular' if re.search(r'aplicacao regular',s) else 'Aplicacao_Nao_Identificada'


def detect_accessibility(text: str) -> str:
    s=fold(text[:1700]); opts=[]
    for key,pat in (('Braile',r'\bbraile\b'),('Libras',r'\blibras\b'),('Ledor',r'\bledor\b'),('NVDA',r'\bnvda\b'),('DOSVOX',r'\bdosvox\b'),('Superampliada',r'\bsuperampliad'),('Ampliada',r'\bampliad')):
        if re.search(pat,s): opts.append(key)
    if 'Superampliada' in opts and 'Ampliada' in opts: opts.remove('Ampliada')
    return '_'.join(opts[:2])


def detect_discipline(source: str) -> tuple[str,str]:
    s=fold(source[:3000])
    matches=[name for name,pat in DISCIPLINES if re.search(r'\b(?:'+pat+r')\b',s)]
    disc=matches[0] if len(matches)==1 else ''
    topics=[name for name,pat in TOPICS if re.search(r'\b(?:'+pat+r')\b',s)]
    return disc, topics[0] if len(topics)==1 else ''


@dataclass
class DocumentInfo:
    exam: str = ''
    year: str = ''
    semester: str = ''
    day: str = ''
    proof_number: str = ''
    edition: str = ''
    colors: list[str] = field(default_factory=list)
    caderno: str = ''
    versions: list[str] = field(default_factory=list)
    kind: str = '08_Outros'
    accessibility: str = ''
    discipline: str = ''
    topic: str = ''
    method: str = ''
    chars: int = 0
    pages: int = 0
    evidence: str = ''
    status: str = 'revisar'
    reason: str = ''
    multiple_reason: str = ''
    combined_days: bool = False


def inspect(file: Path, row: dict, ocr: bool=True) -> DocumentInfo:
    text, method, pages=extract_text(file,ocr=ocr)
    # Fontes de evidencia em ordem de confianca. Consulta nao prova cor/ano/caderno.
    title=row.get('titulo','')
    url_path=unquote(urlparse(row.get('url','')).path)
    brief=text[:9000]
    front=text[:2000]
    filename=file.stem
    query=row.get('pesquisa','')
    # A pasta de origem e evidencia contextual confiavel quando o nome do PDF
    # e generico (ex.: 2000_PV_impresso.pdf) e a capa nao contem texto extraivel.
    # Usamos os componentes proximos ao arquivo, sem deixar a consulta determinar
    # a classificacao do conteudo.
    path_context=' '.join(file.parts[-5:])
    inf=DocumentInfo(method=method,chars=len(text),pages=pages)
    folded_head=fold(front)
    meta=fold(' '.join([file.name,title,url_path,path_context]))
    if re.search(r'\benem\b|exame nacional do ensino medio',fold(brief[:3000])): inf.exam='ENEM'
    elif re.search(r'\bfatec\b|centro paula souza|faculdade de tecnologia do estado',fold(brief[:3000])): inf.exam='FATEC'
    elif re.search(r'\benem\b',meta): inf.exam='ENEM'
    elif re.search(r'\bfatec\b|vestibular fatec',meta): inf.exam='FATEC'
    elif not text.strip() and re.search(r'\benem\b',fold(query)) and not re.search(r'\bfatec\b',fold(query)): inf.exam='ENEM'
    elif not text.strip() and re.search(r'\bfatec\b',fold(query)) and not re.search(r'\benem\b',fold(query)): inf.exam='FATEC'
    # Evitar aceitar o ano da consulta ou de uma pasta possivelmente errada
    # quando nao existe evidencia no proprio arquivo/nome/URL.
    inf.year=extract_year(front,filename,title,url_path)

    inf.kind=detect_kind(front,filename,title)
    # Arquivos historicos explicitamente marcados como prova cancelada/anulada
    # devem continuar no grupo de provas, nao em 'Outros'. Gabaritos preservam prioridade.
    cancellation_text=fold(' '.join([filename,title,url_path,front[:1200]]))
    if (inf.exam=='ENEM' and re.search(r'(?<![a-z])(?:cancelad[oa]s?|anulad[oa]s?)(?![a-z])',cancellation_text)
            and not re.search(r'\bgabarit', fold(filename+' '+title))):
        inf.kind='01_Provas'
    inf.semester=detect_semester(front) or detect_semester(' '.join([filename,title,url_path]))
    inf.day=detect_day(front) or detect_day(' '.join([filename,title,url_path]))
    inf.proof_number=_first(r'\bprova\s*([0-9]{1,2})\s*[-–—:]?\s*(?:'+COLOR_PATTERN+r')\b',fold(front))
    # O formato anterior a 2009 era aplicado em um unico dia (nao confundir PROVA 1 com 1o dia).
    if inf.exam=='ENEM' and inf.year and 2000<=int(inf.year)<=2008 and not inf.day:
        inf.day='Unico'
    explicit_cancel = has_explicit_cancellation(front, file.name)
    inf.edition=detect_edition(front + ' ' + filename + ' ' + title, file.name, front)
    # Defense in depth: a previous heuristic may have marked a file cancelled
    # based on a historical year or incidental text. Flag it instead of trusting it.
    cancellation_unverified = ('cancelad' in fold(inf.edition) and not explicit_cancel)
    if cancellation_unverified:
        inf.edition = 'Aplicacao_Regular' if re.search(r'aplicacao regular', fold(front[:1800])) else 'Aplicacao_Nao_Identificada'
    inf.accessibility=detect_accessibility(front + ' ' + filename + ' ' + title)
    inf.caderno=_first(r'\bcaderno\s*(?:n[.oº°]*\s*)?([0-9]{1,2})\b',fold(front + ' '+filename+' '+title))
    # Variantes eventuais de prova, se explicitadas (sem supor que existam em toda FATEC).
    versions_content=re.findall(r'\b(?:versao|modelo|tipo)\s*[-:]?\s*([a-d])\b',fold(brief[:8000]))
    versions=re.findall(r'\b(?:versao|modelo|tipo)\s*[-:]?\s*([a-d])\b',fold(brief[:8000]+' '+filename+' '+title))
    inf.versions=list(dict.fromkeys(v.upper() for v in versions))
    colors,multi_reason=detect_colors(brief,inf.kind)
    inf.colors=colors; inf.multiple_reason=multi_reason
    if not inf.colors:
        # Para UM unico caderno apenas, usar capa/titulo, mas nao distribuir gabaritos a varias cores por nome.
        single,mr=detect_colors(filename+' '+title+' '+url_path,inf.kind)
        if len(single)==1 and multi_reason!='cores_ambiguas':
            inf.colors=single; inf.multiple_reason='cor_em_nome_titulo_ou_url'
    disc,topic=detect_discipline(filename+' '+title+' '+brief[:350])
    inf.discipline=disc; inf.topic=topic
    flags=[]
    if cancellation_unverified: flags.append('CANCELAMENTO_NAO_COMPROVADO')
    if not text.strip(): flags.append('SEM_TEXTO_EXTRAIVEL')
    if method=='pdf_ocr_amostrado': flags.append('OCR_CONFERENCIA_RECOMENDADA')
    if not inf.exam and not inf.discipline: flags.append('SEM_EXAME_OU_DISCIPLINA')
    if inf.exam and not inf.year and inf.kind in ('01_Provas','02_Gabaritos'): flags.append('ANO_NAO_IDENTIFICADO')
    if inf.exam=='FATEC' and inf.kind in ('01_Provas','02_Gabaritos') and not inf.semester: flags.append('SEMESTRE_NAO_IDENTIFICADO')
    if inf.exam=='FATEC' and inf.kind=='01_Provas' and len(inf.versions)>1: flags.append('MULTIPLAS_VERSOES_NA_PROVA')
    if inf.exam=='FATEC' and inf.kind=='02_Gabaritos' and len(inf.versions)>1 and len(set(versions_content))<2:
        flags.append('VERSOES_SEM_COMPROVACAO_NO_CONTEUDO')
    if inf.exam=='ENEM' and inf.kind in ('01_Provas','02_Gabaritos') and not inf.colors: flags.append('COR_NAO_IDENTIFICADA')
    if multi_reason=='cores_ambiguas': flags.append('CORES_AMBIGUAS')
    days_identified={detect_day(x) for x in re.findall(r'\b(?:[12]\s*[ºo]?\s*dia|dia\s*[12]|primeiro\s+dia|segundo\s+dia)\b',fold(brief[:8000]))} - {''}
    if len(days_identified)>1 and inf.exam=='ENEM' and inf.kind=='02_Gabaritos':
        # Gabarito consolidado pode cobrir os dois dias; registrar isso explicitamente
        # e distribuir cópias para os dois diretórios, sem marcar como conflito.
        inf.day='Ambos'
        inf.combined_days=True
    elif len(days_identified)>1 and inf.exam=='ENEM' and inf.kind=='01_Provas':
        inf.day=''; flags.append('MULTIPLOS_DIAS_IDENTIFICADOS')
    if inf.exam=='ENEM' and inf.kind in ('01_Provas','02_Gabaritos') and not inf.day: flags.append('DIA_NAO_IDENTIFICADO')
    # Conflitos explicitos entre documento e nome: revisao, nunca sobrepor sem aviso.
    content_exam=('ENEM' if re.search(r'\benem\b|exame nacional do ensino medio',fold(brief[:1500])) else
                  'FATEC' if re.search(r'\bfatec\b|centro paula souza',fold(brief[:1500])) else '')
    filename_exam=('ENEM' if re.search(r'\benem\b',meta) else 'FATEC' if re.search(r'\bfatec\b',meta) else '')
    if content_exam and filename_exam and content_exam!=filename_exam: flags.append('CONFLITO_EXAME')
    content_year=extract_year(front)
    name_year=extract_year(filename,title)
    if content_year and name_year and content_year!=name_year: flags.append('CONFLITO_ANO')
    inf.reason=';'.join(flags)
    inf.status='revisar' if flags else 'classificado'
    day_note='; gabarito_combinado_dias_1_e_2' if inf.combined_days else ''
    inf.evidence=f'{method}; caracteres={len(text)}; cores={multi_reason}; prioridade=texto>nome>titulo>url>caminho>consulta{day_note}'
    return inf
