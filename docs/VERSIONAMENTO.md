# Versionamento

## Padrão

O Ferramentas MPC-PB utiliza versionamento semântico no formato
`MAJOR.MINOR.PATCH`.

## Exemplos

- Correção de erro: `1.0.0 → 1.0.1`
- Nova funcionalidade: `1.0.1 → 1.1.0`
- Alteração estrutural incompatível ou nova geração: `1.8.3 → 2.0.0`

## Procedimento para nova versão

1. Implementar a alteração.
2. Executar os testes.
3. Validar o funcionamento.
4. Atualizar a constante oficial em `services/versioning.py`.
5. Atualizar `CHANGELOG.md`.
6. Realizar o commit.
7. Criar a tag Git correspondente à versão.
8. Fazer push da branch e da tag.

Exemplo de tag: `v1.0.0`.

Após a validação deste baseline, a versão atual poderá receber a tag
`v1.0.0`, apontando exatamente para o commit que formalizar esta implantação.

Versão oficial do produto e ponto de recuperação Git são conceitos diferentes.
Uma versão oficial identifica uma entrega do sistema. Branches, commits e tags
de recuperação podem existir para segurança operacional independentemente da
numeração pública da versão.
