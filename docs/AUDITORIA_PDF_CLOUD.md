# Auditoria de recursos da conversão PDF

## Diagnóstico

A causa do incidente no Cloud não foi confirmada: não houve acesso aos logs,
à memória do container nem ao estado da implantação. Um pico de memória é
compatível com o relato, mas a indisponibilidade persistente após reboot e a
falha dos Cloud Logs também exigem investigação da plataforma/implantação.

Riscos encontrados no código anterior:

- `subprocess.run(timeout=55)` não assegurava o encerramento dos descendentes
  do wrapper do LibreOffice.
- Sessões diferentes podiam executar várias conversões simultaneamente.
- Cada registro podia deixar uma tupla de caminho e bytes DOCX/PDF no
  `session_state`, mesmo depois de navegar para outro registro.
- `capture_output=True` acumulava stdout/stderr em memória.

## Alterações

`document_generator/pdf.py` limita a uma conversão por processo Python,
compartilhada pelas sessões Streamlit. Uma tentativa concorrente recebe uma
mensagem recuperável, sem ficar em fila. O lock é liberado em `finally`.

`document_generator/office_process.py` executa somente a árvore criada para
esta conversão. Nenhum processo é encerrado por nome ou varredura global.

- Linux/POSIX: `Popen` com lista de argumentos, sem shell, `start_new_session=True`
  e streams em `DEVNULL`. Espera de 55 segundos. Em `finally`, `SIGKILL` é
  enviado ao grupo privado, inclusive após sucesso do wrapper, e o processo
  direto é recolhido com espera limitada a 5 segundos.
- Windows/LibreOffice: processo criado suspenso, associado a um Job Object com
  `KILL_ON_JOB_CLOSE`, e só então retomado. O job é terminado em `finally` e
  seus handles fechados, atingindo também descendentes. Se a associação falhar,
  o processo ainda suspenso é terminado. Timeout de 55 segundos e espera final
  limitada a 5 segundos. Erros Win32 são normalizados como falhas recuperáveis.
- Windows/Word: estratégia anterior preservada, incluindo fallback para
  LibreOffice no modo automático e encerramento normal do Word pelo worker.

O encerramento dos processos antecede a limpeza do diretório temporário. Um
`finally` explícito remove DOCX, PDF intermediário e perfil exclusivo. O PDF só
é retornado após validar sua existência e assinatura. Os flags headless,
nologo, nodefault e norestore evitam interface, documento inicial e recuperação.

`app.py` deixa de guardar bytes de downloads em `session_state` e remove as
chaves antigas quando uma sessão é reexecutada. Os downloads usam o registro
atual e o PDF persistido pelo fluxo existente. Não há `st.cache_data` ou
`st.cache_resource` de documentos. As prévias guardadas são payloads, não
arquivos. `Store.history()` não carrega os blobs de documentos. O cache PDF
SQLite existente permanece inalterado; não é um cache global Python.

O próprio `st.download_button` mantém os dados do download em memória. Isso
continua necessário na versão usada do Streamlit; remover a tupla duplicada
da sessão não elimina esse custo. A API existente também recebe e retorna
bytes: não houve migração para streaming nem promessa de memória zero.

## Pacotes Debian

```text
libreoffice-writer-nogui
fonts-liberation
```

A variante sem interface gráfica mantém o motor Writer e depende do core sem
GUI (ou equivalente), evitando solicitar a suíte completa e seus aplicativos.
As fontes foram preservadas. Dependências e recomendações transitivas ainda
dependem do instalador APT do Cloud. Tamanho de instalação não equivale ao pico
de RAM; nenhuma redução percentual foi medida.

## Verificação

- Suíte completa: **151 testes aprovados**, 39,72 segundos.
- Comando: `.venv/Scripts/python.exe -m pytest -q --basetemp=tmp/pytest-office-full-final`.
- Testes simulados Linux: sucesso, retorno de erro, timeout, exceção, ausência
  do PDF, grupo encerrado antes da remoção do perfil e recolhimento do processo.
- Testes Windows: Job Object, falhas na criação/associação/retomada, fechamento
  de handles; testes reais com processo pai e filho após sucesso, erro e timeout.
- Testes de interface: downloads continuam disponíveis após rerun, não ficam
  bytes nas chaves de sessão e falha de PDF preserva o DOCX.
- Conversão real via LibreOffice local dos exemplos 5, 6 e 8: sucesso, uma
  página cada; comparação visual com referências e pixels renderizados iguais
  aos exemplos da versão anterior. Nenhum processo soffice apareceu na
  verificação posterior.
- Black e `git diff --check`: aprovados.

Os testes e exemplos usaram bancos isolados e arquivos em `tmp/`. Não houve
finalização de ato real, alteração de dados de produção, migração, mudança de
numeração, conteúdo, templates ou layout. Não houve commit nem push.

## Limitações e publicação

A revisão está pronta para commit e push como mitigação testada. Ainda é
necessária validação no Cloud com logs e monitoramento de memória. Linux foi
simulado neste ambiente Windows; o pacote Debian nogui não foi executado aqui.
O pico de uma única conversão pode exceder a memória disponível.

O lock é por processo Python, não distribuído entre réplicas. `finally` não
executa se o próprio Python sofrer SIGKILL/OOM ou se o container for encerrado;
nesses casos a recuperação e remoção de processos/temporários dependem do
sistema operacional/container. Não se pode garantir disponibilidade do Cloud
apenas com limpeza no código da aplicação.

## Fontes técnicas

- [Debian: Writer sem GUI](https://packages.debian.org/bookworm/libreoffice-writer-nogui).
- [Microsoft: Job Objects e descendentes](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects).
- [Python: subprocess](https://docs.python.org/3.12/library/subprocess.html).
- [Streamlit 1.49: memória dos downloads](https://docs.streamlit.io/1.49.0/develop/api-reference/widgets/st.download_button).
