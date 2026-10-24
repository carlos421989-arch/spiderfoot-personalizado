#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Prepara copias MP4 grandes para envio: comprimir (2 passagens) ou dividir.

Nao apaga nem substitui o arquivo original. Nao acessa a rede, nao altera
historico de downloads e nao inclui derivados nos orcamentos do coletor.
"""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

VIDEO_EXTENSIONS={'.mp4','.mkv','.webm','.mov','.m4v','.avi','.mpg','.mpeg'}

def size_bytes(gb):
    number=float(gb)
    if not math.isfinite(number) or not 0.001 <= number <= 100:
        raise ValueError('O limite deve ser um numero finito entre 0,001 e 100 GB decimais.')
    return int(number*1_000_000_000)

def check_dependencies():
    ffmpeg=shutil.which('ffmpeg')
    ffprobe=shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        raise RuntimeError('FFmpeg e ffprobe nao encontrados no PATH. Instale FFmpeg e reinicie SpiderFoot.')
    return ffmpeg,ffprobe

def probe_duration(path:Path, ffprobe:str)->float:
    run=subprocess.run([ffprobe,'-v','error','-show_entries','format=duration',
        '-of','default=noprint_wrappers=1:nokey=1',str(path)],
        capture_output=True,text=True,timeout=90)
    if run.returncode:
        raise RuntimeError('ffprobe falhou: '+run.stderr[-900:])
    try:
        duration=float(run.stdout.strip())
    except ValueError as exc:
        raise RuntimeError('Duracao nao encontrada no arquivo.') from exc
    if not math.isfinite(duration) or duration<=0:
        raise RuntimeError('Duracao invalida; nao e possivel dimensionar as partes.')
    return duration

def run_ffmpeg(cmd):
    # O progresso detalhado fica no log do coletor, sem gerar dezenas de MB no stdout.
    process=subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,
                           text=True,errors='replace')
    if process.returncode:
        raise RuntimeError('FFmpeg retornou erro '+str(process.returncode)+': '+(process.stderr or '')[-900:])

def publish(src:Path, destination:Path):
    # Nao sobrescrever resultados anteriores: operacao atomica no mesmo volume.
    if destination.exists():
        raise FileExistsError('A saida ja existe; arquivo preservado: '+str(destination))
    src.replace(destination)

def compress(path:Path, target:int, base:Path, ffmpeg:str, duration:float):
    output=base/(path.stem+'_telegram_compactado.mp4')
    if output.exists():
        if output.stat().st_size <= target:
            return [output], 'ja_preparado'
        raise FileExistsError('Ja existe uma saida acima do limite, remova somente a COPIA se quiser refazer: '+str(output))
    with tempfile.TemporaryDirectory(prefix='.v39_compactando_',dir=base) as work:
        work=Path(work)
        temp=work/'compactado.mp4'
        # Margem para audio, contenedores e variabilidade real do bitrate.
        max_bps=target*8/duration
        audio_bps=128_000
        video_kbps=max(30,int((max_bps*0.89-audio_bps)/1000))
        if video_kbps<=30 or max_bps<=audio_bps+30000:
            raise ValueError('Arquivo muito longo para o alvo informado; aumente o tamanho por arquivo.')
        for attempt in range(3):
            log=str(work/('pass_'+str(attempt)))
            bitrate=str(video_kbps)+'k'
            common=[ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y','-i',str(path),
                    '-map','0:v:0','-c:v','libx264','-preset','veryfast','-b:v',bitrate,
                    '-pix_fmt','yuv420p','-passlogfile',log]
            run_ffmpeg(common+['-pass','1','-an','-f','null',os.devnull])
            run_ffmpeg(common+['-pass','2','-map','0:a:0?','-c:a','aac','-b:a','128k',
                '-movflags','+faststart',str(temp)])
            if temp.is_file() and 0<temp.stat().st_size <= target:
                publish(temp,output)
                return [output], 'compactado'
            print('Compressao excedeu o tamanho configurado; nova tentativa com bitrate menor.',flush=True)
            temp.unlink(missing_ok=True)
            video_kbps=int(video_kbps*0.76)
            if video_kbps<30: break
        raise RuntimeError('Nao foi possivel gerar MP4 abaixo do limite; o original foi preservado.')

def split(path:Path,target:int,base:Path,ffmpeg:str,duration:float):
    output=base/(path.stem+'_telegram_partes')
    if output.exists():
        previous=sorted(output.glob('parte_*.mp4'))
        if previous and all(0<p.stat().st_size<=target for p in previous):
            return previous,'ja_preparado'
        raise FileExistsError('A pasta de saida ja existe e deve ser verificada manualmente: '+str(output))
    with tempfile.TemporaryDirectory(prefix='.v39_dividindo_',dir=base) as work:
        work=Path(work)
        source_size=path.stat().st_size
        # 20% de margem por trechos com bitrate variavel e arredondamentos de keyframe.
        segment_seconds=max(.10, duration*target/source_size*.80)
        for attempt in range(10):
            for candidate in work.iterdir():
                if candidate.is_file(): candidate.unlink()
            run_ffmpeg([ffmpeg,'-nostdin','-hide_banner','-loglevel','error','-y','-i',str(path),
                '-map','0:v:0','-map','0:a:0?','-c','copy','-f','segment',
                '-segment_time',f'{segment_seconds:.6f}',
                '-reset_timestamps','1','-segment_start_number','1',str(work/'parte_%03d.mp4')])
            pieces=sorted(work.glob('parte_*.mp4'))
            if pieces and all(0<p.stat().st_size<=target for p in pieces):
                publish(work,output)
                # Temporario sai do gerenciamento depois do move: context manager tolera caminho ausente.
                return sorted(output.glob('parte_*.mp4')),'dividido'
            print('Alguma parte excedeu o limite; tentando segmentos menores.',flush=True)
            segment_seconds*=.67
        raise RuntimeError('Nao foi possivel dividir em partes abaixo do limite com copia sem perda; '+
                           'o video pode ter poucos quadros-chave. Use o modo compactar.')

def process_video(path:Path, mode:str, trigger_gb:float=4.0, target_gb:float=3.5):
    path=Path(path).resolve()
    if mode not in ('compactar','dividir'):
        raise ValueError('Modo invalido: use compactar ou dividir.')
    threshold=size_bytes(trigger_gb)
    target=size_bytes(target_gb)
    if target>=threshold:
        raise ValueError('O tamanho por arquivo deve ser inferior ao limite de acionamento.')
    if not path.is_file() or path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise ValueError('Escolha um arquivo de video concluido: '+str(path))
    if path.name.lower().endswith(('.part','.ytdl')):
        raise ValueError('O download ainda nao terminou.')
    if path.stat().st_size<=threshold:
        return {'status':'abaixo_limite','original':str(path),'saida':[]}
    ffmpeg,ffprobe=check_dependencies()
    duration=probe_duration(path,ffprobe)
    output_folder=path.parent/'_telegram_preparados'
    output_folder.mkdir(exist_ok=True)
    files,state=(compress(path,target,output_folder,ffmpeg,duration) if mode=='compactar' else
                 split(path,target,output_folder,ffmpeg,duration))
    result={'data':datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'status':state,'modo':mode,'original':str(path),
            'bytes_original':path.stat().st_size,'limite_gb':trigger_gb,
            'tamanho_saida_gb':target_gb,'saida':[str(f) for f in files],
            'bytes_saidas':[f.stat().st_size for f in files]}
    if state!='ja_preparado':
        manifest=output_folder/'processamentos_telegram_v39.csv'
        exists=manifest.is_file() and manifest.stat().st_size>0
        with manifest.open('a',newline='',encoding='utf-8-sig' if not exists else 'utf-8') as handle:
            writer=csv.writer(handle)
            if not exists: writer.writerow(['data','modo','arquivo_original','bytes_original','arquivos_gerados','bytes_gerados','limite_gb','tamanho_saida_gb'])
            writer.writerow([result['data'],mode,str(path),result['bytes_original'],
                 '|'.join(result['saida']),'|'.join(str(n) for n in result['bytes_saidas']),trigger_gb,target_gb])
    return result

def batch(directory:Path, mode, trigger_gb,target_gb):
    folder=Path(directory).resolve()
    if not folder.is_dir(): raise ValueError('Pasta inexistente: '+str(folder))
    videos=sorted((x for x in folder.rglob('*') if x.is_file() and x.suffix.lower() in VIDEO_EXTENSIONS
                  and '_telegram_preparados' not in x.parts and not x.name.endswith(('.part','.ytdl'))),
                  key=lambda p:str(p))
    processed=0;errors=0
    for index,video in enumerate(videos,1):
        try:
            result=process_video(video,mode,trigger_gb,target_gb)
            if result['status']!='abaixo_limite': processed+=1
            print(json.dumps({'atual':index,'total':len(videos),**result},ensure_ascii=False),flush=True)
        except Exception as error:
            errors+=1
            print(json.dumps({'atual':index,'total':len(videos),'status':'erro','original':str(video),'detalhe':str(error)},ensure_ascii=False),flush=True)
    print(f'FIM: {processed} preparados; {errors} erros; originais preservados.',flush=True)
    return 1 if errors else 0

def main():
    p=argparse.ArgumentParser(description='V39: preparar videos grandes sem apagar os originais')
    x=p.add_mutually_exclusive_group(required=True)
    x.add_argument('--arquivo',type=Path)
    x.add_argument('--pasta',type=Path)
    p.add_argument('--modo',choices=('compactar','dividir'),required=True)
    p.add_argument('--limite-gb',type=float,default=4.0)
    p.add_argument('--saida-gb',type=float,default=3.5)
    args=p.parse_args()
    try:
        if args.arquivo:
            print(json.dumps(process_video(args.arquivo,args.modo,args.limite_gb,args.saida_gb),ensure_ascii=False),flush=True)
            return 0
        return batch(args.pasta,args.modo,args.limite_gb,args.saida_gb)
    except Exception as exc:
        print('ERRO V39: '+str(exc),file=sys.stderr,flush=True)
        return 1
if __name__=='__main__':sys.exit(main())
