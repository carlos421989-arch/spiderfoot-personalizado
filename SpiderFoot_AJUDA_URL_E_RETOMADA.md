# Ajuda por módulo, URLs completas e retomada de scans

## Ajuda de módulos
Abra `http://127.0.0.1:5004/module-help` enquanto o SpiderFoot estiver em execução. A página lista os módulos carregados na instalação e exibe a descrição, eventos de entrada/saída e opções quando esses metadados existem. Alguns módulos não publicam todos esses campos; nesses casos, a página avisa que a informação não está declarada.

## URL completa no campo Target
O campo aceita URLs `http://` e `https://`, incluindo caminho e query string. O núcleo SpiderFoot não usa uma URL com caminho como tipo de semente independente; por isso, o backend converte a URL para o hostname e realiza OSINT sobre o host. Exemplo: `https://example.org/catalogo/item?id=5` inicia uma investigação do host `example.org`; não garante crawling ou análise específica de `/catalogo/item?id=5`. Para análise de uma página específica é necessário um módulo crawler/web dedicado e devidamente configurado.

## Retomada depois de fechar o programa
Os resultados/eventos já gravados no banco de dados do SpiderFoot permanecem salvos se o banco e os arquivos de configuração forem preservados. Porém, a arquitetura do scanner não persiste o estado interno das filas de cada módulo, nem os pedidos em andamento. Portanto, não é tecnicamente correto prometer continuar exatamente do ponto em que parou após fechar o processo. Scans que ficaram `ABORTED` podem ser refeitos com a configuração salva, mas isso cria uma nova execução e pode repetir consultas. Não exclua nem substitua o banco de dados para preservar os resultados anteriores.

Para uma retomada verdadeira no meio do processamento, seria necessário modificar o motor `sfscan.py` e os módulos para checkpoint durável das filas, estado dos módulos e deduplicação idempotente. Esta atualização não implementa checkpoint de execução e não adiciona um botão enganoso de “retomar exatamente de onde parou”.


## Ajuda dos módulos em português (v8)

A página `/module-help` apresenta descrições em português, significado de entradas/saídas e explicações para opções comuns. Os identificadores técnicos de módulos, opções e eventos permanecem inalterados. Para módulos e opções sem metadados suficientes, a página informa explicitamente que é necessário consultar a documentação/código original, em vez de inventar o significado.
