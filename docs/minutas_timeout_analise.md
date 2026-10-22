# Diagnóstico do timeout em “Analisar fundamentos”

Análise local realizada em 22/10/2026 sobre o commit `f75e9ab` e seu pai
`ed9c66a`. Não houve acesso à produção, chamada ao Gemini real, migration,
alteração de banco, commit, push ou rollback.

## Causa e relação com `f75e9ab`

As duas ocorrências descritas — aproximadamente 120,1 segundos, uma tentativa,
sem retry, fallback ou HTTP — correspondem exatamente ao caminho de
`TimeoutError`/`URLError(TimeoutError)` de `_executar`. Nesse caminho o provedor
não entregou resposta HTTP antes do limite de `urlopen`; a telemetria registra
`categoria_erro=timeout`, `http_status=NULL`, uma tentativa e nenhum fallback.
Isso distingue a ocorrência de validação local, erro de schema, HTTP 4xx/5xx,
quota e falha de conexão imediatamente detectável.

A extração do PDF ocorre quando o usuário inclui o arquivo na instrução, antes do
clique em “Analisar fundamentos”. No clique são executadas consolidação,
reconciliação de metadados, serialização e chamada ao provedor. Portanto, uma
espera coincidente com 120 segundos não é explicada pela extração local.

O diff entre `ed9c66a` e `f75e9ab` comprova que o prompt
`PROMPT_ANALISE_MINUTA`, `_MINUTA_ANALYSIS_SCHEMA`, o limite de contexto, o
limite de saída da análise e `services/minutas_instrucao.py` não mudaram. As duas
linhas alteradas em `ai_service.py` pertenciam ao prompt da etapa posterior de
geração da minuta. Antes da análise, a nova identidade apenas percorre o texto já
presente no contexto e acrescenta `processo_alvo.responsavel` quando seguro.

Em uma amostra sintética representativa de 53 páginas e 252.874 bytes de contexto:

| Medida | Resultado |
| --- | ---: |
| Contexto efetivo anterior estimado | 253.031 bytes |
| Contexto após `f75e9ab` | 253.071 bytes |
| Aumento da reconciliação | 40 bytes |
| Consolidação local | 93,81 ms |
| Reconciliação do responsável | 171,93 ms |
| Estimativa conservadora por caracteres/4 | 63.268 tokens |

Assim, a hipótese de crescimento significativo causado por `f75e9ab` foi
descartada. O volume de 53 páginas continua relevante para a latência do modelo,
mas não foi introduzido pelo commit. Sem o PDF real e sem uma chamada ao Gemini,
não é possível separar conclusivamente lentidão do provedor de latência causada
pelo conteúdo específico. A causa comprovada das ocorrências é silêncio do
provedor até o timeout de comunicação, não falha local.

## Correção

Somente `minutas_analise` passou a ter recuperação controlada de timeout:

- modelo principal: limite de 70 segundos;
- após timeout completo, uma única chamada ao modelo reserva: 45 segundos;
- nenhum retry adicional se a reserva falhar por timeout, conexão ou HTTP;
- orçamento nominal somado de 115 segundos, abaixo do timeout global de 120;
- demais operações de IA mantêm a política existente e o timeout global não foi
  aumentado.

Respostas do modelo principal concluídas em até 70 segundos seguem inalteradas.
Uma resposta que antes chegasse entre 70 e 120 segundos agora será substituída
pela tentativa reserva; esse é o trade-off necessário para oferecer recuperação
sem manter a interface presa por duas janelas completas de 120 segundos. O
resultado reserva passa pelo mesmo schema e pelas mesmas validações documentais.

O tempo de rede de `urllib` é um timeout de operação de socket, não um cronômetro
absoluto de toda a função. Portanto, 115 segundos é o orçamento nominal das duas
esperas, não uma garantia rígida de tempo de parede em toda condição de rede.

A consolidação documental não foi alterada. Não houve truncamento adicional,
resumo heurístico ou perda deliberada de irregularidades, alegações, conclusões,
valores ou páginas. Contextos acima do limite continuam preservando apontamentos
identificáveis ou falhando de modo explícito se não puderem ser reduzidos com
segurança.

## Instrumentação

Foram adicionadas métricas sem conteúdo documental, nomes, chaves ou prompts:

- extração: duração, bytes do PDF, páginas e bytes de texto;
- consolidação: duração, bytes de entrada/saída e páginas omitidas;
- contexto: documentos, unidades, páginas, bytes e omissões;
- metadados: duração, tamanho final, presença de responsável e quantidade de
  pendências;
- prompt: bytes e estimativa aproximada de tokens;
- provedor: início/fim, modelo, tentativa, bytes da requisição/resposta e timeout;
- resposta: duração e tokens reais quando fornecidos pelo Gemini;
- validação e recuperação jurídica: duração e contagens.

