#!/usr/bin/env python3
"""Acervo por evidencia do CONTEUDO, cria subpastas e renomeia COPIAS.

Compatibilidade: continua lendo resultados/arquivos_baixados.csv da versao anterior.
Idempotente, SHA-256, sem excluir ou renomear qualquer original.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

from analisar_conteudo import COLORS, KINDS, DocumentInfo, clean_filename, fold, inspect, canonical_color

BASE=Path(__file__).resolve().parent
SRC=BASE/'resultados'
VALID_EXT={'.pdf','.doc','.docx','.ppt','.pptx','.xls','.xlsx','.odt','.ods','.zip','.txt','.md','.csv'}
OUT_FIELDS=['exame','ano','semestre','aplicacao','dia','numero_prova','cor','versao','caderno','acessibilidade','disciplina','assunto','tipo','status','motivos_revisao',
            'metodo_leitura','caracteres_lidos','paginas','evidencias','titulo_original','nome_original','nome_organizado',
            'pasta','arquivo_completo','url_origem','pesquisa','sha256','data_cadastro']
RENAME_FIELDS=['nome_original','nome_organizado','pasta_destino','sha256','criterio','revisao_pendente']


def sha256_file(path:Path)->str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(512*1024),b''): h.update(chunk)
    return h.hexdigest()


def read_csv(path:Path)->list[dict]:
    if not path.is_file(): return []
    with path.open('r',encoding='utf-8-sig',newline='') as f:
        return list(csv.DictReader(f))


def write_csv(path:Path,fields:list[str],rows:list[dict]):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore')
        wr.writeheader(); wr.writerows(rows)


def init_dirs(root:Path):
    for item in ('00_CAIXA_DE_ENTRADA','00_CATALOGO','01_ENEM','02_FATEC','03_MATERIAIS_POR_DISCIPLINA',
                 '04_LINKS_E_REFERENCIAS','99_A_CLASSIFICAR','99_DUPLICADOS_PARA_REVISAR'):
        (root/item).mkdir(parents=True,exist_ok=True)
    for b in ('YouTube','Cursos_Online','Sites_Oficiais','Outras_Fontes'):
        (root/'04_LINKS_E_REFERENCIAS'/b).mkdir(parents=True,exist_ok=True)


def folder_of(info:DocumentInfo, root:Path, file:Path)->Path:
    if 'CONFLITO_' in info.reason or 'MULTIPLOS_DIAS' in info.reason or 'MULTIPLAS_VERSOES' in info.reason:
        return root/'99_A_CLASSIFICAR'/'01_CONFLITO_DE_INFORMACOES'
    disc=info.discipline or '00_DISCIPLINA_NAO_IDENTIFICADA'
    topic=info.topic or '00_ASSUNTO_GERAL'
    if info.exam=='ENEM':
        if info.kind in ('01_Provas','02_Gabaritos'):
            day_folder = 'Dia_Unico' if info.day=='Unico' else ('Ambos_os_Dias' if info.day=='Ambos' else f'{info.day}_Dia' if info.day else '00_DIA_NAO_IDENTIFICADO')
            return (root/'01_ENEM'/(info.year or '00_ANO_NAO_IDENTIFICADO')/(info.edition or 'Aplicacao_Nao_Identificada')/
                    day_folder/'COR_A_SER_PREENCHIDA'/info.kind)
        return root/'01_ENEM'/(info.year or '00_MATERIAIS_SEM_ANO')/'Materiais_de_Estudo'/disc/topic/info.kind
    if info.exam=='FATEC':
        if info.kind in ('01_Provas','02_Gabaritos'):
            sem=f'{info.semester}_SEMESTRE' if info.semester else '00_SEMESTRE_NAO_IDENTIFICADO'
            return root/'02_FATEC'/(info.year or '00_ANO_NAO_IDENTIFICADO')/sem/'VERSAO_A_SER_PREENCHIDA'/info.kind
        return root/'02_FATEC'/(info.year or '00_MATERIAIS_SEM_ANO')/'Materiais_de_Estudo'/disc/topic/info.kind
    if info.discipline:
        return root/'03_MATERIAIS_POR_DISCIPLINA'/disc/topic/info.kind
    reason='00_SEM_IDENTIFICACAO'
    if 'CONFLITO' in info.reason: reason='01_CONFLITO_DE_INFORMACOES'
    elif info.method.startswith(('legado_sem','formato','pdf_erro','zip_ilegivel')): reason='02_FORMATO_SEM_LEITURA'
    return root/'99_A_CLASSIFICAR'/reason


def friendly_name(info:DocumentInfo, original:Path, digest:str)->str:
    """Nome da COPIA ou DOWNLOAD, usando somente a identificacao documentada."""
    c=[]
    if info.exam: c.append(info.exam)
    if info.year: c.append(info.year)
    if info.exam=='FATEC' and info.semester: c.append(f'{info.semester}S')
    if info.exam=='ENEM':
        if info.edition and info.edition!='Aplicacao_Nao_Identificada':
            c.append(info.edition.replace('Aplicacao_',''))
        if info.day and info.day!='Unico':
            c.append('Dias_1_e_2' if info.day=='Ambos' else f'Dia{info.day}')
        if info.kind=='01_Provas':
            c.append('Prova')
            if info.proof_number: c.append(info.proof_number)
        elif info.kind=='02_Gabaritos': c.append('Gabarito')
        if info.colors:
            c.append('Amarela' if info.colors==['Amarelo'] and info.kind=='01_Provas' and
                     info.year and int(info.year)<=2008 else '-'.join(info.colors))
        elif info.kind in ('01_Provas','02_Gabaritos'): c.append('Cor_Nao_Identificada')
        if info.caderno: c.append(f'Caderno{info.caderno}')
        if info.accessibility: c.append(info.accessibility)
    else:
        if info.exam=='FATEC' and info.versions: c.append('-'.join(f'Versao{x}' for x in info.versions))
        if not info.exam:
            if info.discipline: c.append(info.discipline)
            if info.topic: c.append(info.topic)
        typ={'01_Provas':'Prova','02_Gabaritos':'Gabarito'}.get(info.kind,info.kind.split('_',1)[-1])
        if typ!='Outros': c.append(typ)
    if info.exam=='ENEM' and info.kind=='08_Outros': c.append('Tipo_Nao_Identificado')
    if not info.year and info.exam and info.kind in ('01_Provas','02_Gabaritos'): c.append('Ano_Nao_Identificado')
    if len(c)<2: c=[clean_filename(original.stem,55),'Revisar']
    return clean_filename('_'.join(c),115)+'_'+digest[:10]+original.suffix.lower()


def verified_mapping(path:Path)->dict[str,dict]:
    results={}
    for row in read_csv(path):
        digest=row.get('sha256','').strip().lower()
        if not re.fullmatch('[0-9a-f]{64}',digest): continue
        if not row.get('justificativa','').strip() or not row.get('fonte_verificacao','').strip(): continue
        colors=[]
        for c in re.split('[,;|/]',fold(row.get('cores',''))):
            val=canonical_color(c)
            if val and val not in colors: colors.append(val)
        versions=[v.strip().upper() for v in re.split('[,;|/]',row.get('versoes','')) if v.strip().upper() in ('A','B','C','D')]
        if not colors and not versions: continue
        results[digest]=dict(row, validated_colors=colors, validated_versions=list(dict.fromkeys(versions)))
    return results


def apply_manual_mapping(info:DocumentInfo,row:dict):
    if info.kind!='02_Gabaritos': return
    if info.exam=='FATEC':
        if not row['validated_versions']: return
        info.versions=row['validated_versions']
        for col,attr in (('ano','year'),('semestre','semester')):
            if row.get(col,'').strip(): setattr(info,attr,row[col].strip())
        info.multiple_reason='correspondencia_manual_documentada'
        info.reason=';'.join(f for f in info.reason.split(';') if f and f not in ('VERSAO_NAO_IDENTIFICADA','VERSOES_SEM_COMPROVACAO_NO_CONTEUDO'))
        info.status='validado_manualmente' if not info.reason else 'revisar'
        info.evidence+='; correspondencia_manual='+row.get('fonte_verificacao','')[:120]
        return
    if info.exam!='ENEM' or not row['validated_colors']: return
    info.colors=row['validated_colors']
    for col,attr in (('ano','year'),('dia','day'),('aplicacao','edition'),('caderno','caderno')):
        value=row.get(col,'').strip()
        if value: setattr(info,attr,value)
    if info.edition and not info.edition.startswith('Aplicacao_'): info.edition='Aplicacao_'+info.edition
    old=[f for f in info.reason.split(';') if f and f not in ('COR_NAO_IDENTIFICADA','CORES_AMBIGUAS')]
    info.reason=';'.join(old)
    info.status='validado_manualmente' if not old else 'revisar'
    info.multiple_reason='correspondencia_manual_documentada'
    info.evidence+='; correspondencia_manual='+row.get('fonte_verificacao','')[:120]


def normalize_downloads(src:Path, *, simulate:bool=False)->dict:
    """Renomeia downloads ja catalogados SEM perder os vinculos do CSV.

    Mantem backup do indice anterior, trilha de auditoria e nomes com hash.
    Se uma execucao for interrompida apos rename e antes do CSV, localiza pelo SHA.
    """
    index=src/'arquivos_baixados.csv'
    if not index.is_file(): return {'alterados':0,'sem_acesso':0,'pendentes':0}
    with index.open('r',encoding='utf-8-sig',newline='') as f:
        reader=csv.DictReader(f)
        fields=list(reader.fieldnames or [])
        rows=list(reader)
    if 'arquivo' not in fields: return {'alterados':0,'sem_acesso':len(rows),'pendentes':0}
    stats={'alterados':0,'sem_acesso':0,'pendentes':0}
    audit=[]
    allfiles=None
    # Keep paths lexical under `src`: on Windows Path.resolve() can return an
    # 8.3 short-name path for the base but a long Unicode path for a child.
    # Mixing both makes relative_to() raise ValueError for valid files.
    origin_root=src.absolute()
    materials_root=src/'materiais'

    def inside_materials(candidate: Path) -> bool:
        # `rp` has already been checked for absolute paths and '..'. Reject
        # symlink files/parents so a relative path cannot escape the results tree.
        try:
            rel = candidate.absolute().relative_to(origin_root)
        except ValueError:
            return False
        if not rel.parts or rel.parts[0].casefold() != 'materiais':
            return False
        cur = origin_root
        for part in rel.parts:
            cur = cur / part
            if cur.is_symlink():
                return False
        return True
    for row in rows:
        oldval=(row.get('arquivo') or '').strip()
        rp=Path(oldval)
        # Downloads temporarios/incompletos nao sao documentos: nao renomear nem classificar.
        if rp.name.lower().endswith(('.part', '.crdownload', '.download', '.tmp')):
            stats['pendentes']+=1
            audit.append({'nome_anterior':oldval,'nome_novo':'','sha256':row.get('sha256',''),
                          'status':'INCOMPLETO_IGNORADO','motivo':'download temporario; conclua ou refaca o download antes de organizar'})
            if simulate: print('[IGNORADO INCOMPLETO]',rp.name)
            continue
        if not oldval or rp.is_absolute() or '..' in rp.parts:
            stats['sem_acesso']+=1; continue
        source=src/rp
        # Restricao aos materiais gerados pelo proprio programa, sem misturar
        # caminhos longos e curtos do Windows.
        if not inside_materials(source):
            stats['sem_acesso']+=1;continue
        if not source.is_file() and re.fullmatch(r'[a-fA-F0-9]{64}',row.get('sha256','')):
            if allfiles is None:
                allfiles={}
                for other in (src/'materiais').rglob('*'):
                    if other.is_file() and other.suffix.lower() in VALID_EXT:
                        try: allfiles.setdefault(sha256_file(other),other)
                        except OSError: pass
            healed=allfiles.get(row['sha256'].lower())
            if healed and healed.is_file():
                row['arquivo']=str(healed.relative_to(src))
                source=healed
                stats['alterados']+=1
                if not simulate:
                    recovered=index.with_suffix('.csv.tmp')
                    write_csv(recovered,fields,rows)
                    recovered.replace(index)
        if not source.is_file():
            stats['sem_acesso']+=1;continue
        try:
            digest=sha256_file(source)
            if row.get('sha256') and digest!=row['sha256'].lower():
                stats['pendentes']+=1
                audit.append({'nome_anterior':str(source.relative_to(src)),'nome_novo':'',
                              'sha256':digest,'status':'CONFLITO_SHA256','motivo':'arquivo alterado fora do programa'})
                continue
            info=inspect(source,row)
            newname=friendly_name(info,source,digest)
            target=source.with_name(newname)
            if source==target: continue
            if target.exists():
                # Jamais sobrescrever outro documento.
                if sha256_file(target)!=digest:
                    target=source.with_name(clean_filename(Path(newname).stem,105)+'_'+digest[:16]+source.suffix.lower())
                if target.exists():
                    stats['pendentes']+=1
                    audit.append({'nome_anterior':str(source.relative_to(src)),'nome_novo':str(target.relative_to(src)),
                                  'sha256':digest,'status':'DESTINO_JA_EXISTE','motivo':'sem sobrescrever ou excluir'})
                    continue
            audit.append({'nome_anterior':str(source.relative_to(src)),'nome_novo':str(target.relative_to(src)),
                          'sha256':digest,'status':'SIMULACAO' if simulate else 'RENOMEADO',
                          'motivo':info.evidence+ ('; revisar='+info.reason if info.reason else '')})
            if simulate:
                print('[SIMULACAO NOME]',source.name,'=>',target.name)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                backup=index.with_name('arquivos_baixados_ANTES_DA_RENOMEACAO_V4.csv')
                if not backup.exists(): shutil.copy2(index,backup)
                # Em Windows, antivirus/indexadores podem manter um handle aberto e
                # fazer rename falhar transitoriamente. Fallback seguro: copiar, validar
                # SHA-256 e só então remover a origem. Nunca apagar sem validar o destino.
                try:
                    # os.rename is the filesystem primitive; using it also avoids
                    # wrappers that monkeypatch pathlib.Path.rename in tests.
                    os.rename(source, target)
                except OSError as rename_exc:
                    # Fallback for antivirus/indexers holding a Windows handle.
                    # Never delete a verified copy just because unlinking the source
                    # is blocked; in that case update the index to the safe copy and
                    # leave both files for later cleanup/review.
                    try:
                        shutil.copy2(source, target)
                        if not target.is_file() or sha256_file(target) != digest:
                            try: target.unlink()
                            except OSError: pass
                            raise OSError('copia de contingencia nao passou na validacao SHA-256') from rename_exc
                        try:
                            source.unlink()
                        except OSError as unlink_exc:
                            row['arquivo']=str(target.relative_to(src))
                            stats['alterados']+=1
                            audit.append({'nome_anterior':str(source.relative_to(src)),'nome_novo':str(target.relative_to(src)),
                                          'sha256':digest,'status':'COPIA_VERIFICADA_ORIGINAL_PRESERVADO',
                                          'motivo':'destino validado por SHA-256; original preservado porque o Windows bloqueou a remocao: '+str(unlink_exc)[:120]})
                            tmp=index.with_suffix('.csv.tmp')
                            write_csv(tmp,fields,rows)
                            tmp.replace(index)
                            continue
                    except OSError:
                        # Keep any copy that has the expected digest; only remove a
                        # mismatched/corrupt destination. Preserve the original.
                        if target.exists():
                            try:
                                if sha256_file(target) != digest: target.unlink()
                            except OSError: pass
                        raise rename_exc
                row['arquivo']=str(target.relative_to(src))
                stats['alterados']+=1
                # Atualizacao do CSV apos CADA movimento minimiza lacuna de interrupcao.
                tmp=index.with_suffix('.csv.tmp')
                write_csv(tmp,fields,rows)
                tmp.replace(index)
        except (OSError,ValueError,RuntimeError) as exc:
            stats['pendentes']+=1
            audit.append({'nome_anterior':oldval,'nome_novo':'','sha256':row.get('sha256',''),
                          'status':'ERRO','motivo':type(exc).__name__+': '+str(exc)[:200]})
            print('[PENDENTE RENOMEACAO]', rp.name, type(exc).__name__, str(exc)[:180])
    if audit and not simulate:
        path=src/'relatorio_renomeacoes_downloads.csv'
        audit_fields=['nome_anterior','nome_novo','sha256','status','motivo']
        before=read_csv(path)
        write_csv(path,audit_fields,before+audit)
    return stats


def material_entries(src:Path,root:Path, extra:Path|None,extract_zips:bool):
    """Gera (caminho, linha CSV) sem aceitar caminhos que saiam de resultados/."""
    entries=[]
    for row in read_csv(src/'arquivos_baixados.csv'):
        rpath=Path(row.get('arquivo',''))
        if not row.get('arquivo') or rpath.is_absolute() or '..' in rpath.parts: continue
        path=(src/rpath).resolve()
        if not path.is_relative_to(src.resolve()) or not path.is_file(): continue
        if path.suffix.lower() in VALID_EXT: entries.append((path,row))
    if extra:
        if extra.is_file(): candidates=[extra]
        elif extra.is_dir(): candidates=extra.rglob('*')
        else: raise FileNotFoundError(f'Pasta ou arquivo nao encontrado: {extra}')
        for path in candidates:
            if not path.is_file() or path.suffix.lower() not in VALID_EXT: continue
            if root==path or root in path.parents: continue
            if src==path or src in path.parents: continue
            entries.append((path,{'arquivo':str(path),'titulo':path.stem,'url':'','pesquisa':''}))
    if extract_zips:
        out=src/'extraidos_zip'
        new=[]
        for file,row in entries:
            if file.suffix.lower()!='.zip': continue
            try:
                with zipfile.ZipFile(file) as archive:
                    infos=archive.infolist()
                    if len(infos)>250 or sum(i.file_size for i in infos)>250_000_000: continue
                    for member in infos:
                        name=Path(member.filename)
                        # Protege contra traversal e ZIP bombs; extrai so formatos seguros para leitura.
                        if member.is_dir() or name.is_absolute() or '..' in name.parts or (member.external_attr >> 16)&0o170000==0o120000:
                            continue
                        if name.suffix.lower() not in VALID_EXT-{'.zip'} or member.file_size>50_000_000: continue
                        if member.compress_size and member.file_size/member.compress_size>250: continue
                        fingerprint=hashlib.sha256((str(file)+member.filename).encode()).hexdigest()[:10]
                        fileout=out/(fingerprint+'_'+clean_filename(name.stem,65)+name.suffix.lower())
                        fileout.parent.mkdir(parents=True,exist_ok=True)
                        if not fileout.exists() or fileout.stat().st_size!=member.file_size:
                            with archive.open(member) as srcbytes,fileout.open('wb') as fout:
                                shutil.copyfileobj(srcbytes,fout,length=64*1024)
                        new.append((fileout,dict(row,titulo=(row.get('titulo','')+' '+name.stem).strip())))
            except (OSError,zipfile.BadZipFile,RuntimeError): continue
        entries+=new
    return entries


def output_row(info:DocumentInfo,row:dict,source:Path,dest:Path,digest:str)->dict:
    return {'exame':info.exam,'ano':info.year,'semestre':info.semester,'aplicacao':info.edition,'dia':info.day,'numero_prova':info.proof_number,
            'cor':dest.parent.parent.name if info.exam=='ENEM' and info.kind in ('01_Provas','02_Gabaritos') and 'CONFLITO_' not in info.reason else '|'.join(info.colors),
            'versao':dest.parent.parent.name if info.exam=='FATEC' and info.kind in ('01_Provas','02_Gabaritos') and 'CONFLITO_' not in info.reason else '|'.join(info.versions),
            'caderno':info.caderno,'acessibilidade':info.accessibility,'disciplina':info.discipline,'assunto':info.topic,
            'tipo':info.kind,'status':info.status,'motivos_revisao':info.reason,'metodo_leitura':info.method,
            'caracteres_lidos':info.chars,'paginas':info.pages,'evidencias':info.evidence,'titulo_original':row.get('titulo',''),
            'nome_original':source.name,'nome_organizado':dest.name,'pasta':str(dest.parent),'arquivo_completo':str(dest),
            'url_origem':row.get('url',''),'pesquisa':row.get('pesquisa',''),'sha256':digest,
            'data_cadastro':datetime.now().isoformat(timespec='seconds')}


def record_links(src:Path,root:Path):
    links=read_csv(src/'links_por_fonte.csv') or read_csv(src/'links_encontrados.csv')
    buckets={k:[] for k in ('YouTube','Cursos_Online','Sites_Oficiais','Outras_Fontes')}
    uniq=set()
    for r in links:
        url=r.get('url','').strip()
        ident=(r.get('pesquisa',''),r.get('fonte',''),url)
        if not url or ident in uniq: continue
        uniq.add(ident)
        if 'youtube.com' in url or 'youtu.be' in url: bucket='YouTube'
        elif any(x in url for x in ('inep','gov.br','cps.sp.gov.br','fatec.sp.gov.br')): bucket='Sites_Oficiais'
        elif re.search(r'curso|aula|educa|learn',url,re.I): bucket='Cursos_Online'
        else: bucket='Outras_Fontes'
        buckets[bucket].append({k:r.get(k,'') for k in ('data','pesquisa','fonte','titulo','tipo','url')})
    for bucket,data in buckets.items():
        write_csv(root/'04_LINKS_E_REFERENCIAS'/bucket/'links.csv',['data','pesquisa','fonte','titulo','tipo','url'],data)


def organize(args):
    root=args.destino.expanduser().resolve(); src=SRC
    if not args.simular: init_dirs(root)
    if not args.preservar_nomes_download:
        summary=normalize_downloads(src,simulate=args.simular)
        if summary['alterados'] or summary['pendentes'] or summary['sem_acesso']:
            print('Nomes de downloads: ',summary)
    cat=root/'00_CATALOGO'
    mapping_path=cat/'correspondencias_gabaritos.csv'
    manual=verified_mapping(mapping_path) if mapping_path.exists() else {}
    source_rows=material_entries(src,root,args.entrada, args.extrair_zips and not args.simular)
    seen={}; organized=[]; revisions=[]; rename=[]; copies=0; conflicts=[]; multi=0
    for srcfile,row in source_rows:
        try: digest=sha256_file(srcfile)
        except OSError: continue
        if digest in seen:
            conflicts.append({'sha256':digest,'primeiro_arquivo':str(seen[digest]),'arquivo_duplicado':str(srcfile)})
            continue
        seen[digest]=srcfile
        info=inspect(srcfile,row,ocr=not args.sem_ocr)
        if digest in manual: apply_manual_mapping(info,manual[digest])
        folder=folder_of(info,root,srcfile)
        common=friendly_name(info,srcfile,digest)
        if info.exam=='ENEM' and info.kind in ('01_Provas','02_Gabaritos') and 'CONFLITO_' not in info.reason and 'MULTIPLOS_DIAS' not in info.reason:
            colors=info.colors or ['00_COR_NAO_IDENTIFICADA']
            # Nunca cruzar para outras provas sem evidencia no proprio gabarito ou verificacao manual.
            if len(colors)>1 and info.kind!='02_Gabaritos':
                colors=['00_COR_NAO_IDENTIFICADA']; info.status='revisar'; info.reason+=';VARIAS_CORES_NA_PROVA'
            if len(colors)>1 and (info.multiple_reason not in ('multiplas_cores_comprovadas_no_texto','correspondencia_manual_documentada') or (info.method=='pdf_ocr_amostrado' and info.multiple_reason!='correspondencia_manual_documentada')):
                colors=['00_COR_NAO_IDENTIFICADA']; info.status='revisar'; info.reason+=';GABARITO_MULTIPLO_NAO_COMPROVADO'
            # Conservador: jamais replicar gabaritos por cor/dia automaticamente.
            # Arquivos multi-cor permanecem em area de revisao, ate confirmacao
            # documental independente da heuristica.
            if info.kind=='02_Gabaritos' and (len(colors)>1 or info.day=='Ambos'):
                info.status='revisar'
                info.reason+=';GABARITO_MULTIPLO_REQUER_REVISAO'
                folders=[root/'99_A_CLASSIFICAR'/'03_GABARITOS_MULTIPLOS']
            elif info.kind=='02_Gabaritos' and info.day=='Ambos' and info.combined_days:
                # Um gabarito comprovadamente consolidado e associado aos dois dias
                # ganha uma cópia em cada pasta de dia, para ficar junto das provas.
                edition_root=folder.parents[2]
                folders=[edition_root/day_folder/color/info.kind
                         for day_folder in ('1_Dia','2_Dia') for color in colors]
            else:
                folders=[folder.parent.parent/color/info.kind for color in colors]
        elif info.exam=='FATEC' and info.kind in ('01_Provas','02_Gabaritos') and 'CONFLITO_' not in info.reason and 'MULTIPLAS_VERSOES' not in info.reason:
            versions=info.versions or ['00_VERSAO_NAO_IDENTIFICADA']
            if len(versions)>1 and 'VERSOES_SEM_COMPROVACAO_NO_CONTEUDO' in info.reason:
                versions=['00_VERSAO_NAO_IDENTIFICADA']
            folders=[folder.parent.parent/f'Versao_{version}'/info.kind if version in ('A','B','C','D') else folder.parent.parent/version/info.kind for version in versions]
        else: folders=[folder]
        for target in folders:
            dest=target/common
            if args.simular:
                print('[SIMULACAO]',srcfile.name,'=>',dest.relative_to(root),'|',info.reason or 'OK')
            else:
                target.mkdir(parents=True,exist_ok=True)
                if dest.exists() and sha256_file(dest)!=digest:
                    dest=target/(dest.stem+'_'+digest[:16]+dest.suffix)
                if not dest.exists(): shutil.copy2(srcfile,dest);copies+=1
            line=output_row(info,row,srcfile,dest,digest)
            organized.append(line)
            rename.append({'nome_original':srcfile.name,'nome_organizado':dest.name,'pasta_destino':str(target),'sha256':digest,
                           'criterio':info.evidence,'revisao_pendente':info.reason})
            if info.reason: revisions.append(line)
        if len(folders)>1: multi+=len(folders)-1
    if args.simular:
        print(f'Simulacao concluida: {len(source_rows)} entradas, {len(organized)} destinos, {len(revisions)} entradas a revisar.')
        return 0
    previous=cat/'catalogo_automatico.csv'
    backup=cat/'catalogo_automatico_antes_da_reorganizacao.csv'
    if previous.is_file() and not backup.exists():
        old=read_csv(previous)
        if old and ('arquivo_completo' not in old[0]): shutil.copy2(previous,backup)
    write_csv(previous,OUT_FIELDS,organized)
    write_csv(cat/'revisao_pendente.csv',OUT_FIELDS,revisions)
    write_csv(cat/'relatorio_renomeacoes.csv',RENAME_FIELDS,rename)
    write_csv(cat/'duplicatas_de_entrada.csv',['sha256','primeiro_arquivo','arquivo_duplicado'],conflicts)
    if not mapping_path.exists():
        write_csv(mapping_path,['sha256','ano','semestre','dia','aplicacao','caderno','cores','versoes','justificativa','fonte_verificacao'],[])
    record_links(src,root)
    for source in ('status_fontes.csv','erros.csv','arquivos_baixados.csv','links_encontrados.csv','links_por_fonte.csv','relatorio_renomeacoes_downloads.csv'):
        file=src/source
        if file.is_file(): shutil.copy2(file,cat/source)
    template=BASE/'Catalogo_Acervo_Vestibulares.xlsx'
    if template.is_file() and not (cat/template.name).exists(): shutil.copy2(template,cat/template.name)
    print(f'Acervo: {root}')
    print(f'Entradas distintas: {len(seen)} | Copias novas: {copies} | Destinos catalogados: {len(organized)} | Gabaritos associados a multiplas cores (copias adicionais): {multi}')
    print(f'A revisar: {len(revisions)} | Entradas duplicadas ignoradas: {len(conflicts)}')
    print('Relatorios: 00_CATALOGO/catalogo_automatico.csv, relatorio_renomeacoes.csv, revisao_pendente.csv')
    print('ATENCAO: downloads catalogados podem ser RENOMEADOS (nao excluidos); nomes antigos e links ficam no backup do CSV e no relatorio.')
    print('Copias de pastas anteriores nao sao apagadas. Revise pendencias antes de confiar na correspondencia de gabaritos.')
    return 0


def main(argv=None):
    p=argparse.ArgumentParser(description='Organiza por prova/dia/cor, lendo o conteudo interno e renomeando somente COPIAS')
    p.add_argument('--destino',type=Path,default=Path.home()/'Documents'/'Acervo_Vestibulares')
    p.add_argument('--entrada',type=Path,help='Pasta com outros documentos ja existentes (sem cadastro previo no coletor)')
    p.add_argument('--sem-ocr',action='store_true',help='Nao tentar OCR nos PDFs escaneados')
    p.add_argument('--extrair-zips',action='store_true',help='Opcional: extrai documentos dentro dos ZIPs com limites de seguranca')
    p.add_argument('--simular',action='store_true',help='Mostra destinos/nome sem copiar nem alterar o acervo')
    p.add_argument('--preservar-nomes-download',action='store_true',help='Opcional: mantem nomes antigos em resultados/materiais')
    args=p.parse_args(argv)
    try:return organize(args)
    except (OSError,ValueError) as exc:
        print('Erro durante a organizacao:',exc,file=sys.stderr); return 1


if __name__=='__main__':raise SystemExit(main())
