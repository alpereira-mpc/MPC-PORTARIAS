# Backup V2 e continuidade operacional — Ferramentas MPC-PB

## Estado e limites

O portal gera um **backup lógico V2 do banco da aplicação**, com JSONL tipado, CSV de consulta, anexos originais e hashes SHA-256. SQLite dispõe de reconstrução automática em banco novo isolado. PostgreSQL dispõe de exportação lógica em transação somente leitura; **sua restauração automática permanece bloqueada**. Não houve conexão com produção, migração externa, envio real, criação de infraestrutura ou teste real PostgreSQL nesta implementação.

**Backup externo não configurado** é o estado padrão. ZIP temporário no Streamlit e download solicitado não comprovam cópia durável. Não existe proteção completa contra desastres até que armazenamento externo, monitoramento, credenciais de recuperação e ensaio institucional sejam configurados e comprovados.

## Diagnóstico corrigido

O inventário anterior omitia Tarefas/checklists/lembretes, Petições, relatórios institucionais e PDFs, Tramita, notificações e estruturas adicionais da Agenda. Representações e Ouvidoria tinham BLOBs mapeados sem pasta de documentos. CSV confundia NULL com texto vazio; linhas e documentos eram acumulados em memória. Não havia validador ou restauração.

`database/inventory.py` confronta o schema realmente conectado com a lista autorizada. Tabela desconhecida, coluna binária não inventariada ou ausência parcial de um módulo bloqueiam a geração. Grupos aditivos ainda não inicializados (Agenda, Ofícios, Petições, auditoria) podem estar integralmente ausentes e ficam declarados no manifesto. Ausência não significa exportação de um módulo vazio. Marcadores de Ofícios, auditoria e vínculos da Agenda impedem tratar um módulo já inicializado como ausente legítimo. O teste de cobertura compara também os CREATE TABLE de database/ e services/ com o inventário. Uma tabela temporária de migração é exceção apenas no teste de código; sua presença no banco bloqueia o backup.

A comparação com o schema de produção ocorre **quando o administrador gera o backup**, na mesma transação somente leitura. Não foi feita inspeção remota do Supabase durante o desenvolvimento.

## Formato e abrangência

- `manifest.json`: identificador, versão 2, horário UTC/local, versão do aplicativo, build quando disponível, engine/schema, contagens, exclusões e hashes/tamanhos de cada componente.
- `dados/<tabela>.jsonl`: células tipadas que distinguem NULL, texto vazio, inteiros, reais, booleanos, JSON, decimais, datas/horários e referências binárias. IDs e metadados são preservados.
- `dados/<tabela>.csv`: consulta humana; não é fonte para restauração. Abra textos não confiáveis sem habilitar execução de fórmulas na planilha.
- `documentos/`: bytes originais, inclusive BLOB vazio. Portarias, Memorandos, Ofícios/quarentena, Representações, Ouvidoria, Petições e PDFs institucionais têm pastas explícitas. Nome original, MIME e vínculos permanecem nos registros.
- `schema/schema_manifest.json`: colunas/tipos, fingerprints de definições SQLite e FKs, sequências, schema de origem, módulos não inicializados e exclusões.
- `schema/documentos.json`: cada referência binária, linha/coluna, tamanho e SHA-256.

Leitura SQLite em transação e PostgreSQL em REPEATABLE READ READ ONLY, limitada ao schema exclusivo da aplicação. PostgreSQL usa cursor nomeado para não carregar uma tabela inteira de anexos no cliente. JSONL e CSV são gravados incrementalmente em disco temporário. Um documento individual ainda precisa caber em memória; metadados e listas de hashes crescem com a quantidade de itens.

`schema_migrations` é exportada no PostgreSQL. `sqlite_sequence` é transportada como metadado, incluindo high-water marks. `backup_snapshots` é excluída com motivo explícito: contém snapshots nativos da rotina administrativa de exclusão. **Preservá-la em pg_dump/snapshot nativo independente.** Essa rotina e seus gatilhos não foram modificados.

Arquivos externos apontados por `exportacoes`, `audit_arquivos`, caminhos de quarentena e backups de exclusão SQLite não são varridos nem incorporados indiscriminadamente. Os registros de histórico são exportados; os arquivos de disco precisam de cópia externa separada das pastas autorizadas. Credenciais, Secrets, OAuth, código, templates e infraestrutura também não são incluídos. Dados institucionais inseridos nas tabelas continuam sendo dados: este recurso não promete remover segredos que alguém tenha gravado indevidamente em um campo operacional.