A telemetria persistente existente continua sem armazenar documentos ou prompts e
passa a registrar o fallback e as duas tentativas quando esse caminho ocorrer.
Não foi criada ou alterada tabela.

## Estado da interface

O `spinner` permanece limitado ao bloco da chamada e é encerrado por exceção. Ao
iniciar nova análise da instrução, análise e minuta anteriores são removidas para
que uma falha não pareça sucesso da tentativa atual. PDFs, classificação,
processo, exercício e origem permanecem no `session_state`; o botão pode ser
acionado novamente. A mensagem de timeout informa explicitamente que documentos
e metadados foram preservados. Nenhuma gravação no acervo ocorre nesse fluxo.

## Arquivos modificados

| Arquivo | Finalidade |
| --- | --- |
| `services/ai_service.py` | Timeout/fallback exclusivo da análise, métricas do provedor e mensagem de falha. |
| `services/minutas_consistencia.py` | Conciliação localizada e segura do responsável e do período. |
| `services/minutas_generation.py` | Tempos, propagação de identidade e competência documental de governo/gestão. |
| `services/minutas_instrucao.py` | Métricas de extração e consolidação. |
| `services/minutas_ia_ui.py` | Estado de nova tentativa e apresentação única do bloqueio de identidade. |
| `tests/test_ai_resiliencia.py` | Timeout, fallback único, telemetria, orçamento e ausência de conteúdo nos logs. |
| `tests/test_minutas_consistencia.py` | Narrativa extensa, exercício, conflitos, evidência e duplicidade visual. |
| `tests/test_minutas_generation.py` | Competência de governo e gestão com e sem suporte nas peças atuais. |
| `tests/test_minutas_instrucao.py` | Contextos de 1/53 páginas, preservação de irregularidades e estado da interface. |

## Limitações de homologação

Os testes usam respostas controladas e documentos sintéticos. Eles demonstram a
política local, o contexto enviado e a integridade do estado, mas não substituem
uma execução com o PDF real de 53 páginas, credenciais e modelos configurados no
Streamlit Cloud. A instrumentação permitirá distinguir, na próxima ocorrência,
volume/preparação, primeiro modelo, fallback e validação. Nenhum arquivo DOCX ou
modelo visual foi modificado.

## Diagnóstico complementar: identidade e competência da PCA

Após a análise voltar a responder, foi reproduzida uma causa determinística para
o bloqueio indevido de identidade. A extração da instrução normaliza os espaços
de cada página e, por isso, uma página extensa pode chegar à conciliação como uma
única linha. A implementação anterior descartava a linha inteira quando ela
continha qualquer ano diferente do exercício ou termos processuais como
`auditor`, `advogado`, `relator` e `atual`. Assim, uma autuação em 2025 ou a
menção a um auditor em outro trecho eliminava também a relação documental
válida entre Sebastião Pinto Dantas, o cargo de Prefeito e o exercício de 2024.

A resolução agora avalia o contexto local da relação nome–cargo ou
nome–responsabilidade, delimitado por frase ou oração. Foram incluídas
construções narrativas como “contas sob a responsabilidade de” e “responsável
pelas contas”. Anos só excluem um candidato quando estão vinculados localmente a
exercício, gestão, contas ou período incompatível. Prefeito atual, ex-gestor e
papéis processuais continuam rejeitados, e múltiplos nomes sem períodos seguros
continuam bloqueando a exportação. O desempate não usa RAG, parecer histórico ou
texto produzido pela IA; a evidência permanece vinculada à fonte, página e
trecho das peças do processo atual.

Também foi localizada uma regra excessiva na competência: toda PCA de Prefeitura
com conclusão de gestão e sem pedido de parecer prévio recebia a advertência de
“conclusão apenas de gestão”, mesmo sem demonstração documental de contas de
governo. A advertência agora depende de referência explícita, nas peças atuais,
a contas de governo ou à competência para emissão de parecer prévio. A regra não
cria nem força parecer prévio; quando a competência de governo está documentada
e falta conclusão correspondente, a revisão jurídica continua sinalizada.

A pendência de identidade deixou de integrar simultaneamente a lista de
advertências facultativas. A interface a exibe uma vez como bloqueio e não tenta
preparar o DOCX enquanto ela existir. A validação no serviço DOCX permanece como
segunda barreira. Uma confirmação manual assistida foi avaliada, mas não foi
introduzida: após a correção, o caso documental unívoco é resolvido
automaticamente; casos realmente ambíguos permanecem bloqueados com evidências,
sem permitir nome arbitrário ou seleção derivada de parecer histórico.

As regressões adicionais cobrem uma página extensa do processo 02659/25 com
autuação em 2025, auditor e advogado em trechos distantes, gestor atual de 2025 e
Sebastião Pinto Dantas relacionado às contas de 2024; período divergente;
gestor apenas atual; supressão da duplicidade visual; e PCA de Prefeitura com e
sem suporte documental de contas de governo.
