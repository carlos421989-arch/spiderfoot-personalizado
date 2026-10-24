#!/usr/bin/env python3
"""Content-aware document classification and safe organization for the collector.

Supported text extraction: PDF, DOCX, PPTX, XLSX, text/CSV/HTML and ZIP listings.
For videos, uses filename and optional ffprobe metadata; it does not download videos
from streaming platforms or transcribe audio. OCR requires a separate OCR engine.
"""
from __future__ import annotations
import csv, hashlib, json, os, re, shutil, subprocess, sys, unicodedata
from pathlib import Path
from datetime import datetime, timezone

SUPPORTED = {'.pdf','.docx','.pptx','.xlsx','.txt','.csv','.html','.htm','.md','.json','.xml','.zip','.mp4','.mkv','.mov','.avi','.webm','.m4v','.mp3','.wav','.m4a'}
VIDEO_EXTS = {'.mp4','.mkv','.mov','.avi','.webm','.m4v'}
AUDIO_EXTS = {'.mp3','.wav','.m4a'}

def stamp(): return datetime.now(timezone.utc).isoformat(timespec='seconds')

def safe_component(value, maxlen=90):
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r'[^A-Za-z0-9._ -]+', '_', value)
    value = re.sub(r'\s+', '_', value).strip(' ._-')
    value = re.sub(r'_+', '_', value)
    return (value[:maxlen].strip(' ._-') or 'Documento')

def extract_internal_text(path: Path, max_chars=30000) -> str:
    """Extract textual content where a supported parser is available."""
    ext=path.suffix.lower(); out=[]
    try:
        if ext == '.pdf':
            from PyPDF2 import PdfReader
            reader=PdfReader(str(path))
            for page in reader.pages[:60]:
                try: out.append(page.extract_text() or '')
                except Exception: pass
                if sum(map(len,out)) >= max_chars: break
        elif ext == '.docx':
            from docx import Document
            d=Document(str(path)); out.extend(p.text for p in d.paragraphs)
            for table in d.tables:
                for row in table.rows: out.append(' | '.join(c.text for c in row.cells))
        elif ext == '.pptx':
            from pptx import Presentation
            prs=Presentation(str(path))
            for slide in prs.slides:
                out.extend(sh.text for sh in slide.shapes if getattr(sh,'has_text_frame',False))
        elif ext == '.xlsx':
            from openpyxl import load_workbook
            wb=load_workbook(str(path), read_only=True, data_only=True)
            for ws in wb.worksheets:
                out.append(ws.title)
                for row in ws.iter_rows(max_row=1000, values_only=True):
                    out.append(' '.join(str(v) for v in row if v is not None))
                    if sum(map(len,out)) >= max_chars: break
                if sum(map(len,out)) >= max_chars: break
            wb.close()
        elif ext in {'.txt','.csv','.html','.htm','.md','.json','.xml'}:
            raw=path.read_bytes()[:max_chars*4]
            out.append(raw.decode('utf-8-sig',errors='replace'))
        elif ext == '.zip':
            import zipfile
            with zipfile.ZipFile(path) as z: out.extend(z.namelist()[:300])
    except Exception as exc:
        out.append('')
    return re.sub(r'\s+',' ',' '.join(out)).strip()[:max_chars]

def media_metadata(path: Path) -> dict:
    """Read local media tags using ffprobe when installed; never runs media contents."""
    try:
        proc=subprocess.run(['ffprobe','-v','error','-show_entries','format=duration:format_tags=title,artist,album,comment','-of','json',str(path)],capture_output=True,text=True,timeout=8,check=False)
        if proc.returncode == 0:
            data=json.loads(proc.stdout or '{}'); fmt=data.get('format',{})
            tags=fmt.get('tags',{}) or {}
            return {'title':tags.get('title',''), 'artist':tags.get('artist',''), 'album':tags.get('album',''), 'comment':tags.get('comment',''), 'duration':fmt.get('duration','')}
    except Exception: pass
    return {}