## Administração → Sistema → Backup

1. **Gerar backup**: geração explícita, progresso, resumo, integridade conferida e download. O evento BACKUP_GERADO ocorre depois do snapshot e não aparece dentro dele. A auditoria original registra também falhas e disponibilização.
2. **Validar e restaurar**: upload seguido de botão explícito. Verifica ZIP/manifesto/componentes/linhas/documentos e, para SQLite compatível, reconstrói uma base temporária descartável. Não escreve no banco operacional. Relatório disponível para download; gravar o resultado na Auditoria é uma ação administrativa separada e explícita. O executor offline registra eventos no seu log externo.
3. **Proteção e histórico**: estado externo e recibo de última transferência verificada, alerta de atraso e consulta explícita aos últimos eventos de geração/validação/restauração. Nenhuma consulta pesada é acionada por simples abertura ou rerun.

Para manter uma cópia de teste, o operador de infraestrutura deve configurar `MPC_BACKUP_RESTORE_TEST_ROOT` para uma pasta **já existente**, protegida e aprovada. O administrador deve marcar a confirmação e digitar `RESTAURAR EM SQLITE ISOLADO`. O serviço só cria um subdiretório aleatório novo. Não recebe URL, schema nem arquivo de destino para substituir. Sem configuração, a reconstrução da validação é descartada.

Permissões administrativas são exigidas no serviço e na interface. Proteja a pasta de testes, restrinja acesso e elimine cópias de ensaio segundo a política institucional. Não execute integrações/e-mails em bases restauradas: os estados pendentes e destinatários são preservados e poderiam ser reprocessados.

## Validação e recuperação isolada

Resultados possíveis:

- **Válido para restauração**: pacote íntegro e reconstrução SQLite com schema instalado compatível, restrições, contagens, comparação criptográfica de todos os valores/documentos, FKs e reabertura aprovadas.
- **Incompatível**: versão/schema/engine inadequados ao destino, ou reconstrução ainda não executada. O campo `integrity_ok` distingue pacote conferido de compatibilidade não comprovada. A geração verifica integridade sem repetir reconstrução; não anuncia teste de recuperação concluído.
- **Inválido**: ZIP, manifesto, componentes, contagens, referências ou reconstrução falharam.
- **Legado não certificado**: formato V1; metadados disponíveis, restauração automática bloqueada.

O schema SQLite é construído a partir do código instalado em um banco temporário de confiança. SQL importado nunca é executado. INSERTs são parametrizados, tabelas são autorizadas, colunas são comparadas com o schema confiável. Escrita numa transação, constraints e foreign_key_check antes do commit; reabertura somente leitura após commit. Falha remove apenas o diretório novo criado pelo serviço. Bancos preexistentes nunca são apagados ou substituídos.

Limites defensivos: ZIP até 2 GiB, soma descompactada até 4 GiB, componente até 256 MiB, metadados até 32 MiB, linha JSONL até 16 MiB, até 100.000 entradas, taxa máxima de expansão 2.000. Duplicatas, traversal, links simbólicos, caminhos absolutos, arquivos inesperados e ZIP criptografado são recusados. Bases maiores exigem revisão técnica e backup nativo; limites não devem ser desligados para aceitar arquivo suspeito. Execute o validador externo em ambiente sem rede e com limites de CPU/memória quando receber pacotes não confiáveis.

SHA-256 detecta corrupção, **não autentica origem**: quem altera o arquivo pode recalcular hashes. Não há assinatura implementada. Mantenha cadeia de custódia e controle de acesso; se assinatura institucional for exigida, adote assinatura destacada com ferramenta/provedor aprovado e chave protegida, verificando-a antes da importação. Não coloque chave de assinatura no ZIP.

## Executor independente do portal

Instale a mesma versão do código e dependências em máquina institucional. Não é necessário iniciar Streamlit ou acessar o banco original para validar/restaurar. Exemplos (substituir caminhos, manter tudo fora do Git e proteger com ACL):

