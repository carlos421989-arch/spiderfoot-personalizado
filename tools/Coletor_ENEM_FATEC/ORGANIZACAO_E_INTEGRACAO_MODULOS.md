# Organização inteligente v6: conteúdo interno, nomes e vídeos

## Downloads novos
Cada documento baixado é analisado após o download, antes da gravação final. A extração tenta ler texto de PDF, DOCX, PPTX e XLSX; também pode ler texto/CSV/HTML/JSON/XML e a lista de arquivos dentro de ZIP. O texto interno é combinado com consulta, título, URL e nome para classificar exame, ano, matéria, tipo de material e cor. O arquivo recebe nome descritivo quando o nome de origem é genérico ou um identificador opaco. O hash é mantido no nome para evitar colisões.

## Organizar arquivos que já existem ou foram baixados manualmente
Na interface do coletor, use “Gerar prévia (não mover)” antes de aplicar. Coloque os arquivos em `resultados/entrada_arquivos`. A prévia cria `resultados/relatorio_organizacao.csv`; a aplicação move os arquivos para `resultados/materiais/...` e registra `resultados/manifesto_organizacao.csv`. Não é necessário copiar para essa pasta os arquivos já baixados e catalogados pelo coletor.

## Vídeos e áudios locais
Arquivos locais de vídeo em `entrada_arquivos` são classificados por matéria/ano quando os metadados ou o nome permitirem e recebem numeração estável por pasta (`Video_001_...`, `Video_002_...`). Se `ffprobe` estiver instalado, título/autor/álbum/duração podem ajudar na identificação. Sem tags, usa nome e consulta de origem. Links de YouTube e de outras plataformas continuam como links; o coletor não baixa nem contorna restrições de plataformas de streaming.

## Limitações honestas
- PDFs digitalizados sem camada de texto precisam de OCR; esta versão não executa OCR automaticamente.
- Vídeos não são transcritos. O conteúdo de áudio não é analisado semanticamente sem uma ferramenta de transcrição configurada.
- Formatos antigos DOC/PPT binários, arquivos protegidos por senha e formatos não reconhecidos podem ficar classificados pelo nome, consulta e URL.
- Classificações e gabaritos compartilhados são inferências, não validação oficial. Revise os relatórios antes de confiar nas associações.
- A prévia não move arquivos. A ação de organização real move os arquivos da pasta de entrada e mantém manifesto de origem/destino.
