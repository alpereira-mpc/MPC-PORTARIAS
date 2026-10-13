# Minutas IA — implantação da Fase 2A

## Ordem de implantação

1. Confirme no painel do Supabase que está no projeto usado pelo `DATABASE_URL`
   de produção.
2. Abra o SQL Editor, revise e execute `database/minutas_referencias.sql`.
3. Em Storage, confirme que o bucket `minutas-referencias` existe e está marcado
   como privado. Se o bucket já existia, o script não altera sua configuração.
4. Em Project Settings > API Keys, copie a URL do projeto e uma Secret Key. Em
   projetos que ainda não tenham as novas chaves, a chave `service_role` legada é
   aceita. Nunca use chave `anon` ou publishable nesta integração.
5. No Streamlit Cloud, abra App > Settings > Secrets e mantenha a configuração da
   Fase 1, acrescentando as chaves abaixo:

   ```toml
   MINUTAS_PILOTO_ENABLED = true
   MINUTAS_PILOTO_EMAILS = ["SEU_EMAIL_INSTITUCIONAL_EXATO"]
   SUPABASE_URL = "https://SEU_PROJECT_REF.supabase.co"
   SUPABASE_SECRET_KEY = "SUA_SECRET_KEY"
   MINUTAS_STORAGE_BUCKET = "minutas-referencias"
   ```

6. Salve os Secrets e reinicie o aplicativo.
7. Entre com o e-mail autorizado, abra Minutas IA e confirme as abas Biblioteca de
   Referências e Gerar Minuta.

O aplicativo não cria tabela, bucket ou policies automaticamente. A Secret Key
permanece somente nos Secrets e nunca deve ser exposta ao navegador, ao Git ou a
logs. Como alternativa temporária para projetos legados, use
`SUPABASE_SERVICE_ROLE_KEY` no lugar de `SUPABASE_SECRET_KEY`.

## Cadastro dos quatro processos iniciais

Cadastre um processo por vez na aba Biblioteca de Referências:

1. Confirme a Procuradora Elvira Samara.
2. Informe o número do processo e o exercício.
3. Selecione um dos subtipos: Prefeitura, Câmara Municipal,
   Instituto/Autarquia ou Secretaria Municipal ou Estadual.
4. Anexe o Relatório da Auditoria em PDF.
5. Anexe o parecer definitivo em PDF ou DOCX.
6. Selecione Cadastrar referência e aguarde a confirmação antes de iniciar o
   cadastro seguinte.

Repita os passos para os quatro processos. O mesmo número de processo não pode ser
cadastrado duas vezes para a mesma Procuradora, independentemente de pontuação,
espaços ou diferença entre maiúsculas e minúsculas.

## Verificação posterior

- Consulte cada cartão e prepare o download dos dois documentos.
- Compare os arquivos baixados com os originais.
- Substitua um arquivo de teste e confirme que o outro não foi alterado.
- Exclua somente um registro de teste, marcando a confirmação explícita.
- Revise no Supabase se os objetos ficam exclusivamente no bucket privado.