```powershell
# Backup de SQLite existente: o executor usa mode=ro e não chama Store.initialize.
python -m scripts.backup_admin --audit-log D:/MPCBackup/runner.jsonl generate --sqlite D:/MPCDados/mpc.db --actor administrador@instituicao --output D:/MPCBackup/backup-unico.zip

# Backup PostgreSQL: MPC_BACKUP_DATABASE_URL é fornecida pelo cofre/ambiente do executor.
# Usuário SELECT-only, schema explícito exclusivo e transporte TLS.
python -m scripts.backup_admin --audit-log D:/MPCBackup/runner.jsonl generate --postgres-schema mpc_portarias --actor administrador@instituicao --output D:/MPCBackup/backup-unico.zip --external-config D:/MPCBackup/external.json

python -m scripts.backup_admin --audit-log D:/MPCBackup/recovery.jsonl validate D:/MPCBackup/backup.zip --authorization D:/MPCBackup/recovery-authorization.json
python -m scripts.backup_admin --audit-log D:/MPCBackup/recovery.jsonl restore D:/MPCBackup/backup.zip --authorization D:/MPCBackup/recovery-authorization.json --workspace D:/MPCRecovery --confirm "RESTAURAR EM SQLITE ISOLADO"
python -m scripts.backup_admin --audit-log D:/MPCBackup/monitor.jsonl status --external-config D:/MPCBackup/external.json
```

O gerador exige administrador ativo cadastrado na origem. Nenhuma migração é executada; esquema incompleto provoca recusa. O executor não escreve auditoria na origem SELECT-only: grava log local com início, conclusão/falha e hash, sem dados/credenciais.

Para operação offline, o responsável autoriza via arquivo protegido por ACL, por exemplo:

```json
{"actor":"operador@instituicao","approval_reference":"PROCESSO-ADMINISTRATIVO-APROVADO","scope":"isolated-sqlite-recovery","expires_at":"2026-12-31T23:59:59+00:00"}
```

Esse arquivo é uma autorização operacional confiada ao sistema operacional, não assinatura nem autenticação remota. Em POSIX, o executor recusa escrita para grupo/outros; em Windows, configurar ACL restrita a administradores e conta do executor. Recupere essa autorização separadamente do banco. Seu escopo permite apenas teste isolado e nunca substituição de produção.

Códigos de saída: 0 concluído; 1 falha/recusa; 2 atenção, atraso ou validação não certificada. Preserve os relatórios fora do host da aplicação. Logs e recibos locais devem estar em volume durável, com cópia independente e controle de acesso.

## Armazenamento externo e recorrência (inativos por padrão)

O adaptador implementado é AWS S3 via `boto3`, dependência opcional **somente no executor externo**. Não foi instalado nem conectado ao provedor. Não suporta endpoint HTTP arbitrário. Usa cadeia padrão de credenciais, TLS, SSE-KMS, versionamento, Object Lock configurado, nome único, criação condicional e retenção configurável. O modo COMPLIANCE exige referência institucional específica; o exemplo usa GOVERNANCE. Lê a versão gravada de volta e compara tamanho/SHA-256, criptografia e retenção antes de registrar sucesso.

Exemplo de configuração sem credenciais:

```json
{
  "provider":"s3",
  "approved_by":"responsavel-institucional",
  "approval_reference":"PROCESSO-ADMINISTRATIVO-APROVADO",
  "region":"sa-east-1",
  "bucket":"BUCKET-PRIVADO-APROVADO",
  "prefix":"mpcpb/logical-v2",
  "kms_key_id":"ARN-DA-CHAVE-INSTITUCIONAL",
  "object_lock_mode":"GOVERNANCE",
  "retention_days":90,
  "max_age_hours":26,
  "receipt_path":"D:/MPCBackup/last-verified.json",
  "monitor_path":"D:/MPCBackup/last-remote-check.json"
}
```

Configurar `MPC_BACKUP_EXTERNAL_CONFIG` no portal apenas se ele tiver acesso protegido à configuração e ao recibo atualizado pelo executor; caso contrário, o portal exibirá não configurado. O comando aceita `--external-config`. Nunca guardar backups, autorizações ou credenciais no GitHub.

O monitor remoto deve executar, em conta institucional autorizada, sem abrir Streamlit:

