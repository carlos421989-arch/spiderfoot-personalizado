COLETOR AUTOMATICO ENEM + FATEC
=================================

O que faz
----------
- Recebe as 400 linhas do arquivo original, elimina as repeticoes e usa 235 pesquisas unicas.
- Pesquisa na web e tenta baixar documentos PUBLICOS (.pdf, .doc(x), .ppt(x), .xls(x), .zip).
- Tambem percorre os arquivos de provas e gabaritos oficiais do INEP e da FATEC.
- Salva links de videoaulas, cursos e sites, mas NAO baixa videos do YouTube.
- Mantem registro CSV (abre no Excel), evita arquivos repetidos e permite retomar.

INSTALACAO (Windows)
-------------------
1. Instale Python 3.10 ou mais novo: https://www.python.org/downloads/
   Marque Add Python to PATH durante a instalacao.
2. Extraia o ZIP para uma pasta em seu computador.
3. Dê dois cliques em EXECUTAR_WINDOWS.bat e acompanhe a janela.
   A primeira execucao instala as dependencias e inicia a coleta.

INSTALACAO (macOS ou Linux)
--------------------------
1. Instale Python 3.10+.
2. Abra o Terminal nesta pasta e execute:
   python3 -m pip install -r requirements.txt
   python3 coletar_materiais.py --modo todos

RECOMENDADO: TESTE PEQUENO ANTES
--------------------------------
python3 coletar_materiais.py --modo pesquisas --limite-pesquisas 3 --limite-arquivos 5

SO ARQUIVOS OFICIAIS (mais confiavel)
-----------------------------------
python3 coletar_materiais.py --modo oficiais --ano-inicial 2024 --ano-final 2025 --limite-arquivos 20

TUDO: 235 PESQUISAS + FONTES OFICIAIS
------------------------------------
python3 coletar_materiais.py --modo todos --limite-arquivos 500

Se preferir coletar sem limite total de arquivos:
python3 coletar_materiais.py --modo todos --limite-arquivos 0

ONDE ESTAO OS RESULTADOS?
-------------------------
resultados/materiais/ENEM/<ano>/         -> arquivos baixados do ENEM
resultados/materiais/FATEC/<ano>/        -> arquivos baixados da FATEC
resultados/links_encontrados.csv         -> cursos, videos, links e documentos
resultados/arquivos_baixados.csv        -> downloads concluidos com tamanho e hash
resultados/erros.csv                    -> erros e bloqueios
resultados/execucao.log                 -> andamento da coleta

IMPORTANTE
----------
- Necessita de conexao com a internet NO SEU COMPUTADOR para rodar.
- As 235 pesquisas NAO representam 235 arquivos garantidos: muitos resultados sao paginas,
  nao materiais baixaveis, ou estao indisponiveis. Em particular 2026 ENEM ainda nao ocorreu.
- A pesquisa sem chave utiliza DDGS, biblioteca gratuita de metabusca que pode falhar ou sofrer
  limitacoes/rate limits. Se houver muitos erros, pare e execute novamente mais tarde.
- O programa nao passa por paywall, login, captcha nem protecao contra download.
- Respeite os direitos autorais e os termos dos sites. Nao redistribua conteudo protegido.
- Limites padrao: 500 arquivos novos por execucao; 45 MB por arquivo;
  6 resultados por pesquisa; 2 paginas exploradas por pesquisa.
- Quando o limite for atingido, aumente --limite-arquivos e repita para continuar.
- Pesquisas concluidas e fontes oficiais ja tentadas sao ignoradas em novas execucoes.
  Se desejar reexecutar tudo, remova os arquivos marcadores:
  resultados/pesquisas_concluidas.txt e resultados/fontes_oficiais_concluidas.txt
- Pode haver links de resultados sem PDF; nesses casos, leia links_encontrados.csv.
- Fontes oficiais: https://www.gov.br/inep/ (ENEM) e
  https://vestibular.fatec.sp.gov.br/ (FATEC).

Versao preparada em 21-10-2026. O script foi verificado estaticamente; downloads em massa
so acontecem quando voce executa no seu proprio computador com acesso a Internet.


MECANISMOS DE BUSCA ADICIONAIS
------------------------------
O coletor permite selecionar mecanismos na interface SpiderFoot. Os mecanismos nativos da biblioteca DDGS incluem DuckDuckGo, Brave, Yandex, Google, Bing, Yahoo, Mojeek e Wikipedia (a disponibilidade pode variar por versão). Fontes adicionais selecionáveis incluem Swisscows, Naver, Gibiru, Ecosia, MillionShort, YouTube, Boardreader, Internet Archive Scholar, AOL, Search Encrypt, Ask, Wiki.com, Gigablast, Blackle, Baidu e Openverse.

Limitações importantes:
- Adaptadores HTML são genéricos e podem deixar de funcionar se o provedor mudar o site, exigir JavaScript, CAPTCHA, login ou limitar consultas. O programa não contorna essas proteções.
- YouTube é coletado como lista de links; os vídeos não são baixados.
- Neeva foi encerrado em 2023. O antigo Creative Commons Search foi substituído por Openverse.
- docs.searxng.org é documentação, não um buscador. Para usar SearXNG, informe a URL base de uma instância real que você tenha permissão para consultar.
- Searchcode é especializado em busca/análise de código de repositórios públicos, não pesquisa web geral; fica listado como fonte especializada e não executa consulta genérica.
- Para listar IDs de mecanismos no terminal: python coletar_materiais.py --listar-mecanismos
- Para selecionar mecanismos via terminal: --mecanismos brave,yandex,google,duckduckgo
- A saída resultados/mecanismos_busca.csv registra resultados, falhas, fontes bloqueadas e fontes que precisam de configuração. O marcador resultados/pesquisas_mecanismos_concluidas.csv permite executar mecanismos recém-adicionados para consultas antigas sem repetir os mecanismos já tentados.
