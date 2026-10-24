# Integração SpiderFoot: scans em lote + coletor ENEM/FATEC

Esta edição integra duas ferramentas ao código-fonte fornecido:

1. **Dashboard / scans em lote**: seleção por caixa de seleção, selecionar todos os scans carregados, parar, refazer, excluir com confirmação e exportar um ZIP com logs (CSV) e resultados (CSV/JSON) por scan.
2. **Coletor ENEM/FATEC**: página `http://127.0.0.1:5004/coletor-enem-fatec`, com modo de coleta, limite de pesquisas, limite de arquivos, anos, início/interrupção, log de execução e download ZIP dos resultados.

## Conteúdo incluído

- `spiderfoot/blueprints/collector.py` e registro em `spiderfoot/app.py`.
- `spiderfoot/templates/pages/collector_enem_fatec.html` e link de navegação.
- seleção múltipla no Dashboard e endpoint `/api/scanbatchzip`.
- `tools/Coletor_ENEM_FATEC/` com o script e as listas de consultas fornecidas.
- dependência `ddgs` acrescentada a `requirements.txt`.

## Instalação

1. Faça backup da pasta da instalação e do banco de dados do SpiderFoot.
2. Extraia o pacote de integração na pasta do código-fonte, mantendo a estrutura de diretórios. Se estiver aplicando sobre a pasta `SpiderFoot-Final`, permita substituir os arquivos modificados listados no manifesto.
3. Ative o ambiente virtual usado pelo SpiderFoot e instale a dependência: `python -m pip install -r requirements.txt`.
4. Reinicie o SpiderFoot.
5. Abra o Dashboard e teste a seleção múltipla com scans de teste. O ZIP exporta apenas scans selecionados; a exclusão é irreversível.
6. Abra `/coletor-enem-fatec`. Comece com 5 pesquisas e 25 arquivos para validar o ambiente. Depois, defina limite de pesquisas `0` para percorrer todas as 235 consultas únicas.

## Segurança e limitações

- O coletor baixa apenas arquivos públicos aceitos pelo próprio script; não contorna autenticação, paywalls, CAPTCHA nem bloqueios de acesso.
- Os resultados do SpiderFoot e os documentos ENEM/FATEC ficam em diretórios diferentes. O ZIP do coletor inclui o conteúdo da pasta `tools/Coletor_ENEM_FATEC/resultados/`.
- A interrupção tenta enviar um sinal amigável para que o coletor preserve o progresso registrado; resultados parciais podem ser baixados após o processo parar.
- A compatibilidade foi verificada estaticamente e por compilação dos módulos Python. A instalação e o navegador local do usuário não estão acessíveis neste ambiente; execute os testes no seu computador antes de usar em produção.
- O pacote fornecido `SpiderFoot-Final-Dados.zip` também contém grandes arquivos de recuperação e bancos de dados. Eles não são copiados para o pacote de integração, para evitar substituir ou expor dados locais.
\n\n## Atualização adicional: ajuda, URL completa e retomada\n\n- A página `/module-help` apresenta os metadados dos módulos carregados (descrição, entradas/saídas quando declaradas e opções disponíveis).\n- O campo Target aceita URLs completas HTTP/HTTPS; o SpiderFoot converte a URL para o hostname para OSINT. O caminho/query não é tratado como alvo autônomo pelo núcleo.\n- Resultados já gravados continuam no banco após encerrar o processo, desde que o banco seja preservado. Retomar exatamente no meio de uma execução não é suportado pelo motor sem checkpoints persistentes; scans abortados podem ser refeitos com nova execução. Veja `SpiderFoot_AJUDA_URL_E_RETOMADA.md`.\n

## Mecanismos de busca do coletor (atualização)

A tela `/coletor-enem-fatec` agora permite selecionar fontes. O núcleo usa os backends da biblioteca `ddgs` para DuckDuckGo, Brave, Yandex, Google, Bing, Yahoo, Mojeek e Wikipedia. Adaptadores HTML opcionais tentam consultar Swisscows, Naver, Gibiru, Ecosia, MillionShort, YouTube, Boardreader, Internet Archive Scholar, AOL, Search Encrypt, Ask, Wiki.com, Gigablast, Blackle e Baidu; como os formatos dos sites mudam e alguns exigem JavaScript, esses adaptadores podem não produzir resultados. Openverse é incluído para mídia com licença. SearXNG requer a URL de uma instância real. Neeva está encerrado; Creative Commons Search antigo foi substituído por Openverse; Searchcode é especializado em repositórios de código. O relatório `resultados/mecanismos_busca.csv` mostra o estado de cada fonte.


O coletor registra progresso por par consulta/mecanismo em `resultados/pesquisas_mecanismos_concluidas.csv`. Assim, consultas marcadas como concluídas por versões antigas ainda podem ser executadas nos novos mecanismos selecionados. Falhas e resultados de cada fonte ficam em `resultados/mecanismos_busca.csv`.