```powershell
python -m scripts.backup_admin --audit-log D:/MPCBackup/monitor.jsonl verify --external-config D:/MPCBackup/external.json
python -m scripts.backup_admin --audit-log D:/MPCBackup/monitor.jsonl status --external-config D:/MPCBackup/external.json
```

O backup diário usa arquivo temporário único, lock atômico e retentativas somente antes da publicação. Uma falha após iniciar publicação não é repetida automaticamente, para não duplicar objetos quando a resposta do provedor for incerta:

```powershell
python -m scripts.backup_admin --audit-log D:/MPCBackup/runner.jsonl scheduled --postgres-schema mpc_portarias --actor administrador@instituicao --work-dir D:/MPCBackup/staging --lock-file D:/MPCBackup/runner.lock --external-config D:/MPCBackup/external.json --prepublish-attempts 2
```

No Agendador de Tarefas, executar esse comando uma vez por dia sob uma conta de serviço restrita e configurar também `verify` a cada hora. Não habilitar “executar instâncias em paralelo”. Em systemd, use o mesmo comando em um serviço `Type=oneshot` e um timer diário; o lock permanece obrigatório mesmo quando o agendador oferece proteção própria.

Para baixar a versão fixada no recibo sem o portal, use autorização local com escopo `external-backup-download` e um ZIP de destino inexistente. O executor confere hash/tamanho, valida V2 e nunca restaura produção:

```powershell
python -m scripts.backup_admin --audit-log D:/MPCBackup/recovery.jsonl download --external-config D:/MPCBackup/external.json --authorization D:/MPCBackup/download-authorization.json --destination D:/MPCRecovery/backup.zip
```
A equipe de infraestrutura deve, antes da ativação:

1. Aprovar orçamento, região, bucket privado com bloqueio de acesso público, KMS e Object Lock/versionamento. Nada disso é provisionado pelo código.
2. Conceder à conta do executor somente leitura do banco e, no prefixo aprovado, PutObject, GetObject/GetObjectVersion, PutObjectRetention, GetObjectRetention, consulta de versionamento/Object Lock e operações KMS necessárias (GenerateDataKey/Decrypt). Não conceder DeleteObject, mudança de policy/lifecycle, bypass de retenção ou administração da chave. Restringir bucket policy a TLS, KMS esperado e acesso institucional.
3. Configurar lifecycle institucional para remoção de versões somente após retenção, incluindo versões não correntes. O adaptador não exclui objetos. COMPLIANCE impede redução da retenção; testar com aprovação de custos antes de ativar.
4. Instalar `boto3` em versão que suporte os parâmetros utilizados e testar em bucket segregado aprovado. Credenciais devem vir de role/cofre, sem chaves no arquivo de configuração.
5. Agendar o comando `generate` em Agendador de Tarefas/systemd/serviço institucional **fora do Streamlit**, com nome de saída único (data UTC + UUID), conta restrita, sem execuções sobrepostas, diretório criptografado e limites de tempo/armazenamento. Não há agendamento ativado neste trabalho.
6. Executar `status` em monitor independente a cada hora; alertar se saída diferente de 0, se o job falhar ou se não houver recibo novo no prazo. O último recibo válido não é atualizado em falhas. Alertas dependem da integração institucional de monitoramento ainda não configurada.
7. Copiar o recibo para o canal de monitoramento confiável. Ele descreve verificação passada e não comprova disponibilidade remota atual; testar download e restauração periodicamente. A perda do executor não pode eliminar as instruções de acesso ao bucket/KMS.
8. Definir política para remover ZIPs temporários locais após armazenamento comprovado, sem apagar a única cópia válida. O código não aplica exclusão automática de arquivos institucionais.

## Recuperação definitiva PostgreSQL/Supabase — execução bloqueada no portal

Não há comando de substituir/truncar produção nesta implementação. O ZIP V2 PostgreSQL não é certificado para reconstrução automática: tipos próprios, gatilhos e sequências não transacionais precisam de ensaio real no mesmo engine. Valores de sequências são registrados para diagnóstico, mas sua consistência não é garantida por REPEATABLE READ. Não converter esse ZIP para SQLite e anunciar recuperação integral.

Procedimento institucional necessário:

