#!/usr/bin/env python3
"""Relatorio seguro de downloads incompletos; nao apaga, renomeia ou retoma arquivos."""
from __future__ import annotations
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
SRC = BASE / 'resultados'
TEMP_SUFFIXES = ('.part', '.crdownload', '.download', '.tmp')
FIELDS = ['arquivo', 'existe_no_disco', 'tamanho_bytes', 'ultima_modificacao', 'idade_horas',
          'url_origem', 'pesquisa', 'titulo', 'status_sugerido', 'orientacao']


def read_index(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def is_temporary(value: str) -> bool:
    return Path(value).name.lower().endswith(TEMP_SUFFIXES)


def create_report(src: Path = SRC, age_hours: float = 24.0) -> tuple[Path, list[dict]]:
    index_rows = read_index(src / 'arquivos_baixados.csv')
    metadata: dict[str, dict] = {}
    for row in index_rows:
        rel = (row.get('arquivo') or '').strip()
        if rel and is_temporary(rel):
            # O relatorio nao aceita caminhos absolutos nem traversal.
            rp = Path(rel)
            if not rp.is_absolute() and '..' not in rp.parts:
                metadata[str(rp)] = row
    material_root = src / 'materiais'
    if material_root.exists():
        for p in material_root.rglob('*'):
            if p.is_symlink() or not p.is_file() or not is_temporary(p.name):
                continue
            try:
                rel = str(p.relative_to(src))
            except ValueError:
                continue
            metadata.setdefault(rel, {})
    now = datetime.now().astimezone()
    rows = []
    for rel, source_row in sorted(metadata.items()):
        p = src / rel
        exists = p.is_file()
        size = ''
        modified = ''
        age = ''
        if exists:
            try:
                stat = p.stat()
                size = stat.st_size
                stamp = datetime.fromtimestamp(stat.st_mtime).astimezone()
                modified = stamp.isoformat(timespec='seconds')
                age_value = max(0.0, (now - stamp).total_seconds() / 3600)
                age = round(age_value, 2)
            except OSError:
                age_value = 0.0
        else:
            age_value = None
        if not exists:
            status = 'REGISTRO_SEM_ARQUIVO'
            orient = 'O CSV aponta para um temporario ausente; conferir o historico antes de refazer o download.'
        elif age_value is not None and age_value >= age_hours:
            status = 'ANTIGO_REVISAR'
            orient = 'Temporario antigo; conferir se ha coleta ativa e se o download pode ser retomado. Nao apagar automaticamente.'
        else:
            status = 'RECENTE_OU_EM_ANDAMENTO'
            orient = 'Aguardar a conclusao ou interromper a coleta corretamente; nao renomear enquanto for temporario.'
        rows.append({
            'arquivo': rel, 'existe_no_disco': 'sim' if exists else 'nao', 'tamanho_bytes': size,
            'ultima_modificacao': modified, 'idade_horas': age,
            'url_origem': source_row.get('url', ''), 'pesquisa': source_row.get('pesquisa', ''),
            'titulo': source_row.get('titulo', ''), 'status_sugerido': status, 'orientacao': orient,
        })
    report = src / 'relatorio_downloads_incompletos.csv'
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    return report, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='Gera relatorio de downloads temporarios sem modificar os arquivos.')
    parser.add_argument('--resultados', type=Path, default=SRC, help='Pasta resultados do coletor')
    parser.add_argument('--idade-horas', type=float, default=24.0, help='Idade a partir da qual o temporario e marcado para revisao (padrao: 24)')
    args = parser.parse_args(argv)
    report, rows = create_report(args.resultados, max(0.0, args.idade_horas))
    counts = {}
    for row in rows:
        counts[row['status_sugerido']] = counts.get(row['status_sugerido'], 0) + 1
    print(f'Relatorio criado: {report}')
    print(f'Temporarios encontrados: {len(rows)} | Resumo: {counts}')
    print('Nenhum arquivo foi apagado, renomeado ou retomado.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
