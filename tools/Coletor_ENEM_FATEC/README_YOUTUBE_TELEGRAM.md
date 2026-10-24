# YouTube e Telegram público no Coletor ENEM/FATEC

## YouTube
- O mecanismo YouTube usa `yt-dlp` para pesquisar vídeos e registrar links.
- O download fica desligado por padrão. Ative a caixa na interface somente para vídeos seus, com licença que permita baixar ou com autorização do titular.
- Os arquivos são organizados em `resultados/materiais/ENEM/<ano>/Videoaulas/`, `FATEC` ou `OUTROS/<assunto>/<ano>/Videoaulas/`, conforme a consulta. Os nomes recebem sequência `Video_001_...`, `Video_002_...`.
- O limite de novos arquivos se aplica também aos vídeos. `yt-dlp` tenta baixar a melhor versão de arquivo único; FFmpeg pode ampliar as opções de formato, mas não é obrigatório para o fallback selecionado.
- O coletor não contorna login, DRM, paywall nem controles de acesso. Verifique as permissões/licença antes de baixar ou reutilizar o material.

Instalação na venv do SpiderFoot:
` .\.venv\Scripts\python.exe -m pip install yt-dlp `

## Telegram
- O mecanismo `telegram_public` pesquisa páginas `t.me`/`telegram.me` indexadas por buscadores e as prévias públicas de usernames que você informar.
- Na interface, informe usernames/URLs públicos, um por linha, na caixa “Canais públicos do Telegram”.
- Telegram não oferece uma pesquisa global anônima de todas as mensagens; resultados dependem do que está indexado e do que o canal publica em prévia. O recurso não acessa chats privados, grupos fechados, links de convite, nem contorna requisitos de entrada/autenticação.
- Os resultados ficam em `resultados/links_encontrados.csv` e `resultados/mecanismos_busca.csv`.


## Correção v11 (logs de 21/10/2026)
- Corrige `NameError: name 'limit' is not defined` na busca de páginas públicas do Telegram.
- Limita a busca indexada a backends conhecidos do DDGS; backends não suportados pela versão instalada são ignorados para essa subrotina.
- O Telegram continua limitado a páginas públicas indexadas e prévias públicas de canais informados; não existe pesquisa anônima global de todos os chats.