1. Abrir incidente e autorização formal, identificar inequivocamente projeto/schema de origem e destino, responsáveis e janela. Definir RPO/RTO com base nas cópias comprovadas, sem prometer valores não medidos.
2. Suspender coordenadamente escritas do portal, jobs, integrações e envios; impedir novos acessos de escrita. Confirmar a suspensão.
3. Preservar backup prévio nativo íntegro, separado e legível (pg_dump ou recuperação do provedor), incluindo backup_snapshots, sequências, constraints, funções/triggers e permissões. Preservar documentos de disco/quarentena em cópia separada.
4. Restaurar primeiro em projeto/schema PostgreSQL **realmente segregado**, com credenciais distintas, sem integrações de saída e sem acesso de escrita à produção. Usar ferramentas nativas confiáveis e procedimento aprovado, nunca SQL recebido de ZIP não autenticado.
5. Conferir schema, contagens, FKs, todos os hashes documentais, históricos de exclusão, permissões e avanços de sequências/identidades; abrir registros em serviços representativos e testar nova numeração sem colisão. Registrar relatório e hashes.
6. Ensaiar reversão. Preferir substituição controlada por ambiente paralelo validado, mantendo o original preservado e read-only. Cutover, cache, conexões e configurações devem ser coordenados fora do fluxo normal do portal. Não executar DROP/DELETE/TRUNCATE como improviso.
7. Obter confirmação específica do destino e do relatório, executar a troca aprovada, validar novamente e só então liberar escritas/envios. Registrar responsáveis, horários, evidências, aprovação e ponto de reversão.
8. Se atomicidade, bloqueio de escritores, backup prévio ou rollback não estiverem demonstrados, **não prosseguir**. A restauração definitiva permanece bloqueada.

## Indisponibilidade grave e dependências

- **Supabase/PostgreSQL:** recuperar banco nativo, conta/projeto, roles, RLS/policies aplicáveis, extensões e chaves/conectividade segundo procedimento do provedor. Schemas auth/storage e objetos alheios ao aplicativo não entram no ZIP.
- **GitHub:** recuperar código e versão compatível, templates/assets e instruções. Repositório não é destino de backup de dados; mantenha cópia institucional do release.
- **Streamlit Cloud:** recompor deploy, versão Python/dependências, Secrets, conexão do banco e configurações. Disco efêmero não conserva backups.
- **Google/OIDC e integrações:** recompor clientes, redirects, tokens e credenciais separadamente em cofre aprovado. O backup não altera nem exporta configuração de login.
- **S3/KMS:** acesso de recuperação e chave devem sobreviver à perda da aplicação. SSE-KMS depende da disponibilidade da chave; não agendar sua exclusão enquanto houver backups retidos.
- **SQLite:** restaurar isoladamente com o comando offline; testar e autorizar a troca de arquivo apenas com aplicação parada e plano de reversão externo. Nenhum comando deste projeto efetua essa substituição.

## Ensaio periódico e evidências

Em periodicidade institucional (sugestão inicial: mensal e após mudança de schema): obter cópia externa de uma versão específica, conferir cadeia de custódia/hash, validar, restaurar em ambiente segregado, conferir dados/documentos/IDs/numeração e testar leitura funcional. Simular falha e perda do host original; medir duração/RPO real. Registrar resultado, data, versão, responsável e limitações. Um download sem teste de recuperação não encerra o ensaio.

Testes de desenvolvimento usam somente fixtures sintéticas. O conjunto direcionado inclui inventário contra CREATE TABLE, descoberta de tabela não inventariada, ida e volta de anexos dos sete grupos, NULL/texto vazio/JSON/tipos, contagens/IDs/FKs/sequências, ZIP adulterado/traversal/duplicatas, incompatibilidade, rollback de falha, autorização, bloqueio de banco existente/URL PostgreSQL, runner offline, armazenamento externo simulado e reruns administrativos. PostgreSQL real e S3 real dependem de ambientes segregados configurados e **não devem ser declarados testados por simulações**.


Referências oficiais do adaptador: [PutObject (Boto3)](https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/put_object.html), [S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html) e [permissões e limites de Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock-managing.html). A documentação de operação de Saúde permanece aplicável: o diagnóstico é somente leitura e não corrige schema; ausências requerem análise pelos responsáveis.


## Saúde

Página operacional, não um painel técnico. Indicadores **OK**, **ATENÇÃO** e **ERRO** aparecem em texto (e também com ênfase visual).