def infer_meta(query: str, title: str, url: str, path: Path, base_meta: dict|None=None) -> tuple[dict,str,dict]:
    """Enrich existing metadata with internal text and file tags; be conservative."""
    content=extract_internal_text(path)
    tags=media_metadata(path) if path.suffix.lower() in VIDEO_EXTS|AUDIO_EXTS else {}
    evidence=' '.join([query or '',title or '',url or '',path.name,content[:20000],*map(str,tags.values())])
    meta=dict(base_meta or {})
    # Import lazily to avoid requiring collector functions for stand-alone use.
    try:
        from coletar_materiais import classify_document
        candidate=classify_document(query or '', (title or '')+' '+content[:12000]+' '+' '.join(map(str,tags.values())), url or '')
        for key in ('exam','year','material','subject'):
            value=candidate.get(key)
            if value and (value not in ('OUTROS','sem_ano','Outros','Geral') or not meta.get(key)):
                # Never change a known exam/year/material based only on unrelated boilerplate.
                if key == 'exam' and value == 'OUTROS' and meta.get('exam') in ('ENEM','FATEC'): continue
                if key == 'year' and value == 'sem_ano' and meta.get('year') not in ('','sem_ano',None): continue
                if key == 'subject' and value == 'Geral' and meta.get('subject') not in ('','Geral',None): continue
                meta[key]=value
        colors=candidate.get('colors') or []
        if colors: meta['colors']=colors
        meta['shared']=bool(meta.get('shared') or candidate.get('shared'))
        # Detect an exam/year from the actual body if the query/title missed it.
        if re.search(r'\bENEM\b|Instituto Nacional de Estudos e Pesquisas', content, re.I): meta['exam']='ENEM'
        elif re.search(r'FATEC|Centro Paula Souza', content, re.I): meta['exam']='FATEC'
        if not meta.get('year') or meta.get('year')=='sem_ano':
            m=re.search(r'\b(19\d{2}|20\d{2})\b', content[:10000])
            if m: meta['year']=m.group(1)
        if not meta.get('colors'):
            found=[]
            for c in ('Azul','Amarela','Branca','Rosa','Verde','Laranja','Cinza'):
                if re.search(r'\bcaderno\s+'+c+r'\b|\bprova\s+'+c+r'\b|\bgabarito\s+'+c+r'\b', evidence, re.I): found.append(c)
            if found: meta['colors']=found
        # For local video/audio, organize by topic using tags/name/query; no unsupported transcript claims.
        if path.suffix.lower() in VIDEO_EXTS|AUDIO_EXTS:
            meta['material']='Videoaulas' if path.suffix.lower() in VIDEO_EXTS else 'Audios'
            for subject, pattern in [('Matematica',r'matem[aá]tica|geometria|estat[ií]stica'),('Portugues',r'portugu[eê]s|gram[aá]tica|literatura'),('Fisica',r'f[ií]sica|mec[aâ]nica|eletricidade'),('Quimica',r'qu[ií]mica|estequiometria'),('Biologia',r'biologia|ecologia|gen[eé]tica'),('Historia',r'hist[oó]ria'),('Geografia',r'geografia|geopol[ií]tica'),('Ingles',r'ingl[eê]s|english'),('Informatica',r'inform[aá]tica|programa[cç][aã]o|tecnologia')]:
                if re.search(pattern,evidence,re.I): meta['subject']=subject; break
    except Exception:
        pass
    # Revisao final independente da consulta: capa/nome/URL sao evidencias,
    # enquanto a pesquisa e apenas contexto. Nao publicar ano/cor conflitante.
    if path.suffix.lower() == '.pdf':
        try:
            from analisar_conteudo import inspect as inspect_document
            info = inspect_document(path, {'titulo': title, 'url': url, 'pesquisa': query}, ocr=False)
            if info.exam in ('ENEM', 'FATEC'):
                meta['exam'] = info.exam
            if 'CONFLITO_ANO' in info.reason:
                meta['year'] = 'revisar_ano'
            elif info.year and 'ANO_NAO_IDENTIFICADO' not in info.reason:
                meta['year'] = info.year
            else:
                meta['year'] = 'revisar_ano'
            if 'CORES_AMBIGUAS' in info.reason or 'CONFLITO_' in info.reason:
                meta['colors'] = []
                meta['shared'] = False
            else:
                meta['colors'] = [ {'Amarelo':'Amarela', 'Branco':'Branca'}.get(c,c) for c in info.colors ]
                meta['shared'] = bool(info.multiple_reason == 'multiplas_cores_comprovadas_no_texto' and len(info.colors)>1)
            if info.kind in ('01_Provas','02_Gabaritos'):
                meta['material'] = {'01_Provas':'Provas','02_Gabaritos':'Gabaritos'}[info.kind]
        except (ImportError, OSError, ValueError, RuntimeError) as exc:
            import logging
            logging.warning('Classificacao rigorosa nao disponivel para %s: %s', path.name, exc)
            meta['year'] = 'revisar_ano'
            meta['colors'] = []
            meta['shared'] = False
    return meta, content, tags

