# -*- coding: utf-8 -*-
# AUDITORIA_MATCHER_ORIGINAL_V5
# As tres funcoes abaixo sao uma copia LITERAL do codigo enviado.
# O rótulo identidade_confirmada e um marcador tecnico, NAO e prova
# de que a pessoa do resultado seja a pessoa pesquisada.
import html
import re
from urllib.parse import urlparse, unquote


def _normalize_words(value: str) -> str:
    """Normaliza acentos e pontuacao para comparar FRASES INTEIRAS."""
    import unicodedata
    value = html.unescape(unquote(str(value)))
    value = unicodedata.normalize('NFKD', value).casefold()
    value = ''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[a-z0-9]+', value))

def _digits_match(value: str, evidence: str) -> bool:
    """Correspondencia completa, aceitando separadores comuns em telefone/CPF."""
    digits = re.sub(r'\D', '', value)
    if not digits:
        return False
    pattern = r'(?<!\d)' + r'[\s().+\-]{0,3}'.join(map(re.escape, digits)) + r'(?!\d)'
    return re.search(pattern, evidence) is not None

def _matches_from_index(item: dict, fields: dict) -> list:
    """
    Busca exaustiva de identidade:
    1. Verifica se o Nome Completo aparece no Título ou Snippet.
    2. Verifica se os Identificadores Únicos (Email, CPF, Telefone) aparecem.
    3. Verifica se a URL contém o nome de usuário ou o nome da pessoa.
    """
    parsed = urlparse(item.get('url', ''))
    texts = [item.get('title') or '', item.get('snippet') or '',
             unquote(parsed.path or '')]
    found = []

    # 1. Nome Completo (Exige que as palavras do nome estejam presentes na frase)
    name_target = _normalize_words(fields.get('nome', ''))
    if len(name_target.split()) >= 2 and any(
        ' ' + name_target + ' ' in ' ' + _normalize_words(t) + ' '
        for t in texts
    ):
        found.append('nome')

    # 2. Identificadores Únicos (Email, CPF, Telefone)
    for field in ['email', 'telefone', 'cpf']:
        val = fields.get(field, '')
        if not val: continue
        
        if field == 'email':
            val_clean = val.casefold()
            pattern = r'(?<![a-z0-9_.+\-])' + re.escape(val_clean) + r'(?![a-z0-9_.\-])'
            if any(re.search(pattern, html.unescape(unquote(t)).casefold()) for t in texts):
                found.append('email')
        
        elif field == 'telefone':
            variants = [re.sub(r'\D', '', val)]
            if val.startswith('55') and len(variants[0]) in (12, 13):
                variants.append(variants[0][2:])
            if any(_digits_match(v, str(t)) for v in variants for t in texts):
                found.append('telefone')
                
        elif field == 'cpf':
            variants = [re.sub(r'\D', '', val)]
            if any(_digits_match(v, str(t)) for v in variants for t in texts):
                found.append('cpf')

    # 3. Identificador de Usuário (Redes Sociais e Apps)
    user_val = fields.get('usuario', '').casefold().lstrip('@')
    if user_val:
        pattern = r'(?<![a-z0-9_.\-])@' + re.escape(user_val) + r'(?![a-z0-9_.\-])'
        in_text = any(re.search(pattern, str(t).casefold()) for t in texts[:2])
        segments = [x.casefold().lstrip('@') for x in unquote(parsed.path).strip('/').split('/')]
        if in_text or user_val in segments:
            found.append('usuario')

    # 4. Expansão de Relevância (Onde a pessoa "vive" digitalmente)
    # Se a página contém um dos dados únicos, ela é considerada "Alta Relevância"
    if found:
        found.append('identidade_confirmada')
    
    return found