| Verificação | O que responde |
|---|---|
| Banco de dados | Conexão (`SELECT 1`), engine (PostgreSQL ou SQLite), ambiente (local / Streamlit Cloud, quando identificável com segurança), latência aproximada. Sem `DATABASE_URL`, host completo, usuário, senha ou connection string. |
| Schema | Marcadores em `configuracoes`, `user_version` (SQLite) ou `schema_migrations` (PostgreSQL), tabelas essenciais. **Não executa migration** ao abrir a página. Ausência de tabela/coluna: **ATENÇÃO** e o nome do componente (ex.: `auditoria_eventos`). |
| Auditoria | Tabela `auditoria_eventos`, último evento, volume em 24 h, erros operacionais. Sem eventos: **ATENÇÃO**, não falha grave. |
| Documentos | Disponibilidade leve de geração DOCX e do conversor PDF já usado pelo projeto (LibreOffice / Word). Não gera arquivo ao abrir a tela. Conversor ausente: **ATENÇÃO**. |
| Aplicação | Python, Streamlit, versão do app, build Git (8 caracteres) se disponível. Ausência de Git **não** é erro. Horário em America/Recife. |
| Atividade | Último acesso, último documento finalizado, última alteração administrativa e última atividade — a partir da auditoria. |
| Erros recentes | Resumo de `ERRO_OPERACIONAL` (24 h / 7 dias), com atalho para Acessos e Auditoria. |

O diagnóstico inicial roda ao abrir **Saúde**. Não se repete a cada rerun: use **Atualizar diagnóstico**. Consultas são leves e só ocorrem com a seção aberta.

### Tabelas essenciais

O inventário em `database/inventory.py` replica os nomes criados em `database/store.py`, `postgresql_schema.sql`, access, audit, memorandos, agenda e ofícios. Agenda e Ofícios são aditivos: se o módulo nunca foi aberto, a Saúde pode apontar **ATENÇÃO** até as tabelas existirem.

## Evidência de validação desta implementação — 09/10/2026

Runtime conferido: **Streamlit 1.59.2**, usando a instalação isolada em `tmp/sidebar-runtime-1592`, sem modificar requirements ou autenticação.

- `pytest tests/test_backup.py tests/test_backup_v2.py tests/test_backup_external.py tests/test_system_health.py tests/test_deletion.py --basetemp=tmp/backup-v2-acceptance -q`: **100 passed, 2 skipped**. Os dois skips são integração PostgreSQL, por ausência de ambiente de teste configurado. Um aviso de ZIP duplicado é produzido deliberadamente pela fixture maliciosa.
- Depois do ajuste para separar o registro da Auditoria da pré-visualização, `pytest tests/test_backup_v2.py -k preview_does_not_write --basetemp=tmp/backup-v2-preview-final -q`: **1 passed, 35 deselected**. Confere validação sem escrita operacional e registro apenas após ação explícita.
- Teste completo SQLite gerou V2, validou, restaurou em diretório novo, comparou todas as tabelas incluídas e bytes dos anexos, conferiu FKs, controles de numeração, IDs e nova alocação de ID. Leitura posterior por Portarias, Representações e relatórios institucionais aprovada. O próprio serviço registra amostras de leitura funcional, indicando quando não há registros disponíveis.
- Testes do executor usam origem somente leitura, autorizam recuperação offline e recusam caminhos de bancos existentes. Falha simulada não publica banco recuperado; adulteração, arquivos ausentes, binário substituído por texto, traversal e duplicatas são recusados.
- Adaptador externo testado com cliente **simulado**, incluindo leitura de volta, corrupção, ausência de lock e recibo somente após sucesso. Nenhum upload real foi executado.
- Inventário atual: **73 tabelas declaradas**, com diferenças por engine, exclusões técnicas e módulos aditivos explicitadas. Este número não é uma contagem do schema de produção, que não foi consultado.

A capacidade integral comprovada é a ida e volta **SQLite sintética**. Não há certificação de recuperação PostgreSQL, ensaio real de S3, entrega de alertas, agendamento ativado ou restauração de produção. Arquivos externos, snapshots administrativos nativos, credenciais e infraestrutura continuam exigindo os procedimentos separados descritos acima.