def suggested_filename(path: Path, meta: dict, content: str, tags: dict, query='', title='') -> str:
    ext=path.suffix.lower(); old=path.stem
    clean_old = re.sub(r'[_ -]?(?:[0-9a-f]{8,}|[0-9]{5,})$', '', old, flags=re.I).strip(' _-.')
    generic = bool(re.fullmatch(r'(?:download|file|document|documento|arquivo|scan|tmp|untitled|sem.?titulo)(?:[_ -].*)?', clean_old, re.I))
    informative = len(re.sub(r'[^A-Za-z]','',clean_old)) >= 5 and not generic and not re.fullmatch(r'(?:[0-9a-f]{8,}|[0-9]{5,})',clean_old,re.I)
    # Preserve already clear descriptive names; still clean illegal chars.
    if informative and not re.search(r'^[0-9a-f]{10,}$',old,re.I):
        base=safe_component(clean_old or old,75)
    else:
        title_candidate=(tags.get('title') or title or '')
        if re.fullmatch(r'(?:download|file|document|documento|arquivo|scan|tmp|untitled|sem.?titulo)(?:[_ -].*)?', title_candidate, re.I) or re.fullmatch(r'[0-9a-f]{8,}|[0-9]{5,}', title_candidate, re.I):
            title_candidate=''
        if not title_candidate and content:
            # Prefer a short informative line near the start, not arbitrary boilerplate.
            parts=re.split(r'(?<=[.!?])\s+|\s{2,}',content)
            title_candidate=next((p for p in parts if 12 <= len(p.strip()) <= 100 and re.search(r'[A-Za-zÀ-ÿ]',p) and not re.search(r'cookie|privacy|javascript|http[s]?://',p,re.I)), '')
        bits=[]
        exam=meta.get('exam','OUTROS'); year=meta.get('year','sem_ano'); material=meta.get('material','Documento')
        if exam and exam!='OUTROS': bits.append(exam)
        if year and year!='sem_ano': bits.append(str(year))
        bits.append(material)
        colors=meta.get('colors') or []
        if colors: bits.append('Caderno_'+'_'.join(colors))
        if meta.get('subject') and meta.get('subject')!='Geral': bits.append(meta['subject'])
        # Avoid duplicating structured information already encoded in the name.
        structured = [str(exam), str(year), str(material)] + list(colors)
        repeats_structure = any(re.search(r'\b'+re.escape(token)+r'\b', title_candidate, re.I) for token in structured if token and token not in ('OUTROS','sem_ano','Outros'))
        if title_candidate and not repeats_structure: bits.append(title_candidate[:65])
        base=safe_component('_'.join(bits),115)
    digest=hashlib.sha256((str(path)+str(path.stat().st_size)).encode('utf-8')).hexdigest()[:8]
    return f'{base}_{digest}{ext}'

def unique_path(path: Path) -> Path:
    if not path.exists(): return path
    stem,suffix=path.stem,path.suffix; i=2
    while True:
        candidate=path.with_name(f'{stem}_{i}{suffix}')
        if not candidate.exists(): return candidate
        i+=1

