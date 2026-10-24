"""Fontes independentes oficiais. Catalogo SEM consultas automatizadas de dados pessoais."""

PUBLIC_SOURCES = (
    {
        'nome': 'Buscadores públicos',
        'descricao': 'As fontes configuradas na Minha Exposição Digital pesquisam textos indexados; não verificam identidade automaticamente.',
        'url': '/minha-exposicao',
        'tipo': 'Auditoria local',
        'autenticacao': 'Não (buscas abertas; opcionais podem exigir API key do provedor)',
    },
    {
        'nome': 'Receita Federal — consultar CNPJ',
        'descricao': 'Consulta cadastral de pessoas jurídicas, por canal oficial. Não é uma consulta de CPF particular.',
        'url': 'https://www.gov.br/pt-br/servicos/consultar-cadastro-nacional-de-pessoas-juridicas',
        'tipo': 'Serviço oficial externo',
        'autenticacao': 'Pode exigir gov.br conforme o serviço',
    },
    {
        'nome': 'Banco Central — Registrato',
        'descricao': 'Relatórios sobre sua própria vida financeira; acesso restrito ao titular na plataforma oficial.',
        'url': 'https://www.bcb.gov.br/meubc/registrato',
        'tipo': 'Serviço oficial externo',
        'autenticacao': 'Sim, identificação do titular',
    },
    {
        'nome': 'ANPD — direitos dos titulares',
        'descricao': 'Orientações para pedir acesso, correção ou eliminação de dados ao controlador responsável.',
        'url': 'https://www.gov.br/anpd/pt-br/assuntos/titular-de-dados',
        'tipo': 'Serviço oficial externo',
        'autenticacao': 'Depende do requerimento',
    },
    {
        'nome': 'Have I Been Pwned — exposição de e-mail',
        'descricao': 'Serviço independente de checagem de exposição de endereços. A consulta de dados detalhados pode exigir verificação/assinatura.',
        'url': 'https://haveibeenpwned.com/',
        'tipo': 'Serviço externo independente',
        'autenticacao': 'Conforme funcionalidade',
    },
)