def organize_inbox(tool_dir: Path, apply=False) -> dict:
    """Organize files users place in resultados/entrada_arquivos. Dry-run by default."""
    out=tool_dir/'resultados'; inbox=out/'entrada_arquivos'; inbox.mkdir(parents=True,exist_ok=True)
    files=[p for p in inbox.rglob('*') if p.is_file() and p.suffix.lower() in SUPPORTED and not p.name.endswith('.part')]
    report=out/'relatorio_organizacao.csv'; manifest=out/'manifesto_organizacao.csv'
    rows=[]
    for path in sorted(files):
        query=path.parent.name.replace('_',' '); title=path.stem
        # Reuse classification heuristics from collector when possible.
        try:
            from coletar_materiais import classify_document, organized_path, category
            meta=classify_document(query,title,'')
        except Exception:
            meta={'exam':'OUTROS','year':'sem_ano','material':'Outros','colors':[],'shared':False,'subject':'Geral','query':query,'title':title,'url':''}
        meta,content,tags=infer_meta(query,title,'',path,meta)
        ext=path.suffix.lower()
        if ext in VIDEO_EXTS: meta['material']='Videoaulas'
        elif ext in AUDIO_EXTS: meta['material']='Audios'
        if meta.get('exam')=='ENEM':
            base=out/'materiais'/'ENEM'/str(meta.get('year') or 'sem_ano')/str(meta.get('material') or 'Outros')
            if meta['material']=='Provas': base/= 'Caderno_'+(meta['colors'][0] if meta.get('colors') else 'Cor_nao_identificada')
            elif meta['material']=='Gabaritos': base/= ('Compartilhado_todas_as_cores' if meta.get('shared') and len(meta.get('colors',[]))>1 else 'Caderno_'+(meta['colors'][0] if meta.get('colors') else 'Cor_nao_identificada'))
            elif meta.get('material') in ('Videoaulas','Audios'): base/=safe_component(meta.get('subject') or 'Geral')
        elif meta.get('exam')=='FATEC':
            base=out/'materiais'/'FATEC'/str(meta.get('year') or 'sem_ano')/str(meta.get('material') or 'Outros')
            if meta.get('material') in ('Videoaulas','Audios'): base/=safe_component(meta.get('subject') or 'Geral')
        else: base=out/'materiais'/'OUTROS'/safe_component(meta.get('subject') or 'Geral')/str(meta.get('year') or 'sem_ano')/str(meta.get('material') or 'Outros')
        newname=suggested_filename(path,meta,content,tags,query,title)
        # Video sequence is assigned after gathering items in each destination folder.
        if ext in VIDEO_EXTS:
            display=(tags.get('title') or title)
            newname='VIDEO_PENDING__'+safe_component(display,90)+ext
        dest=base/newname
        rows.append({'source':path,'dest':dest,'meta':meta,'content':content,'tags':tags,'name':newname})
    # Stable sequence for local videos per subject/year folder.
    groups={}
    for row in rows:
        if row['source'].suffix.lower() in VIDEO_EXTS:
            groups.setdefault(row['dest'].parent,[]).append(row)
    for folder,items in groups.items():
        items.sort(key=lambda x: (str(x['tags'].get('title') or x['source'].stem).casefold(), x['source'].name.casefold()))
        for i,row in enumerate(items,1):
            title=row['tags'].get('title') or row['source'].stem
            row['name']=f'Video_{i:03d}_{safe_component(title,85)}{row["source"].suffix.lower()}'
            row['dest']=folder/row['name']
    out_rows=[]
    for row in rows:
        src=row['source']; dest=unique_path(row['dest'])
        status='PREVISTO'
        if apply:
            dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.move(str(src),str(dest)); status='ORGANIZADO'
            # Record content-derived metadata; text extraction limitations are explicit.
            append_line=[stamp(),str(src.relative_to(out)),str(dest.relative_to(out)),row['meta'].get('exam','OUTROS'),row['meta'].get('year','sem_ano'),row['meta'].get('material','Outros'),';'.join(row['meta'].get('colors',[])),row['meta'].get('subject','Geral'),row['tags'].get('title',''), 'conteudo_extraido' if row['content'] else ('metadados_media' if row['tags'] else 'nome_consulta_url'),hashlib.sha256(dest.read_bytes()).hexdigest()]
            write_csv(manifest,['data','origem','destino','exame','ano','tipo','cores','assunto','titulo_interno','base_classificacao','sha256'],append_line)
        out_rows.append([str(src.relative_to(out)),str(dest.relative_to(out)),status,row['meta'].get('exam','OUTROS'),row['meta'].get('year','sem_ano'),row['meta'].get('material','Outros'),';'.join(row['meta'].get('colors',[])),row['meta'].get('subject','Geral'), 'texto interno extraído' if row['content'] else 'sem texto extraível; usado nome/consulta/metadados'])
    write_csv(report,['origem','destino_sugerido','status','exame','ano','tipo_material','cores','assunto','metodo_analise'],out_rows)
    return {'scanned':len(files),'organized':len(rows) if apply else 0,'preview':len(rows),'apply':apply,'report':str(report)}

def write_csv(path, header, row):
    path.parent.mkdir(parents=True,exist_ok=True); exists=path.exists() and path.stat().st_size>0
    with path.open('a',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f)
        if not exists: w.writerow(header)
        if row: w.writerow(row) if not isinstance(row[0],list) else w.writerows(row)

def main():
    import argparse
    ap=argparse.ArgumentParser(description='Organizador inteligente de documentos e vídeos do coletor')
    ap.add_argument('--tool-dir',default=str(Path(__file__).resolve().parent))
    ap.add_argument('--aplicar',action='store_true',help='Move and renames files; without this flag it only writes a preview report.')
    args=ap.parse_args(); result=organize_inbox(Path(args.tool_dir).resolve(),apply=args.aplicar)
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
